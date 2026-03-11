#!/usr/bin/env python3
"""PinePhone LoRa RX test — receive packets from RAK TX beacon.

Sets up the SX1262 in continuous RX mode and polls for incoming packets.
The RAK should be running LoRaP2P_TX (sends "Hello" every 5s).

Usage (on PinePhone):
    python3 test_pinephone_rx.py
"""

import sys
import time

try:
    import smbus2
except ImportError:
    print("error: smbus2 not installed (pip3 install smbus2)")
    sys.exit(1)


# ATtiny84 I2C-to-SPI bridge
I2C_BUS = 2
I2C_ADDR = 0x28
CMD_TRANSMIT = 0x01
CMD_DELAY = 0.010
POST_DELAY = 0.000126


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


def read_register(bus, addr):
    resp = spi_command(bus, [0x1D, (addr >> 8) & 0xFF, addr & 0xFF, 0x00, 0x00])
    return resp[-1]


def write_register(bus, addr, val):
    spi_command(bus, [0x0D, (addr >> 8) & 0xFF, addr & 0xFF, val])


def get_status(bus):
    resp = spi_command(bus, [0xC0, 0x00])
    status = resp[0]
    return (status >> 4) & 0x07, (status >> 1) & 0x07


def get_irq(bus):
    resp = spi_command(bus, [0x12, 0x00, 0x00, 0x00])
    return (resp[2] << 8) | resp[3]


def get_rx_buffer_status(bus):
    """GetRxBufferStatus — returns (payload_len, rx_start_offset)."""
    resp = spi_command(bus, [0x13, 0x00, 0x00, 0x00])
    return resp[2], resp[3]


def get_packet_status(bus):
    """GetPacketStatus for LoRa — returns (rssi, snr)."""
    resp = spi_command(bus, [0x14, 0x00, 0x00, 0x00, 0x00])
    rssi = -resp[2] // 2
    snr = resp[3] if resp[3] < 128 else (resp[3] - 256)
    snr = snr // 4
    return rssi, snr


def read_buffer(bus, offset, size):
    """Read from SX1262 data buffer, chunked to respect smbus2 limit."""
    result = []
    pos = 0
    while pos < size:
        chunk = min(size - pos, 28)  # max 28 data bytes per read
        resp = spi_command(bus, [0x1E, offset + pos, 0x00] + [0x00] * chunk)
        result.extend(resp[3:])
        pos += chunk
    return bytes(result)


STATUS_MODES = {2: "STDBY_RC", 3: "STDBY_XOSC", 4: "FS", 5: "RX", 6: "TX"}
IRQ_RX_DONE = 0x0002
IRQ_CRC_ERROR = 0x0040
IRQ_HEADER_VALID = 0x0010


def main():
    bus = smbus2.SMBus(I2C_BUS)
    print("=== PinePhone LoRa RX Test ===\n")

    # --- Sync ---
    print("1. Buffer sync")
    if not sync_buffer(bus):
        return 1

    # --- Init (JF's SX126x::Init) ---
    print("\n2. Init")
    time.sleep(0.020)
    spi_command(bus, [0xC0, 0x00])  # Wakeup
    time.sleep(0.010)
    spi_command(bus, [0x80, 0x00])  # SetStandby(RC)
    spi_command(bus, [0x8A, 0x01])  # SetPacketType(LoRa)

    # --- Configure (JF's PinedioLoraRadio::Initialize) ---
    print("3. Configure radio")
    spi_command(bus, [0x9D, 0x01])  # SetDio2AsRfSwitchCtrl(true)
    spi_command(bus, [0x80, 0x00])  # SetStandby(RC)
    spi_command(bus, [0x96, 0x01])  # SetRegulatorMode(DCDC)
    spi_command(bus, [0x8F, 0x00, 0x7F])  # SetBufferBaseAddresses(tx=0, rx=127)

    # TX params (needed even for RX — JF sets them during init)
    spi_command(bus, [0x95, 0x04, 0x07, 0x00, 0x01])  # SetPaConfig
    write_register(bus, 0x08E7, 0x38)  # OCP 160mA
    spi_command(bus, [0x8E, 0x16, 0x07])  # SetTxParams(+22, RAMP_3400)

    # IRQ: all on irqMask, RxDone on DIO1
    spi_command(bus, [0x08, 0xFF, 0xFF, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00])

    # Frequency
    spi_command(bus, [0x98, 0xE1, 0xE9])  # CalibrateImage 902-928
    time.sleep(0.010)
    freq = int(915e6 / (32e6 / (1 << 25)))
    spi_command(bus, [0x86, (freq >> 24) & 0xFF, (freq >> 16) & 0xFF,
                      (freq >> 8) & 0xFF, freq & 0xFF])

    spi_command(bus, [0x8A, 0x01])  # SetPacketType(LoRa)
    spi_command(bus, [0x9F, 0x00])  # SetStopRxTimerOnPreambleDetect(false)

    # Modulation: SF7, BW125, CR4/5, no LDRO
    spi_command(bus, [0x8B, 0x07, 0x04, 0x01, 0x00])
    txmod = read_register(bus, 0x0889)
    write_register(bus, 0x0889, txmod | (1 << 2))

    # Packet params: preamble=8, variable header, max payload=64, CRC on, normal IQ
    spi_command(bus, [0x8C, 0x00, 0x08, 0x00, 0x40, 0x01, 0x00])
    iq_reg = read_register(bus, 0x0736)
    write_register(bus, 0x0736, iq_reg | (1 << 2))

    # Clear IRQ and enter RX
    spi_command(bus, [0x02, 0xFF, 0xFF])  # ClearIrqStatus

    # SetRx(timeout=0xFFFFFF — continuous)
    print("4. Entering continuous RX...")
    spi_command(bus, [0x82, 0xFF, 0xFF, 0xFF])

    mode, cmd = get_status(bus)
    print(f"  status: mode={STATUS_MODES.get(mode, mode)}, cmd={cmd}")

    # --- Poll for packets ---
    print("\n5. Waiting for packets (30s)...\n")
    packets = 0
    for i in range(300):  # 300 * 100ms = 30s
        time.sleep(0.100)
        irq = get_irq(bus)

        if irq & IRQ_RX_DONE:
            crc_err = bool(irq & IRQ_CRC_ERROR)
            # Get payload info
            pkt_len, pkt_offset = get_rx_buffer_status(bus)
            rssi, snr = get_packet_status(bus)
            # Read payload
            payload = read_buffer(bus, pkt_offset, pkt_len)
            # Clear IRQ
            spi_command(bus, [0x02, 0xFF, 0xFF])

            packets += 1
            crc_str = " CRC_ERR" if crc_err else ""
            try:
                text = payload.decode('ascii')
                print(f"  [{packets}] {pkt_len}B, RSSI={rssi}dBm, SNR={snr}: "
                      f"{text!r}{crc_str}")
            except UnicodeDecodeError:
                print(f"  [{packets}] {pkt_len}B, RSSI={rssi}dBm, SNR={snr}: "
                      f"{payload.hex()}{crc_str}")

            # Re-enter RX
            spi_command(bus, [0x82, 0xFF, 0xFF, 0xFF])

    print(f"\nDone. Received {packets} packets in 30s.")
    bus.close()
    return 0 if packets > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
