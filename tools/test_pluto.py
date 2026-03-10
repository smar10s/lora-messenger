#!/usr/bin/env python3
"""Capture IQ from Pluto SDR at 915 MHz and detect LoRa TX bursts."""

import sys
import numpy as np
import adi

# --- Config ---
FREQ_HZ     = 915_000_000  # match LoRa TX
SAMPLE_RATE = 1_000_000    # 1 MSPS
RX_BW       = 1_000_000
RX_GAIN     = 73            # max gain -- needed for small antenna coupling
BUF_SIZE    = 2**14         # ~16ms per capture
DURATION_S  = 15            # how long to listen
THRESHOLD   = 15            # dB above median = "burst detected"


def main():
    sdr = adi.Pluto("usb:")
    sdr.rx_lo = FREQ_HZ
    sdr.sample_rate = SAMPLE_RATE
    sdr.rx_rf_bandwidth = RX_BW
    sdr.gain_control_mode_chan0 = "manual"
    sdr.rx_hardwaregain_chan0 = RX_GAIN
    sdr.rx_buffer_size = BUF_SIZE

    # flush
    for _ in range(5):
        sdr.rx()

    capture_time_ms = BUF_SIZE / SAMPLE_RATE * 1000
    n_captures = int(DURATION_S / (BUF_SIZE / SAMPLE_RATE))

    print(f"Pluto RX @ {FREQ_HZ/1e6:.0f} MHz | gain {RX_GAIN} dB | {capture_time_ms:.0f}ms captures")
    print(f"Listening for {DURATION_S}s ({n_captures} captures)...\n")

    powers_db = []
    for _ in range(n_captures):
        iq = sdr.rx().astype(np.complex64)
        pwr = 10 * np.log10(np.mean(np.abs(iq) ** 2) + 1e-20)
        powers_db.append(pwr)

    powers_db = np.array(powers_db)
    noise_floor = np.median(powers_db)
    bursts = powers_db > (noise_floor + THRESHOLD)
    n_bursts = np.sum(bursts)

    print(f"Noise floor:  {noise_floor:+.1f} dBFS")
    print(f"Peak:         {np.max(powers_db):+.1f} dBFS")
    print(f"Dynamic range: {np.max(powers_db) - noise_floor:.1f} dB")
    print(f"Bursts:       {n_bursts} (>{THRESHOLD} dB above noise)\n")

    if n_bursts > 0:
        print("SIGNAL DETECTED -- LoRa TX bursts visible.")
        # Show burst timestamps
        burst_indices = np.where(bursts)[0]
        for idx in burst_indices:
            t = idx * BUF_SIZE / SAMPLE_RATE
            print(f"  t={t:5.2f}s  power={powers_db[idx]:+.1f} dBFS  (+{powers_db[idx]-noise_floor:.0f} dB)")
    else:
        print("NO SIGNAL -- check antenna, distance, and that LoRa TX is running.")

    sys.exit(0 if n_bursts > 0 else 1)


if __name__ == "__main__":
    main()
