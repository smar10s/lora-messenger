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


STATUS_MODES = {
    2: "STDBY_RC", 3: "STDBY_XOSC", 4: "FS", 5: "RX", 6: "TX",
}
CMD_STATUSES = {
    1: "ok", 2: "data available", 3: "timeout", 5: "processing error", 6: "exec failure",
}


def main():
    failed = False

    # --- Open I2C bus ---
    try:
        bus = smbus2.SMBus(I2C_BUS)
    except OSError as e:
        print(f"error: cannot open /dev/i2c-{I2C_BUS}: {e}")
        print("check permissions: ls -la /dev/i2c-2")
        return 1

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
    # SetStandby STDBY_RC (opcode 0x80, arg 0x00)
    spi_transfer(bus, [0x80, 0x00])
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

    # Write a different value
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

    # Restore original values
    write_register(bus, REG_SYNCWORD_HI, orig_hi)
    write_register(bus, REG_SYNCWORD_LO, orig_lo)

    # --- Summary ---
    bus.close()
    if failed:
        print("\nFAIL: some tests failed")
        return 1
    print("\nPASS: all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
