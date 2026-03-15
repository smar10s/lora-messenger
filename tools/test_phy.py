#!/usr/bin/env python3
"""Live PHY test: RAK beacon -> Pluto SDR decode with statistics.

Expects a RAK device running the LoRaP2P_TX beacon firmware (sends
"Hello" every 5s). Captures IQ windows from Pluto and runs the
demodulator, tracking CRC pass/fail rates.

Usage:
    # First flash the RAK with the beacon:
    #   make flash SKETCH=examples/LoRaP2P_TX -C firmware
    #
    # Then run:
    ./run tools/test_phy.py           # default 60s
    ./run tools/test_phy.py -d 120    # 120 seconds
    ./run tools/test_phy.py -s        # save failing IQ captures
"""

import sys
import time
import argparse
import numpy as np
import adi
from lora.demod import LoRaParams, demodulate

# --- SDR config ---
FREQ_HZ     = 915_000_000
SAMPLE_RATE = 1_000_000
RX_BW       = 1_000_000
RX_GAIN     = 73
BUF_SIZE    = 2**16       # ~65ms per read
WINDOW_S    = 6           # capture window -- just over one TX interval

# --- LoRa config ---
PARAMS = LoRaParams(sf=7, bw=125e3, cr=1, fs=1e6)
EXPECTED_PAYLOAD = b"Hello"


def main():
    parser = argparse.ArgumentParser(description="Live PHY decode test")
    parser.add_argument("-d", "--duration", type=int, default=60,
                        help="Test duration in seconds (default: 60)")
    parser.add_argument("-s", "--save-fails", action="store_true",
                        help="Save IQ captures of failed decodes")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Print verbose demod output for failures")
    args = parser.parse_args()

    # --- Setup SDR ---
    print("Connecting to Pluto SDR...", end=" ", flush=True)
    sdr = adi.Pluto("usb:")
    sdr.rx_lo              = FREQ_HZ
    sdr.sample_rate        = SAMPLE_RATE
    sdr.rx_rf_bandwidth    = RX_BW
    sdr.gain_control_mode_chan0 = "manual"
    sdr.rx_hardwaregain_chan0   = RX_GAIN
    sdr.rx_buffer_size     = BUF_SIZE
    print("ok")

    # Flush stale buffers
    for _ in range(5):
        sdr.rx()

    n_reads = int(WINDOW_S * SAMPLE_RATE / BUF_SIZE)

    print(f"Live PHY test @ {FREQ_HZ/1e6:.0f} MHz | SF{PARAMS.sf} BW{PARAMS.bw/1e3:.0f}k "
          f"CR4/{PARAMS.cr+4}")
    print(f"Expected payload: \"{EXPECTED_PAYLOAD.decode()}\" ({len(EXPECTED_PAYLOAD)} bytes)")
    print(f"Duration: {args.duration}s | Window: {WINDOW_S}s")
    print(f"{'='*60}")
    print(f"{'Time':>8s}  {'#':>3s}  {'Payload':<20s}  {'CRC':>4s}  {'CFO':>8s}")
    print(f"{'='*60}")

    # Stats
    total_preambles = 0
    total_decoded = 0
    total_crc_ok = 0
    total_crc_fail = 0
    total_correct = 0  # CRC OK and payload matches
    total_windows = 0
    fail_captures = []

    t_start = time.time()

    try:
        while time.time() - t_start < args.duration:
            total_windows += 1

            # Capture one window
            chunks = []
            for _ in range(n_reads):
                chunks.append(sdr.rx().astype(np.complex64))
            iq = np.concatenate(chunks)

            # Demodulate
            results = demodulate(iq, PARAMS, verbose=False)

            # Count preambles (results includes both decoded and failed)
            total_preambles += len(results)

            for r in results:
                if "payload" not in r:
                    continue

                total_decoded += 1
                payload = r["payload"]
                crc_ok = r.get("crc_ok")
                cfo = r.get("cfo", "?")

                if crc_ok:
                    total_crc_ok += 1
                    if payload == EXPECTED_PAYLOAD:
                        total_correct += 1
                else:
                    total_crc_fail += 1

                try:
                    text = payload.decode("ascii", errors="replace")
                except Exception:
                    text = payload.hex()

                crc_tag = "OK" if crc_ok else "FAIL"
                cfo_str = f"{cfo:.2f}" if isinstance(cfo, float) else str(cfo)
                match_tag = "" if payload == EXPECTED_PAYLOAD else " (!)"
                ts = time.strftime("%H:%M:%S")
                total = total_crc_ok + total_crc_fail
                print(f"[{ts}]  {total:3d}  \"{text}\"{match_tag:<12s}  {crc_tag:>4s}  CFO={cfo_str:>6s}")

                # Save failing captures
                if not crc_ok and args.save_fails:
                    fname = f"fail_{total_crc_fail:03d}.npy"
                    np.save(fname, iq)
                    fail_captures.append(fname)
                    print(f"         -> saved {fname}")

                # Verbose demod on failures
                if not crc_ok and args.verbose:
                    print("         Verbose re-decode:")
                    demodulate(iq, PARAMS, verbose=True)

    except KeyboardInterrupt:
        print("\n--- Interrupted ---")

    elapsed = time.time() - t_start

    # --- Summary ---
    print(f"\n{'='*60}")
    print(f"PHY TEST SUMMARY")
    print(f"{'='*60}")
    print(f"Duration:        {elapsed:.1f}s")
    print(f"Windows:         {total_windows}")
    print(f"Preambles found: {total_preambles}")
    print(f"Packets decoded: {total_decoded}")
    print(f"CRC OK:          {total_crc_ok}")
    print(f"CRC FAIL:        {total_crc_fail}")
    print(f"Correct payload: {total_correct}")
    if total_decoded > 0:
        pct = total_crc_ok / total_decoded * 100
        print(f"CRC pass rate:   {total_crc_ok}/{total_decoded} ({pct:.1f}%)")
        if total_correct < total_crc_ok:
            print(f"  (Note: {total_crc_ok - total_correct} had CRC OK but wrong payload)")
    else:
        print("CRC pass rate:   N/A (no packets decoded)")

    if fail_captures:
        print(f"\nFailed captures saved: {fail_captures}")

    return 0 if total_crc_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
