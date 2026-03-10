"""PlutoSDR modem — ADALM-Pluto SDR with software LoRa PHY."""

import threading
import time
from collections import deque

import numpy as np

from lora.mod import modulate
from lora.demod import LoRaParams, demodulate

from modem.base import LoRaModem, RxPacket, MAX_TTL

# --- SDR config ---
FREQ_HZ     = 915_000_000
SAMPLE_RATE = 1_000_000
TX_BW       = 1_000_000
RX_BW       = 1_000_000
TX_ATTEN    = -10
RX_GAIN     = 73
BUF_SIZE    = 2**16        # ~65ms per read at 1 MSPS

# RX LO offset: the Pluto's AD9363 transceiver corrupts TX when
# rx_lo == tx_lo and RX has been active.  Offsetting the RX LO by
# 100 kHz avoids the issue.  We compensate in software by
# frequency-shifting the captured IQ before demodulation.
RX_LO_OFFSET = 100_000    # Hz

# --- Protocol ---
DEDUP_RING_SIZE = 16

# --- LoRa params (must match all other components) ---
LORA_PARAMS = LoRaParams(sf=7, bw=125e3, cr=1, fs=1e6)

# --- TX scaling (match transmit.py: unit-amplitude complex64 -> 2**14) ---
TX_SCALE = 2**14

# --- RX capture ---
# Read continuously, hand off chunks to a demod thread.
# A LoRa packet at SF7/BW125 is ~92ms. We demod windows of ~2s
# to allow comfortable preamble + packet overlap.
DEMOD_WINDOW_READS = 30   # ~2s of IQ at 65ms/read


