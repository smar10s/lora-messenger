"""PinePhone LoRa backplate modem — SX1262 over ATtiny84 I2C-SPI bridge."""

import threading
import time

from modem.base import LoRaModem, RxPacket, MAX_TTL

# ---------------------------------------------------------------------------
# ATtiny84 I2C-SPI bridge constants
# ---------------------------------------------------------------------------
I2C_BUS = 2
I2C_ADDR = 0x28
CMD_TRANSMIT = 0x01

# Max SPI payload per transfer (smbus2 block limit: 32 I2C bytes - 1 CMD byte)
MAX_SPI_BYTES = 31
# Max data bytes per WriteBuffer (31 - opcode - offset)
MAX_WRITE_CHUNK = 29
# Max data bytes per ReadBuffer (31 - opcode - offset - NOP)
MAX_READ_CHUNK = 28

# Timing — matches JF's driver
CMD_DELAY = 0.010     # 10ms pre-command (WaitOnBusy fake — BUSY pin unconnected)
POST_DELAY = 0.000126 # 126us post-command (WaitOnCounter)

# ---------------------------------------------------------------------------
# SX1262 constants
# ---------------------------------------------------------------------------
# LoRa radio config — must match RAK firmware
RF_FREQ_HZ = 915_000_000
LORA_SF = 7         # SF7
LORA_BW = 0x04      # 125 kHz
LORA_CR = 0x01      # 4/5
LORA_PREAMBLE = 8
LORA_MAX_PAYLOAD = 64

# IRQ flags
IRQ_TX_DONE = 0x0001
IRQ_RX_DONE = 0x0002
IRQ_CRC_ERROR = 0x0040

# Register addresses
REG_OCP = 0x08E7
REG_TX_MODULATION = 0x0889
REG_IQ_POLARITY = 0x0736

# Sync pattern for buffer alignment
SYNC_PATTERN = [0x10, 0x20, 0x30, 0x40, 0x50, 0xAA, 0x55, 0x00, 0xFF]

# Dedup ring (same as PlutoModem — air packets carry the relay header)
DEDUP_RING_SIZE = 16

# RX poll interval
RX_POLL_INTERVAL = 0.050  # 50ms


