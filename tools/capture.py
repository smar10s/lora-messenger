#!/usr/bin/env python3
"""Capture raw IQ from Pluto SDR and save to .npy file.

Captures enough data to contain at least one full LoRa packet.
The beacon sends "Hello" every 5s, so 10s of capture guarantees at least one.
"""

import sys
import numpy as np
import adi
import time

# --- Config (must match TX / test_pluto.py) ---
FREQ_HZ     = 915_000_000
SAMPLE_RATE = 1_000_000   # 1 MSPS -- plenty for 125 kHz BW LoRa
RX_BW       = 1_000_000
RX_GAIN     = 73           # max gain
BUF_SIZE    = 2**16        # 65536 samples per read (~65ms)
DURATION_S  = 10           # capture window


def main():
    out_file = sys.argv[1] if len(sys.argv) > 1 else "capture.npy"

    # --- Setup SDR ---
    sdr = adi.Pluto("usb:")
    sdr.rx_lo       = FREQ_HZ
    sdr.sample_rate = SAMPLE_RATE
    sdr.rx_rf_bandwidth = RX_BW
    sdr.gain_control_mode_chan0 = "manual"
    sdr.rx_hardwaregain_chan0 = RX_GAIN
    sdr.rx_buffer_size = BUF_SIZE

    # Flush stale buffer
    for _ in range(5):
        sdr.rx()

    n_reads = int(DURATION_S * SAMPLE_RATE / BUF_SIZE)
    print(f"Capturing {DURATION_S}s @ {SAMPLE_RATE/1e6:.1f} MSPS ({n_reads} reads of {BUF_SIZE} samples)")

    chunks = []
    t0 = time.time()
    for i in range(n_reads):
        iq = sdr.rx().astype(np.complex64)
        chunks.append(iq)
        if (i + 1) % 20 == 0:
            elapsed = time.time() - t0
            print(f"  {i+1}/{n_reads} reads ({elapsed:.1f}s elapsed)")

    elapsed = time.time() - t0
    iq_all = np.concatenate(chunks)
    print(f"Done: {len(iq_all)} samples ({elapsed:.1f}s wall clock)")
    print(f"Saving to {out_file}")
    np.save(out_file, iq_all)
    print(f"Saved: {out_file} ({iq_all.nbytes / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
