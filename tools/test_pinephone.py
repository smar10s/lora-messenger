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


def spi_transfer(bus, data):
    """Send SPI bytes through the ATtiny bridge, read back responses."""
    bus.write_i2c_block_data(I2C_ADDR, CMD_TRANSMIT, data)
    time.sleep(0.005)
    return [bus.read_byte(I2C_ADDR) for _ in range(len(data))]


def read_register(bus, addr):
    """Read a single SX1262 register via ReadRegister (opcode 0x1D)."""
    resp = spi_transfer(bus, [0x1D, (addr >> 8) & 0xFF, addr & 0xFF, 0x00, 0x00])
    return resp[-1]


def main():
    # --- Open I2C bus ---
    try:
        bus = smbus2.SMBus(I2C_BUS)
    except OSError as e:
        print(f"error: cannot open /dev/i2c-{I2C_BUS}: {e}")
        print("check permissions: ls -la /dev/i2c-2")
        return 1

    print(f"I2C bus {I2C_BUS} opened, bridge at 0x{I2C_ADDR:02x}")

    # --- Read version register ---
    ver = read_register(bus, REG_VERSION)
    print(f"SX1262 version register (0x{REG_VERSION:04x}): 0x{ver:02x}")

    if ver == 0x00 or ver == 0xFF:
        print("FAIL: version register returned 0x00 or 0xFF (no SX1262 response)")
        bus.close()
        return 1

    # --- Read twice for consistency ---
    ver2 = read_register(bus, REG_VERSION)
    if ver != ver2:
        print(f"FAIL: inconsistent reads (0x{ver:02x} then 0x{ver2:02x})")
        bus.close()
        return 1
    print(f"  consistent: 2/2 reads match")

    # --- Read a different register ---
    sw = read_register(bus, REG_SYNCWORD_HI)
    print(f"LoRa sync word MSB (0x{REG_SYNCWORD_HI:04x}): 0x{sw:02x}")

    # --- Summary ---
    bus.close()
    print("\nPASS: I2C -> ATtiny -> SPI -> SX1262 path works")
    return 0


if __name__ == "__main__":
    sys.exit(main())
