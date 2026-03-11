#!/usr/bin/env python3
"""PinePhone LoRa TX test — send a packet, verify RAK receives it.

Implements the full SX1262 init + TX sequence using the verified I2C-SPI
bridge transport. Follows JF's driver init order exactly.

Usage (on PinePhone):
    python3 test_pinephone_tx.py
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

# Timing
CMD_DELAY = 0.010     # 10ms pre-command (WaitOnBusy)
POST_DELAY = 0.000126 # 126us post-command (WaitOnCounter)


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
    i2c_write(bus, [CMD_TRANSMIT, 0x80, 0x00])       # SetStandby(RC)
    time.sleep(0.001)
    i2c_write(bus, [CMD_TRANSMIT, 0x8F, 0x00, 0x00]) # SetBufferBaseAddress
    time.sleep(0.001)

    pattern = [0x10, 0x20, 0x30, 0x40, 0x50, 0xAA, 0x55, 0x00, 0xFF]
    i2c_write(bus, [CMD_TRANSMIT, 0x0E, 0x00] + pattern)  # WriteBuffer
    time.sleep(0.001)
    i2c_write(bus, [CMD_TRANSMIT, 0x1E, 0x00, 0x00] + [0x00] * 9)  # ReadBuffer
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


def get_errors(bus):
    resp = spi_command(bus, [0x17, 0x00, 0x00, 0x00])
    return (resp[2] << 8) | resp[3]


STATUS_MODES = {2: "STDBY_RC", 3: "STDBY_XOSC", 4: "FS", 5: "RX", 6: "TX"}
CMD_STATUSES = {1: "ok", 2: "data_avail", 3: "timeout", 5: "proc_err", 6: "exec_fail"}
IRQ_TX_DONE = 0x0001
IRQ_RX_DONE = 0x0002


def status_str(bus):
    mode, cmd = get_status(bus)
    return f"mode={STATUS_MODES.get(mode, mode)}, cmd={CMD_STATUSES.get(cmd, cmd)}"


def main():
    bus = smbus2.SMBus(I2C_BUS)
    print("=== PinePhone LoRa TX Test ===\n")

    # --- Step 1: Sync ---
    print("1. Buffer sync")
    if not sync_buffer(bus):
        return 1

    # --- Step 2: Init (matches JF's SX126x::Init) ---
    # Reset — can't actually toggle NRESET, just pause (matches JF)
    print("\n2. Init")
    time.sleep(0.020)  # fake reset: 10ms low + 10ms high

    # Wakeup — send GetStatus (matches JF)
    spi_command(bus, [0xC0, 0x00])
    time.sleep(0.010)  # WaitOnBusyLong

    # SetStandby(STDBY_RC)
    spi_command(bus, [0x80, 0x00])
    # SetPacketType(LoRa = 0x01)
    spi_command(bus, [0x8A, 0x01])

    print(f"  after Init: {status_str(bus)}")

    # --- Step 3: Configure (matches JF's PinedioLoraRadio::Initialize) ---
    print("\n3. Configure radio")

    # SetDio2AsRfSwitchCtrl(true)
    spi_command(bus, [0x9D, 0x01])
    # SetStandby(RC) — JF calls this again
    spi_command(bus, [0x80, 0x00])
    # SetRegulatorMode(USE_DCDC = 0x01)
    spi_command(bus, [0x96, 0x01])
    # SetBufferBaseAddresses(tx=0, rx=127)
    spi_command(bus, [0x8F, 0x00, 0x7F])

    # SetTxParams — JF's driver calls SetPaConfig internally:
    # SX1262: SetPaConfig(0x04, 0x07, 0x00, 0x01)
    spi_command(bus, [0x95, 0x04, 0x07, 0x00, 0x01])
    # OCP register = 0x38 (160mA)
    write_register(bus, 0x08E7, 0x38)
    # SetTxParams(power=22, ramp=RADIO_RAMP_3400_US=0x07)
    spi_command(bus, [0x8E, 0x16, 0x07])

    # SetDioIrqParams(irqMask=0xFFFF, dio1=0x0001, dio2=0, dio3=0)
    spi_command(bus, [0x08, 0xFF, 0xFF, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00])

    # SetRfFrequency(915 MHz)
    # JF's driver calls CalibrateImage internally for >900MHz
    spi_command(bus, [0x98, 0xE1, 0xE9])  # CalibrateImage 902-928 MHz
    time.sleep(0.010)
    freq = int(915e6 / (32e6 / (1 << 25)))
    spi_command(bus, [0x86, (freq >> 24) & 0xFF, (freq >> 16) & 0xFF,
                      (freq >> 8) & 0xFF, freq & 0xFF])

    # SetPacketType(LoRa) — JF calls this again
    spi_command(bus, [0x8A, 0x01])

    # SetStopRxTimerOnPreambleDetect(false)
    spi_command(bus, [0x9F, 0x00])

    # SetModulationParams(SF7=0x07, BW125=0x04, CR4/5=0x01, LDROpt=0x00)
    spi_command(bus, [0x8B, 0x07, 0x04, 0x01, 0x00])
    # BW != 500kHz, so set bit 2 of REG_TX_MODULATION
    txmod = read_register(bus, 0x0889)
    write_register(bus, 0x0889, txmod | (1 << 2))

    err = get_errors(bus)
    print(f"  errors: 0x{err:04x}")
    print(f"  status: {status_str(bus)}")

    # --- Step 4: Send packet ---
    print("\n4. Send packet")
    payload = b"PINE TX OK"
    print(f"  payload: {payload!r} ({len(payload)} bytes)")

    # SetPacketParams(preamble=8, header=variable, payloadLen, CRC=on, IQ=normal)
    spi_command(bus, [0x8C, 0x00, 0x08, 0x00, len(payload), 0x01, 0x00])

    # IQ polarity workaround (normal IQ — set bit 2)
    iq_reg = read_register(bus, 0x0736)
    write_register(bus, 0x0736, iq_reg | (1 << 2))

    # ClearIrqStatus(all)
    spi_command(bus, [0x02, 0xFF, 0xFF])

    # WriteBuffer(offset=0, payload) — chunked if needed
    chunk = list(payload)
    if len(chunk) <= 29:
        spi_command(bus, [0x0E, 0x00] + chunk)
    else:
        # Multi-chunk write
        offset = 0
        while offset < len(chunk):
            end = min(offset + 29, len(chunk))
            spi_command(bus, [0x0E, offset] + chunk[offset:end])
            offset = end

    # Verify buffer contents
    resp = spi_command(bus, [0x1E, 0x00, 0x00] + [0x00] * len(payload))
    readback = bytes(resp[3:])
    if readback != payload:
        print(f"  buffer MISMATCH: {readback!r}")
        return 1
    print(f"  buffer verified OK")

    # SetTx(timeout=0xFFFFFF — max, ~262s)
    print(f"  SetTx...")
    spi_command(bus, [0x83, 0xFF, 0xFF, 0xFF])

    # Poll for TX done
    print(f"  waiting for TxDone...")
    for i in range(50):  # 50 * 100ms = 5s max
        time.sleep(0.100)
        irq = get_irq(bus)
        mode, cmd = get_status(bus)
        if irq & IRQ_TX_DONE:
            print(f"  TX DONE! (irq=0x{irq:04x}, mode={STATUS_MODES.get(mode, mode)})")
            # Clear IRQ
            spi_command(bus, [0x02, 0xFF, 0xFF])
            break
        if i % 10 == 0:
            print(f"    poll {i}: irq=0x{irq:04x}, mode={STATUS_MODES.get(mode, mode)}")
    else:
        irq = get_irq(bus)
        mode, _ = get_status(bus)
        print(f"  TIMEOUT — no TxDone after 5s (irq=0x{irq:04x}, mode={STATUS_MODES.get(mode, mode)})")
        return 1

    print("\nDone. Check RAK serial output for received packet.")
    bus.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