class PinePhoneModem(LoRaModem):
    """LoRa modem for the PinePhone LoRa backplate.

    Talks to an SX1262 radio through an ATtiny84 I2C-to-SPI bridge on
    /dev/i2c-2. The bridge has a 128-byte circular buffer that must be
    synchronized before use (JF's SyncI2CBuffer pattern).

    Architecture: single background thread polls for RX packets and
    handles TX requests. Only the background thread touches I2C.
    """

    def __init__(self, i2c_bus: int = I2C_BUS):
        self._i2c_bus = i2c_bus
        self._bus = None
        self._rx_cb = None
        self._running = False
        self._connected = False
        self._thread: threading.Thread | None = None
        self._tx_queue: list[tuple[int, int, bytes]] = []
        self._tx_lock = threading.Lock()
        self._dedup_ring: list[int] = []

    # ------------------------------------------------------------------
    # LoRaModem interface
    # ------------------------------------------------------------------

    def send(self, ttl: int, dedup: int, payload: bytes) -> None:
        if not self._connected:
            return
        with self._tx_lock:
            self._tx_queue.append((ttl, dedup, payload))

    def set_receive_callback(self, cb):
        self._rx_cb = cb

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # I2C-SPI bridge transport
    # ------------------------------------------------------------------

    def _i2c_write(self, data):
        """Write bytes to ATtiny. data[0] is the I2C command byte."""
        if len(data) > 32:
            raise ValueError(
                f"I2C write too large: {len(data)} bytes (max 32). "
                f"SPI payload must be <= {MAX_SPI_BYTES} bytes."
            )
        if len(data) < 2:
            self._bus.write_byte(I2C_ADDR, data[0])
        else:
            self._bus.write_i2c_block_data(I2C_ADDR, data[0], list(data[1:]))

    def _i2c_read_byte(self):
        """Read one byte from ATtiny circular buffer."""
        return self._bus.read_byte(I2C_ADDR)

    def _spi_command(self, data):
        """Send SPI command through bridge. Returns response bytes.

        Enforces the 31-byte SPI limit and proper timing. Always reads
        exactly len(data) response bytes — the bridge produces one response
        byte per SPI byte clocked. Reading more or fewer desyncs the buffer.
        """
        if len(data) > MAX_SPI_BYTES:
            raise ValueError(
                f"SPI command too large: {len(data)} bytes (max {MAX_SPI_BYTES}). "
                f"Split into multiple transfers for larger payloads."
            )
        time.sleep(CMD_DELAY)
        self._i2c_write([CMD_TRANSMIT] + list(data))
        time.sleep(POST_DELAY)
        return [self._i2c_read_byte() for _ in range(len(data))]

    def _sync_buffer(self):
        """Align ATtiny circular buffer pointers (JF's SyncI2CBuffer).

        Writes a known pattern to the SX1262 data buffer, issues a
        ReadBuffer, then scans I2C reads until the pattern is found.
        After this, every spi_command response is correctly framed.
        """
        self._i2c_write([CMD_TRANSMIT, 0x80, 0x00])       # SetStandby(RC)
        time.sleep(0.001)
        self._i2c_write([CMD_TRANSMIT, 0x8F, 0x00, 0x00]) # SetBufferBaseAddress
        time.sleep(0.001)
        self._i2c_write([CMD_TRANSMIT, 0x0E, 0x00] + SYNC_PATTERN)
        time.sleep(0.001)
        self._i2c_write([CMD_TRANSMIT, 0x1E, 0x00, 0x00] + [0x00] * len(SYNC_PATTERN))
        time.sleep(0.001)

        seq_started = False
        seq_index = 0
        for count in range(256):
            d = self._i2c_read_byte()
            if not seq_started:
                for i, v in enumerate(SYNC_PATTERN):
                    if d == v:
                        seq_started = True
                        seq_index = i
                        break
            else:
                if seq_index + 1 < len(SYNC_PATTERN) and d == SYNC_PATTERN[seq_index + 1]:
                    seq_index += 1
                    if seq_index == len(SYNC_PATTERN) - 1:
                        return True
                else:
                    seq_started = False
                    for i, v in enumerate(SYNC_PATTERN):
                        if d == v:
                            seq_started = True
                            seq_index = i
                            break
        return False

    # ------------------------------------------------------------------
    # SX1262 register/command helpers
    # ------------------------------------------------------------------

    def _read_register(self, addr):
        resp = self._spi_command([0x1D, (addr >> 8) & 0xFF, addr & 0xFF, 0x00, 0x00])
        return resp[-1]

    def _write_register(self, addr, val):
        self._spi_command([0x0D, (addr >> 8) & 0xFF, addr & 0xFF, val])

    def _get_status(self):
        """Returns (mode, cmd_status)."""
        resp = self._spi_command([0xC0, 0x00])
        return (resp[0] >> 4) & 0x07, (resp[0] >> 1) & 0x07

    def _get_irq(self):
        resp = self._spi_command([0x12, 0x00, 0x00, 0x00])
        return (resp[2] << 8) | resp[3]

    def _clear_irq(self):
        self._spi_command([0x02, 0xFF, 0xFF])

    def _get_rx_buffer_status(self):
        """Returns (payload_len, rx_start_offset)."""
        resp = self._spi_command([0x13, 0x00, 0x00, 0x00])
        return resp[2], resp[3]

    def _get_packet_status(self):
        """Returns (rssi, snr) for LoRa."""
        resp = self._spi_command([0x14, 0x00, 0x00, 0x00, 0x00])
        rssi = -(resp[2] // 2)
        snr_raw = resp[3] if resp[3] < 128 else resp[3] - 256
        return rssi, snr_raw // 4

    def _read_buffer(self, offset, size):
        """Read from SX1262 data buffer, chunked for smbus2 limit."""
        result = []
        pos = 0
        while pos < size:
            chunk = min(size - pos, MAX_READ_CHUNK)
            resp = self._spi_command([0x1E, offset + pos, 0x00] + [0x00] * chunk)
            result.extend(resp[3:])
            pos += chunk
        return bytes(result)

    def _write_buffer(self, offset, data):
        """Write to SX1262 data buffer, chunked for smbus2 limit."""
        pos = 0
        while pos < len(data):
            chunk = min(len(data) - pos, MAX_WRITE_CHUNK)
            self._spi_command([0x0E, offset + pos] + list(data[pos:pos + chunk]))
            pos += chunk

    def _set_rx(self):
        """Enter continuous RX mode."""
        self._spi_command([0x82, 0xFF, 0xFF, 0xFF])

    # ------------------------------------------------------------------
    # SX1262 init — follows JF's driver exactly
    # ------------------------------------------------------------------

    def _init_radio(self):
        """Full SX1262 init + LoRa configuration. Call after sync."""
        # Init (SX126x::Init: Reset -> Wakeup -> SetStandby -> SetPacketType)
        time.sleep(0.020)                  # fake reset (NRESET not accessible)
        self._spi_command([0xC0, 0x00])    # Wakeup (GetStatus)
        time.sleep(0.010)                  # WaitOnBusyLong
        self._spi_command([0x80, 0x00])    # SetStandby(STDBY_RC)
        self._spi_command([0x8A, 0x01])    # SetPacketType(LoRa)

        # Configure (PinedioLoraRadio::Initialize order)
        self._spi_command([0x9D, 0x01])    # SetDio2AsRfSwitchCtrl(true)
        self._spi_command([0x80, 0x00])    # SetStandby(RC) — again per JF
        self._spi_command([0x96, 0x01])    # SetRegulatorMode(USE_DCDC)
        self._spi_command([0x8F, 0x00, 0x7F])  # SetBufferBaseAddresses(tx=0, rx=127)

        # PA config (SX1262: dutyCycle=0x04, hpMax=0x07, deviceSel=0x00, paLut=0x01)
        self._spi_command([0x95, 0x04, 0x07, 0x00, 0x01])
        self._write_register(REG_OCP, 0x38)  # OCP 160mA
        # SetTxParams(power=+22dBm, ramp=RADIO_RAMP_3400_US=0x07)
        self._spi_command([0x8E, 0x16, 0x07])

        # IRQ: all flags in irqMask, DIO1 = 0x0001
        self._spi_command([0x08, 0xFF, 0xFF, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00])

        # Frequency: CalibrateImage first (902-928 MHz band)
        self._spi_command([0x98, 0xE1, 0xE9])
        time.sleep(0.010)
        freq_reg = int(RF_FREQ_HZ / (32e6 / (1 << 25)))
        self._spi_command([0x86, (freq_reg >> 24) & 0xFF, (freq_reg >> 16) & 0xFF,
                           (freq_reg >> 8) & 0xFF, freq_reg & 0xFF])

        self._spi_command([0x8A, 0x01])    # SetPacketType(LoRa) — again per JF
        self._spi_command([0x9F, 0x00])    # SetStopRxTimerOnPreambleDetect(false)

        # Modulation: SF7, BW125, CR4/5, no LDRO
        self._spi_command([0x8B, LORA_SF, LORA_BW, LORA_CR, 0x00])
        # BW != 500kHz workaround: set bit 2 of REG_TX_MODULATION
        txmod = self._read_register(REG_TX_MODULATION)
        self._write_register(REG_TX_MODULATION, txmod | (1 << 2))

        # Packet params: preamble=8, variable header, max payload, CRC on, normal IQ
        self._spi_command([0x8C, 0x00, LORA_PREAMBLE, 0x00, LORA_MAX_PAYLOAD, 0x01, 0x00])
        # IQ polarity workaround (normal IQ — set bit 2)
        iq_reg = self._read_register(REG_IQ_POLARITY)
        self._write_register(REG_IQ_POLARITY, iq_reg | (1 << 2))

        self._clear_irq()

    # ------------------------------------------------------------------
    # TX
    # ------------------------------------------------------------------

    def _transmit(self, ttl, dedup, payload):
        """Transmit one packet. Blocks until TxDone or timeout."""
        # Build air packet: [TTL][DEDUP_HI][DEDUP_LO][payload]
        air = bytes([ttl, (dedup >> 8) & 0xFF, dedup & 0xFF]) + payload
        pkt_len = len(air)

        # Update packet params for this payload length
        self._spi_command([0x8C, 0x00, LORA_PREAMBLE, 0x00, pkt_len, 0x01, 0x00])

        self._clear_irq()
        self._write_buffer(0x00, air)

        # SetTx(timeout=0xFFFFFF)
        self._spi_command([0x83, 0xFF, 0xFF, 0xFF])

        # Poll for TxDone (max ~2s — a SF7/BW125 packet is <100ms)
        for _ in range(40):
            time.sleep(0.050)
            irq = self._get_irq()
            if irq & IRQ_TX_DONE:
                self._clear_irq()
                return True
        # Timeout — clear IRQ and give up
        self._clear_irq()
        self._emit_status("TX timeout")
        return False

    # ------------------------------------------------------------------
    # Dedup (same as PlutoModem — we see raw air packets)
    # ------------------------------------------------------------------

    def _dedup_seen(self, dedup):
        """Return True if this dedup token was recently seen."""
        if dedup in self._dedup_ring:
            return True
        self._dedup_ring.append(dedup)
        if len(self._dedup_ring) > DEDUP_RING_SIZE:
            self._dedup_ring.pop(0)
        return False

    def _dedup_add(self, dedup):
        """Add our own TX dedup to the ring (avoid self-echo)."""
        if dedup not in self._dedup_ring:
            self._dedup_ring.append(dedup)
            if len(self._dedup_ring) > DEDUP_RING_SIZE:
                self._dedup_ring.pop(0)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    MAX_RECOVERY_ATTEMPTS = 3

    def _run_loop(self):
        """Background thread: init radio, then poll RX and handle TX."""
        try:
            import smbus2
        except ImportError:
            self._emit_status("error: smbus2 not installed")
            return

        # Open I2C
        try:
            self._bus = smbus2.SMBus(self._i2c_bus)
        except OSError as e:
            self._emit_status(f"error: /dev/i2c-{self._i2c_bus}: {e}")
            return

        # Sync + init
        if not self._init_and_enter_rx():
            return

        # Main poll loop
        while self._running:
            try:
                # --- Handle TX ---
                with self._tx_lock:
                    tx_items = list(self._tx_queue)
                    self._tx_queue.clear()

                for ttl, dedup, payload in tx_items:
                    self._dedup_add(dedup)
                    self._transmit(ttl, dedup, payload)
                    # Return to RX after TX
                    self._set_rx()

                # --- Poll RX ---
                irq = self._get_irq()
                if irq & IRQ_RX_DONE:
                    crc_ok = not (irq & IRQ_CRC_ERROR)
                    pkt_len, pkt_offset = self._get_rx_buffer_status()
                    rssi, snr = self._get_packet_status()
                    data = self._read_buffer(pkt_offset, pkt_len)
                    self._clear_irq()
                    self._set_rx()

                    if not crc_ok:
                        self._emit_status(f"rx: CRC error, {pkt_len}B")
                    elif len(data) < 4:
                        self._emit_status(f"rx: too short ({len(data)}B)")
                    else:
                        ttl = data[0]
                        dedup = (data[1] << 8) | data[2]
                        app_payload = data[3:]
                        if ttl > MAX_TTL:
                            self._emit_status(
                                f"rx: ttl={ttl} > MAX_TTL, dropped "
                                f"({pkt_len}B, data={data[:8].hex()})"
                            )
                        elif self._dedup_seen(dedup):
                            self._emit_status(
                                f"rx: dedup 0x{dedup:04x} seen, dropped"
                            )
                        else:
                            pkt = RxPacket(
                                ttl=ttl, dedup=dedup, payload=app_payload,
                                rssi=rssi, snr=snr,
                            )
                            if self._rx_cb:
                                self._rx_cb(pkt)
                else:
                    time.sleep(RX_POLL_INTERVAL)

            except OSError as e:
                self._connected = False
                self._emit_status(f"I2C error: {e}, recovering...")
                if self._recover():
                    continue
                else:
                    self._emit_status("recovery failed, disconnecting")
                    break
            except Exception:
                pass

        # Cleanup
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None
        self._connected = False

    def _init_and_enter_rx(self):
        """Sync buffer, init radio, enter RX. Returns True on success."""
        if not self._sync_buffer():
            self._emit_status("error: buffer sync failed")
            self._bus.close()
            self._bus = None
            return False
        try:
            self._init_radio()
        except Exception as e:
            self._emit_status(f"error: radio init failed: {e}")
            self._bus.close()
            self._bus = None
            return False
        self._set_rx()
        self._connected = True
        self._emit_status("connected on PinePhone backplate")
        return True

    def _recover(self):
        """Attempt to recover from an I2C error by re-syncing + re-init."""
        for attempt in range(1, self.MAX_RECOVERY_ATTEMPTS + 1):
            try:
                time.sleep(0.050)
                if self._sync_buffer():
                    self._init_radio()
                    self._set_rx()
                    self._connected = True
                    self._emit_status(f"recovered (attempt {attempt})")
                    return True
            except OSError:
                pass
            time.sleep(0.100 * attempt)  # backoff
        return False
