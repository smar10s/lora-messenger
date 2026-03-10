"""RAK11300 modem — serial binary framing over USB."""

import struct
import threading
import time

import serial
from serial import SerialException

from modem.base import LoRaModem, RxPacket, MAX_TTL


def parse_rx_frame(data: bytes) -> RxPacket:
    """Parse a binary RX frame from firmware.

    Format: [TTL][DEDUP_HI][DEDUP_LO][RSSI_lo][RSSI_hi][SNR][payload...]
    """
    ttl = data[0]
    dedup = (data[1] << 8) | data[2]
    rssi = struct.unpack_from("<h", data, 3)[0]
    snr = struct.unpack_from("<b", data, 5)[0]
    payload = data[6:]
    return RxPacket(ttl=ttl, dedup=dedup, payload=payload, rssi=rssi, snr=snr)


def build_tx_frame(ttl: int, dedup: int, payload: bytes) -> bytes:
    """Build a binary TX frame for firmware.

    Format: [LEN][TTL][DEDUP_HI][DEDUP_LO][payload...]
    """
    if len(payload) > 252:
        raise ValueError(f"payload too large: {len(payload)} bytes (max 252)")
    body = bytes([ttl, dedup >> 8, dedup & 0xFF]) + payload
    return bytes([len(body)]) + body


class RAKModem(LoRaModem):
    """LoRa modem using RAK11300 over USB serial with binary framing."""

    def __init__(self, port: str, baud: int = 115200):
        self._port = port
        self._baud = baud
        self._ser: serial.Serial | None = None
        self._rx_cb = None
        self._running = False
        self._reader_thread: threading.Thread | None = None

    def send(self, ttl: int, dedup: int, payload: bytes) -> None:
        if self._ser is None:
            return
        try:
            frame = build_tx_frame(ttl, dedup, payload)
            self._ser.write(frame)
        except SerialException:
            self._ser = None
            self._emit_status("disconnected")

    def set_receive_callback(self, cb):
        self._rx_cb = cb

    def start(self) -> None:
        self._running = True
        # Don't connect here — the reader thread handles connect/reconnect.
        # This avoids calling _emit_status from the main thread where
        # call_from_thread is invalid.
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    @property
    def connected(self) -> bool:
        return self._ser is not None

    @property
    def port(self) -> str:
        return self._port

    def _connect(self) -> bool:
        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=1)
            self._emit_status(f"connected on {self._port}")
            return True
        except SerialException:
            self._ser = None
            return False

    def _reader_loop(self) -> None:
        first_connect = True
        while self._running:
            if self._ser is None:
                if not first_connect:
                    time.sleep(2)
                try:
                    self._ser = serial.Serial(self._port, self._baud, timeout=1)
                    label = "connected" if first_connect else "reconnected"
                    self._emit_status(f"{label} on {self._port}")
                    first_connect = False
                except SerialException:
                    first_connect = False
                    continue

            try:
                header = self._ser.read(1)
                if not header:
                    continue

                length = header[0]

                if length < 6:
                    continue

                frame = self._ser.read(length)
                if len(frame) < length:
                    continue

                # Skip text boot message (first byte would be > MAX_TTL for ASCII)
                if frame[0] > MAX_TTL:
                    continue

                pkt = parse_rx_frame(frame)
                if self._rx_cb:
                    self._rx_cb(pkt)

            except SerialException:
                self._ser = None
                self._emit_status("disconnected")
            except Exception:
                pass