class PlutoModem(LoRaModem):
    """LoRa modem using ADALM-Pluto SDR with software modulation/demodulation.

    Two threads:
    - Reader thread: calls sdr.rx() continuously, queues chunks, handles TX
    - Demod thread: consumes queued IQ, runs demodulator, delivers packets

    Only the reader thread touches the sdr object (no thread-safety issues).
    """

    def __init__(self, uri: str = "usb:"):
        self._uri = uri
        self._sdr = None
        self._rx_cb = None
        self._running = False
        self._reader_thread: threading.Thread | None = None
        self._demod_thread: threading.Thread | None = None
        self._tx_queue: list[np.ndarray] = []
        self._tx_lock = threading.Lock()
        # IQ chunks queue: reader pushes, demod pops
        self._iq_queue: deque[np.ndarray] = deque()
        self._iq_event = threading.Event()
        self._dedup_ring: list[int] = []

    def send(self, ttl: int, dedup: int, payload: bytes) -> None:
        if self._sdr is None:
            return
        try:
            air_packet = bytes([ttl, dedup >> 8, dedup & 0xFF]) + payload
            iq = modulate(air_packet, LORA_PARAMS)
            iq_tx = (iq * TX_SCALE).astype(np.complex64)
            with self._tx_lock:
                self._tx_queue.append(iq_tx)
            self._dedup_add(dedup)
        except Exception as exc:
            self._emit_status(f"TX error: {exc}")

    def set_receive_callback(self, cb):
        self._rx_cb = cb

    def start(self) -> None:
        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._demod_thread = threading.Thread(target=self._demod_loop, daemon=True)
        self._reader_thread.start()
        self._demod_thread.start()

    def stop(self) -> None:
        self._running = False
        self._iq_event.set()  # wake demod thread
        sdr = self._sdr
        self._sdr = None
        if sdr is not None:
            try:
                sdr.tx_destroy_buffer()
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        return self._sdr is not None

    def _connect(self) -> bool:
        """Initialise the Pluto SDR."""
        try:
            import adi
            sdr = adi.Pluto(self._uri)

            sdr.sample_rate              = SAMPLE_RATE
            sdr.tx_lo                    = FREQ_HZ
            sdr.tx_rf_bandwidth          = TX_BW
            sdr.tx_hardwaregain_chan0     = TX_ATTEN
            sdr.tx_cyclic_buffer         = False
            # RX config (LO offset to avoid TX corruption — see RX_LO_OFFSET)
            sdr.rx_lo                    = FREQ_HZ + RX_LO_OFFSET
            sdr.rx_rf_bandwidth          = RX_BW
            sdr.gain_control_mode_chan0   = "manual"
            sdr.rx_hardwaregain_chan0     = RX_GAIN
            sdr.rx_buffer_size           = BUF_SIZE

            for _ in range(5):
                sdr.rx()

            self._sdr = sdr
            return True
        except Exception:
            self._sdr = None
            return False

    def _reader_loop(self) -> None:
        """Read IQ from SDR, handle TX, push chunks to demod queue."""
        first_connect = True
        read_count = 0

        while self._running:
            if self._sdr is None:
                if not first_connect:
                    time.sleep(2)
                if self._connect():
                    label = "connected" if first_connect else "reconnected"
                    self._emit_status(f"{label} on {self._uri}")
                    read_count = 0
                first_connect = False
                if self._sdr is None:
                    continue

            # --- Handle TX (between reads, same thread as sdr) ---
            with self._tx_lock:
                tx_items = list(self._tx_queue)
                self._tx_queue.clear()

            for iq_tx in tx_items:
                try:
                    self._sdr.tx(iq_tx)
                    # Wait for DMA to push the full waveform.
                    # Generous margin: waveform duration + 200ms.
                    dur = len(iq_tx) / SAMPLE_RATE + 0.2
                    time.sleep(dur)
                    self._sdr.tx_destroy_buffer()
                except Exception as exc:
                    self._emit_status(f"TX error: {exc}")

            # --- Read one IQ chunk ---
            try:
                chunk = self._sdr.rx().astype(np.complex64)
            except Exception:
                self._sdr = None
                self._emit_status("disconnected")
                continue

            self._iq_queue.append(chunk)
            read_count += 1

            # Signal demod thread every DEMOD_WINDOW_READS chunks
            if read_count >= DEMOD_WINDOW_READS:
                self._iq_event.set()
                read_count = 0

    def _demod_loop(self) -> None:
        """Consume IQ chunks, run demodulator, deliver packets."""
        while self._running:
            self._iq_event.wait(timeout=2.0)
            self._iq_event.clear()

            if not self._running:
                break

            # Drain all available chunks
            chunks = []
            while self._iq_queue:
                try:
                    chunks.append(self._iq_queue.popleft())
                except IndexError:
                    break

            if not chunks:
                continue

            iq = np.concatenate(chunks)

            # Compensate for RX LO offset: shift captured IQ back to
            # baseband so the demodulator sees the signal at DC.
            if RX_LO_OFFSET != 0:
                t = np.arange(len(iq), dtype=np.float64) / SAMPLE_RATE
                iq = iq * np.exp(2j * np.pi * RX_LO_OFFSET * t).astype(np.complex64)

            try:
                results = demodulate(iq, LORA_PARAMS, verbose=False)
            except Exception:
                continue

            for r in results:
                if not r.get("crc_ok", False):
                    continue
                pkt = self._parse_air_packet(r.get("payload", b""))
                if pkt is None:
                    continue
                if self._dedup_check(pkt.dedup):
                    continue
                if self._rx_cb:
                    self._rx_cb(pkt)

    def _dedup_check(self, dedup: int) -> bool:
        """Return True if dedup token was recently seen (duplicate).

        Keyed on the full 16-bit dedup token (UID + SEQ).
        """
        if dedup in self._dedup_ring:
            return True
        self._dedup_ring.append(dedup)
        if len(self._dedup_ring) > DEDUP_RING_SIZE:
            self._dedup_ring.pop(0)
        return False

    def _dedup_add(self, dedup: int) -> None:
        """Add a dedup token to the ring (used on TX to avoid self-echo)."""
        if dedup not in self._dedup_ring:
            self._dedup_ring.append(dedup)
            if len(self._dedup_ring) > DEDUP_RING_SIZE:
                self._dedup_ring.pop(0)

    @staticmethod
    def _parse_air_packet(payload_bytes: bytes) -> RxPacket | None:
        """Parse over-the-air format [TTL][DEDUP_HI][DEDUP_LO][app_payload...] into RxPacket."""
        if len(payload_bytes) < 4:
            return None
        ttl = payload_bytes[0]
        dedup = (payload_bytes[1] << 8) | payload_bytes[2]
        if ttl > MAX_TTL:
            return None
        app_payload = payload_bytes[3:]
        return RxPacket(ttl=ttl, dedup=dedup, payload=app_payload)
