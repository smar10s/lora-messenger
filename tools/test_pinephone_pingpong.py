#!/usr/bin/env python3
"""PinePhone ping-pong test — half-duplex TX/RX cycle validation.

Sends PING, waits for PONG, repeats. Tests the exact TX->RX transition
that chat ACKs need. Partner: RAK running LoRaP2P_PingPong firmware.

No protocol layer, no encryption, no modem abstraction — raw SX1262
commands only, minimal code path to isolate radio timing from app
complexity.

The SX1262 transmitter uses PacketParams.PayloadLength to determine how
many buffer bytes to send. SetPacketParams is called with the actual
payload length before each TX. The receiver decodes length from the LoRa
explicit header, so RX doesn't need a matching PacketParams update.

Usage (on PinePhone):
    python3 test_pinephone_pingpong.py [rounds]

Default: 20 rounds. RAK must be running LoRaP2P_PingPong responder.
"""

import sys
import time

try:
    import smbus2
except ImportError:
    print("error: smbus2 not installed (pip3 install smbus2)")
    sys.exit(1)


# ---------------------------------------------------------------------------
# ATtiny84 I2C-SPI bridge
# ---------------------------------------------------------------------------
I2C_BUS = 2
I2C_ADDR = 0x28
CMD_TRANSMIT = 0x01
CMD_DELAY = 0.010      # 10ms pre-command (fake WaitOnBusy)
POST_DELAY = 0.000126  # 126us post-command

# ---------------------------------------------------------------------------
# SX1262 constants
# ---------------------------------------------------------------------------
LORA_MAX_PAYLOAD = 64
IRQ_TX_DONE = 0x0001
IRQ_RX_DONE = 0x0002
IRQ_CRC_ERROR = 0x0040
STATUS_MODES = {2: "STDBY_RC", 3: "STDBY_XOSC", 4: "FS", 5: "RX", 6: "TX"}

# ---------------------------------------------------------------------------
# Test parameters
# ---------------------------------------------------------------------------
DEFAULT_ROUNDS = 20
RX_TIMEOUT_S = 3.0     # max wait for PONG
RX_POLL_MS = 50         # poll interval during RX wait
POST_RX_DELAY_S = 0.200 # pause after receiving PONG before next PING


# ---------------------------------------------------------------------------
# Transport (identical to test_pinephone_tx.py)
# ---------------------------------------------------------------------------

def i2c_write(bus, data):
    if len(data) > 32:
        raise ValueError(f"I2C write too large: {len(data)} bytes (max 32)")
    if len(data) < 2:
        bus.write_byte(I2C_ADDR, data[0])
    else:
        bus.write_i2c_block_data(I2C_ADDR, data[0], list(data[1:]))


def i2c_read_byte(bus):
    return bus.read_byte(I2C_ADDR)


def spi_command(bus, data):
    if len(data) > 31:
        raise ValueError(f"SPI command too large: {len(data)} bytes (max 31)")
    time.sleep(CMD_DELAY)
    i2c_write(bus, [CMD_TRANSMIT] + list(data))
    time.sleep(POST_DELAY)
    return [i2c_read_byte(bus) for _ in range(len(data))]


def sync_buffer(bus):
    """JF's SyncI2CBuffer — align ATtiny circular buffer."""
    i2c_write(bus, [CMD_TRANSMIT, 0x80, 0x00])
    time.sleep(0.001)
    i2c_write(bus, [CMD_TRANSMIT, 0x8F, 0x00, 0x00])
    time.sleep(0.001)
    pattern = [0x10, 0x20, 0x30, 0x40, 0x50, 0xAA, 0x55, 0x00, 0xFF]
    i2c_write(bus, [CMD_TRANSMIT, 0x0E, 0x00] + pattern)
    time.sleep(0.001)
    i2c_write(bus, [CMD_TRANSMIT, 0x1E, 0x00, 0x00] + [0x00] * 9)
    time.sleep(0.001)
    seq_started = False
    seq_index = 0
    for count in range(256):
        d = i2c_read_byte(bus)
        if not seq_started:
            for i in range(len(pattern)):
                if d == pattern[i]:
                    seq_started = True
                    seq_index = i
                    break
        else:
            if seq_index + 1 < len(pattern) and d == pattern[seq_index + 1]:
                seq_index += 1
                if seq_index == len(pattern) - 1:
                    print(f"  sync: aligned after {count + 1} bytes")
                    return True
            else:
                seq_started = False
                for i in range(len(pattern)):
                    if d == pattern[i]:
                        seq_started = True
                        seq_index = i
                        break
    print("  sync: FAILED")
    return False


# ---------------------------------------------------------------------------
# SX1262 helpers
# ---------------------------------------------------------------------------

def read_register(bus, addr):
    resp = spi_command(bus, [0x1D, (addr >> 8) & 0xFF, addr & 0xFF, 0x00, 0x00])
    return resp[-1]


def write_register(bus, addr, val):
    spi_command(bus, [0x0D, (addr >> 8) & 0xFF, addr & 0xFF, val])


