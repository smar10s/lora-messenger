#!/usr/bin/env python3
"""PinePhone LoRa backplate hardware test.

Verifies the full hardware path: Python -> I2C -> ATtiny84 -> SPI -> SX1262.
Run on the PinePhone with the LoRa backplate attached.

Usage:
    python3 tools/test_pinephone.py
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

# SX1262 registers
REG_VERSION = 0x0320
REG_SYNCWORD_HI = 0x0740  # LoRa sync word MSB
REG_SYNCWORD_LO = 0x0741  # LoRa sync word LSB


def spi_transfer(bus, data):
    """Send SPI bytes through the ATtiny bridge, read back responses."""
    bus.write_i2c_block_data(I2C_ADDR, CMD_TRANSMIT, data)
    time.sleep(0.005)
    return [bus.read_byte(I2C_ADDR) for _ in range(len(data))]


def read_register(bus, addr):
    """Read a single SX1262 register via ReadRegister (opcode 0x1D)."""
    resp = spi_transfer(bus, [0x1D, (addr >> 8) & 0xFF, addr & 0xFF, 0x00, 0x00])
    return resp[-1]


def write_register(bus, addr, val):
    """Write a single SX1262 register via WriteRegister (opcode 0x0D)."""
    spi_transfer(bus, [0x0D, (addr >> 8) & 0xFF, addr & 0xFF, val])


def get_status(bus):
    """GetStatus (opcode 0xC0). Returns (mode, cmd_status) from status byte."""
    resp = spi_transfer(bus, [0xC0, 0x00])
    status = resp[0]
    mode = (status >> 4) & 0x07
    cmd_status = (status >> 1) & 0x07
    return mode, cmd_status


def get_errors(bus):
    """GetDeviceErrors (opcode 0x17). Returns raw 16-bit error word."""
    resp = spi_transfer(bus, [0x17, 0x00, 0x00, 0x00])
    return (resp[2] << 8) | resp[3]


STATUS_MODES = {
    2: "STDBY_RC", 3: "STDBY_XOSC", 4: "FS", 5: "RX", 6: "TX",
}
CMD_STATUSES = {
    1: "ok", 2: "data available", 3: "timeout", 5: "processing error", 6: "exec failure",
}
ERROR_FLAGS = [
    "RC64K_CALIB", "RC13M_CALIB", "PLL_CALIB", "ADC_CALIB",
    "IMG_CALIB", "XOSC_START", "PLL_LOCK", "PA_RAMP",
]


def error_names(err):
    return [ERROR_FLAGS[i] for i in range(8) if err & (1 << i)]


def main():
    failed = False

    # --- Open I2C bus ---
    try:
        bus = smbus2.SMBus(I2C_BUS)
    except OSError as e:
        print(f"error: cannot open /dev/i2c-{I2C_BUS}: {e}")
        print("check permissions: ls -la /dev/i2c-2")
        return 1

    # Drain any stale bytes from the ATtiny circular buffer
    for _ in range(128):
        try:
            bus.read_byte(I2C_ADDR)
        except Exception:
            break

    print(f"I2C bus {I2C_BUS} opened, bridge at 0x{I2C_ADDR:02x}\n")

    # --- Test 1: read version register ---
    print("test 1: read version register")
    ver = read_register(bus, REG_VERSION)
    ver2 = read_register(bus, REG_VERSION)
    print(f"  version = 0x{ver:02x}")
    if ver == 0x00 or ver == 0xFF:
        print("  FAIL: no SX1262 response (0x00 or 0xFF)")
        bus.close()
        return 1
    if ver != ver2:
        print(f"  FAIL: inconsistent (0x{ver:02x} then 0x{ver2:02x})")
        bus.close()
        return 1
    print(f"  PASS: consistent reads")

    # --- Test 2: GetStatus + SetStandby ---
    print("\ntest 2: GetStatus + SetStandby")
    mode, cmd_st = get_status(bus)
    print(f"  status: mode={STATUS_MODES.get(mode, mode)}, cmd={CMD_STATUSES.get(cmd_st, cmd_st)}")
    spi_transfer(bus, [0x80, 0x00])  # SetStandby STDBY_RC
    time.sleep(0.01)
    mode, cmd_st = get_status(bus)
    print(f"  after SetStandby: mode={STATUS_MODES.get(mode, mode)}, cmd={CMD_STATUSES.get(cmd_st, cmd_st)}")
    if mode != 2:
        print(f"  FAIL: expected STDBY_RC (2), got {mode}")
        failed = True
    else:
        print(f"  PASS: in STDBY_RC")

    # --- Test 3: write register and read back ---
    print("\ntest 3: register write/readback")
    orig_hi = read_register(bus, REG_SYNCWORD_HI)
    orig_lo = read_register(bus, REG_SYNCWORD_LO)
    print(f"  original sync word: 0x{orig_hi:02x} 0x{orig_lo:02x}")

    test_hi, test_lo = 0x34, 0x44
    write_register(bus, REG_SYNCWORD_HI, test_hi)
    write_register(bus, REG_SYNCWORD_LO, test_lo)
    got_hi = read_register(bus, REG_SYNCWORD_HI)
    got_lo = read_register(bus, REG_SYNCWORD_LO)
    print(f"  after write 0x{test_hi:02x} 0x{test_lo:02x}: got 0x{got_hi:02x} 0x{got_lo:02x}")

    if got_hi != test_hi or got_lo != test_lo:
        print(f"  FAIL: readback mismatch")
        failed = True
    else:
        print(f"  PASS: write/readback matches")

    write_register(bus, REG_SYNCWORD_HI, orig_hi)
    write_register(bus, REG_SYNCWORD_LO, orig_lo)

    # --- Test 4: TCXO and radio configuration ---
    print("\ntest 4: TCXO + radio config")

    # SetDio3AsTcxoCtrl: 1.7V, max timeout (~262s)
    # The backplate uses a TCXO on DIO3. It needs a long startup time
    # (500ms observed). Max timeout keeps it powered in all states.
    spi_transfer(bus, [0x97, 0x01, 0xFF, 0xFF, 0xFF])
    time.sleep(0.5)

    # Clear errors and calibrate
    spi_transfer(bus, [0x07, 0x00, 0x00])  # ClearDeviceErrors
    spi_transfer(bus, [0x89, 0x7F])         # Calibrate(all)
    time.sleep(0.1)

    err = get_errors(bus)
    if err:
        print(f"  FAIL: calibration errors 0x{err:04x} {error_names(err)}")
        failed = True
    else:
        print(f"  PASS: TCXO started, calibration clean")

    # Configure radio: LoRa, 915 MHz, SF7/BW125k/CR4-5
    spi_transfer(bus, [0x9D, 0x01])  # SetDio2AsRfSwitchCtrl
    spi_transfer(bus, [0x8A, 0x01])  # SetPacketType = LoRa
    freq_reg = int(915e6 * (2**25) / 32e6)
    spi_transfer(bus, [0x86, (freq_reg >> 24) & 0xFF, (freq_reg >> 16) & 0xFF,
                       (freq_reg >> 8) & 0xFF, freq_reg & 0xFF])
    spi_transfer(bus, [0x98, 0xE1, 0xE9])  # CalibrateImage 902-928 MHz
    time.sleep(0.1)
    spi_transfer(bus, [0x07, 0x00, 0x00])   # ClearDeviceErrors
    spi_transfer(bus, [0x95, 0x04, 0x07, 0x00, 0x01])  # SetPaConfig (SX1262)
    spi_transfer(bus, [0x8E, 0x16, 0x04])   # SetTxParams +22dBm
    spi_transfer(bus, [0x8B, 0x07, 0x04, 0x01, 0x00])  # SetModulationParams

    err = get_errors(bus)
    mode, cmd_st = get_status(bus)
    if err:
        print(f"  FAIL: config errors 0x{err:04x} {error_names(err)}")
        failed = True
    elif cmd_st != 1:
        print(f"  FAIL: cmd_status={CMD_STATUSES.get(cmd_st, cmd_st)}")
        failed = True
    else:
        print(f"  PASS: radio configured (915 MHz, SF7/BW125k/CR4-5, +22 dBm)")

    # --- Test 5: buffer write/readback ---
    print("\ntest 5: TX buffer write/readback")
    spi_transfer(bus, [0x8F, 0x00, 0x00])  # SetBufferBaseAddress
    payload = b"Hello from PinePhone!"
    spi_transfer(bus, [0x0E, 0x00] + list(payload))  # WriteBuffer

    # ReadBuffer
    resp = spi_transfer(bus, [0x1E, 0x00, 0x00] + [0x00] * len(payload))
    readback = bytes(resp[3:])

    if readback == payload:
        print(f"  PASS: buffer roundtrip ({len(payload)} bytes)")
    else:
        print(f"  FAIL: buffer mismatch")
        print(f"    wrote: {payload}")
        print(f"    read:  {readback}")
        failed = True

    # --- Summary ---
    bus.close()
    if failed:
        print("\nFAIL: some tests failed")
        return 1
    print("\nPASS: all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
