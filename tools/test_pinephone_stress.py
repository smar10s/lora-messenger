#!/usr/bin/env python3
"""Stress test: sustained RX polling to provoke I2C errors.

Runs the PinePhoneModem's core loop pattern (get_irq + status checks)
at maximum speed for a configurable duration. Reports any errors and
tests recovery.

Usage (on PinePhone):
    python3 test_pinephone_stress.py [seconds]
"""
import sys
import time

try:
    import smbus2
except ImportError:
    print("error: smbus2 not installed")
    sys.exit(1)

# Bridge constants
I2C_BUS = 2
I2C_ADDR = 0x28
CMD_TRANSMIT = 0x01
CMD_DELAY = 0.010
POST_DELAY = 0.000126
SYNC_PATTERN = [0x10, 0x20, 0x30, 0x40, 0x50, 0xAA, 0x55, 0x00, 0xFF]


def i2c_write(bus, data):
    if len(data) > 32:
        raise ValueError(f"I2C write too large: {len(data)}")
    if len(data) < 2:
        bus.write_byte(I2C_ADDR, data[0])
    else:
        bus.write_i2c_block_data(I2C_ADDR, data[0], list(data[1:]))


def i2c_read_byte(bus):
    return bus.read_byte(I2C_ADDR)


def spi_command(bus, data):
    if len(data) > 31:
        raise ValueError(f"SPI too large: {len(data)}")
    time.sleep(CMD_DELAY)
    i2c_write(bus, [CMD_TRANSMIT] + list(data))
    time.sleep(POST_DELAY)
    return [i2c_read_byte(bus) for _ in range(len(data))]


def sync_buffer(bus):
    i2c_write(bus, [CMD_TRANSMIT, 0x80, 0x00])
    time.sleep(0.001)
    i2c_write(bus, [CMD_TRANSMIT, 0x8F, 0x00, 0x00])
    time.sleep(0.001)
    i2c_write(bus, [CMD_TRANSMIT, 0x0E, 0x00] + SYNC_PATTERN)
    time.sleep(0.001)
    i2c_write(bus, [CMD_TRANSMIT, 0x1E, 0x00, 0x00] + [0x00] * len(SYNC_PATTERN))
    time.sleep(0.001)
    seq_started = False
    seq_index = 0
    for count in range(256):
        d = i2c_read_byte(bus)
        if not seq_started:
            for i, v in enumerate(SYNC_PATTERN):
                if d == v:
                    seq_started, seq_index = True, i
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
                        seq_started, seq_index = True, i
                        break
    return False


def get_irq(bus):
    resp = spi_command(bus, [0x12, 0x00, 0x00, 0x00])
    return (resp[2] << 8) | resp[3]


def get_status(bus):
    resp = spi_command(bus, [0xC0, 0x00])
    return (resp[0] >> 4) & 0x07, (resp[0] >> 1) & 0x07


def read_register(bus, addr):
    resp = spi_command(bus, [0x1D, (addr >> 8) & 0xFF, addr & 0xFF, 0x00, 0x00])
    return resp[-1]


def init_rx(bus):
    """Minimal init to get into RX mode."""
    time.sleep(0.020)
    spi_command(bus, [0xC0, 0x00])
    time.sleep(0.010)
    spi_command(bus, [0x80, 0x00])
    spi_command(bus, [0x8A, 0x01])
    spi_command(bus, [0x9D, 0x01])
    spi_command(bus, [0x80, 0x00])
    spi_command(bus, [0x96, 0x01])
    spi_command(bus, [0x8F, 0x00, 0x7F])
    spi_command(bus, [0x95, 0x04, 0x07, 0x00, 0x01])
    spi_command(bus, [0x08, 0xFF, 0xFF, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00])
    spi_command(bus, [0x98, 0xE1, 0xE9])
    time.sleep(0.010)
    freq = int(915e6 / (32e6 / (1 << 25)))
    spi_command(bus, [0x86, (freq >> 24) & 0xFF, (freq >> 16) & 0xFF,
                      (freq >> 8) & 0xFF, freq & 0xFF])
    spi_command(bus, [0x8A, 0x01])
    spi_command(bus, [0x9F, 0x00])
    spi_command(bus, [0x8B, 0x07, 0x04, 0x01, 0x00])
    spi_command(bus, [0x8C, 0x00, 0x08, 0x00, 0x40, 0x01, 0x00])
    spi_command(bus, [0x02, 0xFF, 0xFF])
    spi_command(bus, [0x82, 0xFF, 0xFF, 0xFF])


def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    bus = smbus2.SMBus(I2C_BUS)

    print(f"=== I2C stress test ({duration}s) ===\n")

    # Sync + init
    print("sync...")
    if not sync_buffer(bus):
        print("FAIL: sync failed")
        return 1
    print("init radio...")
    init_rx(bus)
    mode, cmd = get_status(bus)
    print(f"status: mode={mode}, cmd={cmd}")
    if mode != 5:
        print(f"WARN: expected RX mode (5), got {mode}")
    print()

    # --- Stress loop ---
    # Simulate the modem's RX poll: get_irq every iteration, get_status
    # periodically, plus a register read to verify data integrity.
    polls = 0
    errors = 0
    recoveries = 0
    start = time.monotonic()
    last_report = start
    reg_mismatches = 0

    print(f"polling at ~{1/CMD_DELAY:.0f} Hz (irq + status + register read)...")
    print(f"{'time':>6s}  {'polls':>7s}  {'errors':>6s}  {'recov':>5s}  {'reg_err':>7s}")
    print(f"{'─'*6}  {'─'*7}  {'─'*6}  {'─'*5}  {'─'*7}")

    while time.monotonic() - start < duration:
        try:
            irq = get_irq(bus)
            mode, cmd = get_status(bus)

            # Periodic data integrity check: version register should be 0x53
            if polls % 50 == 0:
                ver = read_register(bus, 0x0320)
                if ver != 0x53:
                    reg_mismatches += 1

            # Re-enter RX if we somehow left it
            if mode != 5 and mode != 6:  # not RX and not TX
                spi_command(bus, [0x82, 0xFF, 0xFF, 0xFF])

            polls += 1

        except OSError as e:
            errors += 1
            err_time = time.monotonic() - start
            print(f"  OSError at {err_time:.1f}s (poll #{polls}): {e}")

            # Try to recover: re-sync
            try:
                time.sleep(0.050)
                if sync_buffer(bus):
                    init_rx(bus)
                    recoveries += 1
                    print(f"  recovered (re-sync + init)")
                else:
                    print(f"  re-sync FAILED")
                    break
            except OSError as e2:
                print(f"  recovery failed: {e2}")
                break

        # Progress report every 5s
        now = time.monotonic()
        if now - last_report >= 5.0:
            elapsed = now - start
            rate = polls / elapsed if elapsed > 0 else 0
            print(f"{elapsed:5.0f}s  {polls:7d}  {errors:6d}  {recoveries:5d}  {reg_mismatches:7d}  "
                  f"({rate:.0f}/s)")
            last_report = now

    elapsed = time.monotonic() - start
    rate = polls / elapsed if elapsed > 0 else 0

    print()
    print(f"done: {polls} polls in {elapsed:.1f}s ({rate:.0f}/s)")
    print(f"errors: {errors}, recoveries: {recoveries}, reg mismatches: {reg_mismatches}")

    bus.close()
    if errors > 0 and recoveries < errors:
        print("FAIL: unrecovered errors")
        return 1
    if reg_mismatches > 0:
        print("FAIL: register data corruption")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