def get_status(bus):
    resp = spi_command(bus, [0xC0, 0x00])
    return (resp[0] >> 4) & 0x07, (resp[0] >> 1) & 0x07


def get_irq(bus):
    resp = spi_command(bus, [0x12, 0x00, 0x00, 0x00])
    return (resp[2] << 8) | resp[3]


def clear_irq(bus):
    spi_command(bus, [0x02, 0xFF, 0xFF])


def get_rx_buffer_status(bus):
    resp = spi_command(bus, [0x13, 0x00, 0x00, 0x00])
    return resp[2], resp[3]


def get_packet_status(bus):
    resp = spi_command(bus, [0x14, 0x00, 0x00, 0x00, 0x00])
    rssi = -(resp[2] // 2)
    snr = resp[3] if resp[3] < 128 else (resp[3] - 256)
    return rssi, snr // 4


def read_buffer(bus, offset, size):
    result = []
    pos = 0
    while pos < size:
        chunk = min(size - pos, 28)
        resp = spi_command(bus, [0x1E, offset + pos, 0x00] + [0x00] * chunk)
        result.extend(resp[3:])
        pos += chunk
    return bytes(result)


def write_buffer(bus, offset, data):
    pos = 0
    while pos < len(data):
        chunk = min(len(data) - pos, 29)
        spi_command(bus, [0x0E, offset + pos] + list(data[pos:pos + chunk]))
        pos += chunk


def set_rx(bus):
    spi_command(bus, [0x82, 0xFF, 0xFF, 0xFF])


def status_str(bus):
    mode, cmd = get_status(bus)
    cmd_names = {1: "ok", 2: "data_avail", 3: "timeout", 5: "proc_err", 6: "exec_fail"}
    return f"mode={STATUS_MODES.get(mode, mode)}, cmd={cmd_names.get(cmd, cmd)}"


# ---------------------------------------------------------------------------
# Init — same as test_pinephone_tx.py, but SetPacketParams uses max payload
# ---------------------------------------------------------------------------

def set_packet_params(bus, payload_len):
    """SetPacketParams: preamble=8, variable header, payloadLen, CRC on, normal IQ."""
    spi_command(bus, [0x8C, 0x00, 0x08, 0x00, payload_len, 0x01, 0x00])


def init_radio(bus):
    """Full SX1262 init. SetPacketParams set to LORA_MAX_PAYLOAD for RX.
    TX calls set_packet_params with actual length before each send."""
    # Init (JF's SX126x::Init)
    time.sleep(0.020)
    spi_command(bus, [0xC0, 0x00])  # Wakeup
    time.sleep(0.010)
    spi_command(bus, [0x80, 0x00])  # SetStandby(RC)
    spi_command(bus, [0x8A, 0x01])  # SetPacketType(LoRa)

    # Configure (JF's PinedioLoraRadio::Initialize)
    spi_command(bus, [0x9D, 0x01])           # SetDio2AsRfSwitchCtrl(true)
    spi_command(bus, [0x80, 0x00])           # SetStandby(RC)
    spi_command(bus, [0x96, 0x01])           # SetRegulatorMode(DCDC)
    spi_command(bus, [0x8F, 0x00, 0x7F])    # SetBufferBaseAddresses(tx=0, rx=127)

    # PA + TX power
    spi_command(bus, [0x95, 0x04, 0x07, 0x00, 0x01])  # SetPaConfig
    write_register(bus, 0x08E7, 0x38)                   # OCP 160mA
    spi_command(bus, [0x8E, 0x16, 0x07])                # SetTxParams(+22, RAMP_3400)

    # IRQ
    spi_command(bus, [0x08, 0xFF, 0xFF, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00])

    # Frequency
    spi_command(bus, [0x98, 0xE1, 0xE9])  # CalibrateImage 902-928 MHz
    time.sleep(0.010)
    freq = int(915e6 / (32e6 / (1 << 25)))
    spi_command(bus, [0x86, (freq >> 24) & 0xFF, (freq >> 16) & 0xFF,
                      (freq >> 8) & 0xFF, freq & 0xFF])

    spi_command(bus, [0x8A, 0x01])  # SetPacketType(LoRa) — again per JF
    spi_command(bus, [0x9F, 0x00])  # SetStopRxTimerOnPreambleDetect(false)

    # Modulation: SF7, BW125, CR4/5, no LDRO
    spi_command(bus, [0x8B, 0x07, 0x04, 0x01, 0x00])
    txmod = read_register(bus, 0x0889)
    write_register(bus, 0x0889, txmod | (1 << 2))  # BW workaround

    # Packet params — LORA_MAX_PAYLOAD for RX; TX overrides per-send
    spi_command(bus, [0x8C, 0x00, 0x08, 0x00, LORA_MAX_PAYLOAD, 0x01, 0x00])
    iq_reg = read_register(bus, 0x0736)
    write_register(bus, 0x0736, iq_reg | (1 << 2))  # IQ polarity workaround

    clear_irq(bus)


# ---------------------------------------------------------------------------
# TX — minimal: write buffer, SetTx, poll for TxDone
# ---------------------------------------------------------------------------

def transmit(bus, payload):
    """Send payload bytes. Returns True on TxDone, False on timeout.

    Sets PacketParams to actual payload length for TX. Does NOT restore
    to LORA_MAX_PAYLOAD after — in variable-length explicit header mode,
    the receiver decodes length from the header, so PacketParams.PayloadLength
    shouldn't matter for RX.
    """
    set_packet_params(bus, len(payload))
    clear_irq(bus)
    write_buffer(bus, 0x00, payload)
    spi_command(bus, [0x83, 0xFF, 0xFF, 0xFF])  # SetTx

    for i in range(40):  # 40 * 50ms = 2s
        time.sleep(0.050)
        irq = get_irq(bus)
        if irq & IRQ_TX_DONE:
            clear_irq(bus)
            return True
    clear_irq(bus)
    return False


# ---------------------------------------------------------------------------
# RX — poll for RxDone, return payload or None
# ---------------------------------------------------------------------------

def receive(bus, timeout_s):
    """Poll for one RX packet. Returns (payload_bytes, rssi, snr) or None.

    Logs every poll result for timing analysis. Caller must re-enter RX
    before calling this.
    """
    polls = int(timeout_s * 1000 / RX_POLL_MS)
    t_start = time.monotonic()

    for i in range(polls):
        time.sleep(RX_POLL_MS / 1000)
        irq = get_irq(bus)

        if irq & IRQ_RX_DONE:
            t_rx = time.monotonic() - t_start
            crc_err = bool(irq & IRQ_CRC_ERROR)
            pkt_len, pkt_offset = get_rx_buffer_status(bus)
            rssi, snr = get_packet_status(bus)
            data = read_buffer(bus, pkt_offset, pkt_len)
            clear_irq(bus)

            if crc_err:
                print(f"    poll {i}: RxDone CRC_ERROR at {t_rx:.3f}s "
                      f"(len={pkt_len}, offset={pkt_offset}, "
                      f"data={data[:16].hex()})")
                # Keep polling — maybe a valid packet follows
                set_rx(bus)
                continue

            return data, rssi, snr

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROUNDS

    bus = smbus2.SMBus(I2C_BUS)
    print(f"=== PinePhone Ping-Pong Test ({rounds} rounds) ===\n")

    # --- Sync ---
    print("1. Buffer sync")
    if not sync_buffer(bus):
        return 1

    # --- Init ---
    print("\n2. Init radio")
    init_radio(bus)
    print(f"  {status_str(bus)}")

    # --- Ping-pong ---
    print(f"\n3. Starting ping-pong ({rounds} rounds)\n")

    sent = 0
    received = 0
    timeouts = 0
    rtts = []

    for n in range(1, rounds + 1):
        ping_payload = f"PING {n:02d}".encode()
        expected_pong = f"PONG {n:02d}".encode()

        # TX: send PING
        t_send = time.monotonic()
        print(f"  [{n:02d}] TX: {ping_payload.decode()}")
        ok = transmit(bus, ping_payload)
        if not ok:
            print(f"  [{n:02d}] TX TIMEOUT — aborting")
            break
        sent += 1

        # Transition to RX — minimal: just SetRx (ClearIrq done in transmit)
        set_rx(bus)
        t_rx_enter = time.monotonic()
        tx_time_ms = (t_rx_enter - t_send) * 1000
        print(f"         TX done, entered RX ({tx_time_ms:.0f}ms)")

        # RX: wait for PONG
        result = receive(bus, RX_TIMEOUT_S)

        if result is None:
            timeouts += 1
            print(f"  [{n:02d}] RX: TIMEOUT ({RX_TIMEOUT_S}s)")
            # Re-enter RX for next round (receive exits without SetRx on timeout)
            set_rx(bus)
        else:
            data, rssi, snr = result
            rtt = (time.monotonic() - t_send) * 1000
            rtts.append(rtt)

            try:
                text = data.decode('ascii')
            except UnicodeDecodeError:
                text = data.hex()

            match = "OK" if data == expected_pong else f"MISMATCH (expected {expected_pong!r})"
            print(f"  [{n:02d}] RX: {text!r} ({len(data)}B, rssi={rssi}, snr={snr}, "
                  f"rtt={rtt:.0f}ms) {match}")

            if data == expected_pong:
                received += 1
            # Re-enter RX before next PING (clean state)
            set_rx(bus)

        # Pause before next round
        if n < rounds:
            time.sleep(POST_RX_DELAY_S)

    # --- Summary ---
    print(f"\n{'='*50}")
    print(f"RESULTS: {received}/{sent} PONGs received, "
          f"{timeouts} timeouts")
    if rtts:
        print(f"RTT: min={min(rtts):.0f}ms, avg={sum(rtts)/len(rtts):.0f}ms, "
              f"max={max(rtts):.0f}ms")
    if received == sent and sent == rounds:
        print("PASS")
    else:
        print("FAIL")

    bus.close()
    return 0 if received == sent else 1


if __name__ == "__main__":
    sys.exit(main())
