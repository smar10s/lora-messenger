#!/usr/bin/env python3
"""PinePhone I2C-SPI bridge transport test.

Tests the ATtiny84 I2C-to-SPI bridge in isolation. Implements JF's
SyncI2CBuffer to align the circular buffer, then exercises the transport
with progressively harder patterns: register roundtrips, buffer transfers,
rapid command sequences, alignment drift detection.

Treats the SX1262 as a generic SPI device with readable registers and a
256-byte data buffer. No radio configuration — this is purely about proving
the bridge is a reliable byte pipe.

Usage (on PinePhone):
    python3 tools/test_pinephone_sync.py
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

# Inter-command delay — JF uses 10ms (WaitOnBusy fake)
CMD_DELAY = 0.010
# Post-command delay — JF uses 126us (WaitOnCounter)
POST_DELAY = 0.000126

# SX1262 registers
REG_VERSION = 0x0320
REG_SYNCWORD_HI = 0x0740
REG_SYNCWORD_LO = 0x0741


def i2c_write(bus, data):
    """Raw I2C write to ATtiny. data includes CMD_TRANSMIT prefix.

    Raises ValueError if data exceeds the smbus2 block write limit (32 bytes).
    The kernel may silently truncate larger writes — we must prevent this.
    """
    if len(data) > 32:
        raise ValueError(
            f"I2C write too large: {len(data)} bytes (max 32). "
            f"SPI payload must be <= 31 bytes."
        )
    if len(data) < 2:
        bus.write_byte(I2C_ADDR, data[0])
    else:
        bus.write_i2c_block_data(I2C_ADDR, data[0], list(data[1:]))


def i2c_read_byte(bus):
    """Read a single byte from ATtiny circular buffer."""
    return bus.read_byte(I2C_ADDR)


def sync_buffer(bus):
    """Port of JF's SyncI2CBuffer — align ATtiny circular buffer pointers.

    Sends known SPI commands, writes a recognizable pattern to the SX1262
    buffer, issues a read, then scans the I2C output for the pattern.
    Once found, read and write pointers are aligned.
    """
    print("sync: sending setup commands...")

    # SetStandby(STDBY_RC)
    i2c_write(bus, [CMD_TRANSMIT, 0x80, 0x00])
    time.sleep(0.001)

    # SetBufferBaseAddress(tx=0, rx=0)
    i2c_write(bus, [CMD_TRANSMIT, 0x8F, 0x00, 0x00])
    time.sleep(0.001)

    # WriteBuffer(offset=0, pattern)
    print("sync: writing verification pattern...")
    pattern = [0x10, 0x20, 0x30, 0x40, 0x50, 0xAA, 0x55, 0x00, 0xFF]
    i2c_write(bus, [CMD_TRANSMIT, 0x0E, 0x00] + pattern)
    time.sleep(0.001)

    # ReadBuffer(offset=0) — pad with zeros for response
    # SPI response: [status, status, data0, data1, ..., data8] = 11 bytes
    # We send opcode + offset + NOP + 9 data NOPs = 12 bytes SPI
    i2c_write(bus, [CMD_TRANSMIT, 0x1E, 0x00, 0x00] + [0x00] * 9)
    time.sleep(0.001)

    # Now scan I2C reads for the known pattern
    print("sync: scanning for pattern...")
    seq_started = False
    seq_index = 0
    count = 0

    while count < 256:
        d = i2c_read_byte(bus)

        if not seq_started:
            # Look for any byte that matches somewhere in the pattern
            for i in range(len(pattern)):
                if d == pattern[i]:
                    seq_started = True
                    seq_index = i
                    break
        else:
            if seq_index + 1 < len(pattern) and d == pattern[seq_index + 1]:
                seq_index += 1
                if seq_index == len(pattern) - 1:
                    print(f"sync: aligned after {count + 1} bytes")
                    return True
            else:
                # Mismatch — restart
                seq_started = False
                # Check if this byte starts a new match
                for i in range(len(pattern)):
                    if d == pattern[i]:
                        seq_started = True
                        seq_index = i
                        break

        count += 1

    print(f"sync: FAILED — pattern not found in {count} bytes")
    return False


def spi_command(bus, data):
    """Send an SPI command through the bridge with proper timing.

    Waits CMD_DELAY before (WaitOnBusy equivalent), sends the command,
    waits POST_DELAY after (WaitOnCounter equivalent).

    The ATtiny clocks one response byte per SPI byte sent, so we read
    exactly len(data) bytes back. There is no "empty" sentinel — stale
    data recirculates if you read too many. Always read exactly as many
    bytes as the SPI transaction length.

    Max SPI payload: 31 bytes (smbus2 block limit is 32 including CMD_TRANSMIT).
    Raises ValueError if exceeded.

    Returns list of response bytes (same length as data).
    """
    if len(data) > 31:
        raise ValueError(
            f"SPI command too large: {len(data)} bytes (max 31). "
            f"Split into multiple transfers for larger payloads."
        )
    time.sleep(CMD_DELAY)
    i2c_write(bus, [CMD_TRANSMIT] + list(data))
    time.sleep(POST_DELAY)
    return [i2c_read_byte(bus) for _ in range(len(data))]


def read_register(bus, addr):
    """ReadRegister (opcode 0x1D). Response: [status, status, status, data]."""
    resp = spi_command(bus, [0x1D, (addr >> 8) & 0xFF, addr & 0xFF, 0x00, 0x00])
    return resp[-1]


def write_register(bus, addr, val):
    """WriteRegister (opcode 0x0D)."""
    spi_command(bus, [0x0D, (addr >> 8) & 0xFF, addr & 0xFF, val])


def get_status(bus):
    """GetStatus (opcode 0xC0). Returns (mode, cmd_status)."""
    resp = spi_command(bus, [0xC0, 0x00])
    status = resp[0]
    mode = (status >> 4) & 0x07
    cmd_status = (status >> 1) & 0x07
    return mode, cmd_status


STATUS_MODES = {
    2: "STDBY_RC", 3: "STDBY_XOSC", 4: "FS", 5: "RX", 6: "TX",
}
CMD_STATUSES = {
    1: "ok", 2: "data available", 3: "timeout",
    5: "processing error", 6: "exec failure",
}


def main():
    failed = False

    # --- Open I2C ---
    try:
        bus = smbus2.SMBus(I2C_BUS)
    except OSError as e:
        print(f"error: cannot open /dev/i2c-{I2C_BUS}: {e}")
        return 1

    print(f"I2C bus {I2C_BUS} opened, bridge at 0x{I2C_ADDR:02x}\n")

    # --- Step 1: Buffer sync ---
    print("--- step 1: buffer sync ---")
    if not sync_buffer(bus):
        print("FATAL: buffer sync failed, cannot continue")
        bus.close()
        return 1
    print()

    # --- Step 2: Read version register ---
    print("--- step 2: read version register ---")
    ver1 = read_register(bus, REG_VERSION)
    ver2 = read_register(bus, REG_VERSION)
    print(f"  read 1: 0x{ver1:02x}")
    print(f"  read 2: 0x{ver2:02x}")
    if ver1 in (0x00, 0xFF):
        print("  FAIL: no SX1262 response")
        failed = True
    elif ver1 != ver2:
        print(f"  FAIL: inconsistent reads")
        failed = True
    else:
        print(f"  PASS")
    print()

    # --- Step 3: Write register, read back ---
    print("--- step 3: register write/readback ---")
    orig_hi = read_register(bus, REG_SYNCWORD_HI)
    orig_lo = read_register(bus, REG_SYNCWORD_LO)
    print(f"  original sync word: 0x{orig_hi:02x}{orig_lo:02x}")

    test_hi, test_lo = 0xAA, 0x55
    write_register(bus, REG_SYNCWORD_HI, test_hi)
    write_register(bus, REG_SYNCWORD_LO, test_lo)
    got_hi = read_register(bus, REG_SYNCWORD_HI)
    got_lo = read_register(bus, REG_SYNCWORD_LO)
    print(f"  wrote 0x{test_hi:02x}{test_lo:02x}, read 0x{got_hi:02x}{got_lo:02x}")

    if got_hi != test_hi or got_lo != test_lo:
        print(f"  FAIL: readback mismatch")
        failed = True
    else:
        print(f"  PASS")

    # Restore
    write_register(bus, REG_SYNCWORD_HI, orig_hi)
    write_register(bus, REG_SYNCWORD_LO, orig_lo)
    print()

    # --- Step 4: GetStatus ---
    print("--- step 4: GetStatus ---")
    mode, cmd_st = get_status(bus)
    mode_str = STATUS_MODES.get(mode, f"unknown({mode})")
    cmd_str = CMD_STATUSES.get(cmd_st, f"unknown({cmd_st})")
    print(f"  mode={mode_str}, cmd={cmd_str}")
    if mode != 2:
        print(f"  FAIL: expected STDBY_RC")
        failed = True
    else:
        print(f"  PASS")
    print()

    # --- Step 5: SetStandby + verify ---
    print("--- step 5: SetStandby(RC) + verify ---")
    spi_command(bus, [0x80, 0x00])  # SetStandby STDBY_RC
    mode, cmd_st = get_status(bus)
    mode_str = STATUS_MODES.get(mode, f"unknown({mode})")
    cmd_str = CMD_STATUSES.get(cmd_st, f"unknown({cmd_st})")
    print(f"  mode={mode_str}, cmd={cmd_str}")
    if mode != 2:
        print(f"  FAIL: expected STDBY_RC after SetStandby")
        failed = True
    else:
        print(f"  PASS")
    print()

    # --- Step 6: Multiple register roundtrips ---
    print("--- step 6: multiple register roundtrips ---")
    test_values = [0x00, 0xFF, 0x42, 0xDE, 0xAD]
    all_ok = True
    for val in test_values:
        write_register(bus, REG_SYNCWORD_HI, val)
        got = read_register(bus, REG_SYNCWORD_HI)
        ok = "ok" if got == val else "MISMATCH"
        print(f"  write 0x{val:02x}, read 0x{got:02x} — {ok}")
        if got != val:
            all_ok = False

    if all_ok:
        print(f"  PASS: {len(test_values)}/{len(test_values)} roundtrips")
    else:
        print(f"  FAIL: some roundtrips failed")
        failed = True

    # Restore default private sync word
    write_register(bus, REG_SYNCWORD_HI, 0x14)
    write_register(bus, REG_SYNCWORD_LO, 0x24)
    print()

    # --- Step 7: Buffer write/readback — various sizes ---
    print("--- step 7: buffer write/readback (various sizes) ---")
    # smbus2 block limit: 32 I2C bytes = 31 SPI bytes max
    # WriteBuffer: [0x0E, offset] + data -> max 29 data bytes
    # ReadBuffer:  [0x1E, offset, NOP] + data -> max 28 data bytes
    # The read is the bottleneck.
    for size in [1, 8, 16, 28]:
        pattern = bytes([(i * 37 + size) & 0xFF for i in range(size)])
        spi_command(bus, [0x8F, 0x00, 0x00])  # SetBufferBaseAddress(0, 0)
        spi_command(bus, [0x0E, 0x00] + list(pattern))  # WriteBuffer
        resp = spi_command(bus, [0x1E, 0x00, 0x00] + [0x00] * size)
        readback = bytes(resp[3:])
        if readback == pattern:
            print(f"  {size:2d} bytes: PASS")
        else:
            print(f"  {size:2d} bytes: FAIL")
            print(f"    wrote: {pattern.hex()}")
            print(f"    read:  {readback.hex()}")
            failed = True
    print()

    # --- Step 8: Rapid-fire register roundtrips ---
    print("--- step 8: rapid-fire writes (50 roundtrips) ---")
    mismatches = 0
    for i in range(50):
        val = (i * 73 + 17) & 0xFF
        write_register(bus, REG_SYNCWORD_HI, val)
        got = read_register(bus, REG_SYNCWORD_HI)
        if got != val:
            if mismatches < 3:
                print(f"  #{i}: wrote 0x{val:02x}, read 0x{got:02x}")
            mismatches += 1
    if mismatches == 0:
        print(f"  PASS: 50/50")
    else:
        print(f"  FAIL: {mismatches}/50 mismatches")
        failed = True
    print()

    # --- Step 9: Overflow — write without reading ---
    # The ATtiny has a 128-byte circular response buffer. Each SPI command
    # produces N response bytes (one per SPI byte clocked). If we send
    # commands without reading responses, the write pointer advances and
    # eventually wraps. What happens?
    #
    # We'll send a known number of commands, skip all reads, then try to
    # recover and verify the transport is still usable.
    print("--- step 9: overflow — write without reading ---")

    # First, establish a known register value while transport is good
    write_register(bus, REG_SYNCWORD_HI, 0xBE)
    got = read_register(bus, REG_SYNCWORD_HI)
    assert got == 0xBE, f"pre-overflow sanity check failed: 0x{got:02x}"
    print(f"  pre-overflow: register = 0x{got:02x} (good)")

    # Now flood: send SPI commands, don't read any responses.
    # Each GetStatus is 2 SPI bytes -> 2 response bytes in the ATtiny buffer.
    # 128 / 2 = 64 commands to fill it. Send 80 to guarantee overflow.
    print(f"  flooding 80 GetStatus commands (160 response bytes, buffer=128)...")
    for i in range(80):
        i2c_write(bus, [CMD_TRANSMIT, 0xC0, 0x00])
        time.sleep(0.001)  # let the ATtiny process

    # The buffer has overflowed. Read pointer is now stale.
    # Try reading — what do we get?
    print(f"  reading 20 bytes from overflowed buffer:")
    overflow_bytes = []
    for i in range(20):
        b = i2c_read_byte(bus)
        overflow_bytes.append(b)
    print(f"    {' '.join(f'{b:02x}' for b in overflow_bytes)}")

    # Now: can we recover by re-syncing?
    print(f"  re-syncing after overflow...")
    if not sync_buffer(bus):
        print(f"  FAIL: re-sync failed after overflow")
        failed = True
    else:
        # Verify transport works after re-sync
        got = read_register(bus, REG_SYNCWORD_HI)
        if got == 0xBE:
            print(f"  PASS: re-sync recovered, register still 0x{got:02x}")
        else:
            print(f"  FAIL: re-sync seemed ok but register = 0x{got:02x} (expected 0xBE)")
            failed = True
    print()

    # --- Step 10: Read from empty buffer ---
    # What does the ATtiny return when we read with nothing pending?
    # This tells us what a "stale" read looks like so we can detect it.
    print("--- step 10: read from empty buffer ---")
    # Do one normal command and drain its response to empty the buffer
    resp = spi_command(bus, [0xC0, 0x00])  # GetStatus, 2 response bytes consumed
    print(f"  GetStatus response: [{resp[0]:02x} {resp[1]:02x}]")

    # Now read 10 more bytes — there should be nothing pending
    empty_bytes = []
    for i in range(10):
        b = i2c_read_byte(bus)
        empty_bytes.append(b)
    print(f"  10 reads from empty buffer: {' '.join(f'{b:02x}' for b in empty_bytes)}")

    # Check if they're all the same (likely 0x00 or 0xFF — whatever the
    # ATtiny returns when read pointer == write pointer)
    unique = set(empty_bytes)
    if len(unique) == 1:
        print(f"  empty buffer returns: 0x{empty_bytes[0]:02x} (consistent)")
    else:
        print(f"  empty buffer returns mixed values: {unique}")

    # The critical question: did those empty reads desync us?
    # Verify with a known register read.
    print(f"  re-syncing after empty reads...")
    if not sync_buffer(bus):
        print(f"  FAIL: re-sync failed after empty reads")
        failed = True
    else:
        got = read_register(bus, REG_SYNCWORD_HI)
        if got == 0xBE:
            print(f"  PASS: recovered, register = 0x{got:02x}")
        else:
            print(f"  FAIL: register = 0x{got:02x} (expected 0xBE)")
            failed = True
    print()

    # --- Step 11: spi_command size guard ---
    # The kernel silently accepts writes > 32 bytes (possible truncation).
    # Our i2c_write guard must catch this before it reaches the kernel.
    print("--- step 11: spi_command size guard ---")
    spi_command(bus, [0x8F, 0x00, 0x00])  # SetBufferBaseAddress
    # Max write: 29 data bytes (opcode + offset + 29 = 31 SPI bytes)
    big_pattern = bytes(range(29))
    spi_command(bus, [0x0E, 0x00] + list(big_pattern))
    # Max read: 28 data bytes (opcode + offset + NOP + 28 = 31 SPI bytes)
    # Read back first 28 of the 29 we wrote
    resp = spi_command(bus, [0x1E, 0x00, 0x00] + [0x00] * 28)
    readback = bytes(resp[3:])
    if readback == big_pattern[:28]:
        print(f"  31 SPI bytes (max): PASS")
    else:
        print(f"  31 SPI bytes (max): FAIL")
        failed = True

    # 32 SPI bytes should be caught by our guard
    try:
        spi_command(bus, [0x0E, 0x00] + list(range(30)))  # 32 SPI bytes
        print(f"  32 SPI bytes: no error raised — FAIL (guard broken)")
        failed = True
    except ValueError:
        print(f"  32 SPI bytes: ValueError — PASS (guard works)")
    print()

    # --- Step 12: Verify transport is still clean after all abuse ---
    print("--- step 12: final transport verification ---")
    # Re-sync one last time, then do a definitive register roundtrip
    if not sync_buffer(bus):
        print(f"  FAIL: final re-sync failed")
        failed = True
    else:
        test_val = 0x77
        write_register(bus, REG_SYNCWORD_HI, test_val)
        got = read_register(bus, REG_SYNCWORD_HI)
        if got == test_val:
            print(f"  PASS: register roundtrip clean after all tests")
        else:
            print(f"  FAIL: register = 0x{got:02x} (expected 0x{test_val:02x})")
            failed = True

    # Restore defaults
    write_register(bus, REG_SYNCWORD_HI, 0x14)
    write_register(bus, REG_SYNCWORD_LO, 0x24)

    # --- Summary ---
    bus.close()
    if failed:
        print("\nRESULT: FAIL")
        return 1
    print("\nRESULT: PASS — transport layer verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
