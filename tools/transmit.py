#!/usr/bin/env python3
"""Transmit a LoRa packet from Pluto SDR.

Generates a LoRa IQ waveform and transmits it once (or repeatedly).

Usage:
    ./run tools/transmit.py                    # send "Hello" once
    ./run tools/transmit.py "test message"     # send custom payload once
    ./run tools/transmit.py "Hello" --repeat 5 # send 5 times, 2s apart
"""

import sys
import time
import argparse
import numpy as np
import adi
from lora.mod import modulate
from lora.demod import LoRaParams

# --- Config ---
FREQ_HZ     = 915_000_000
SAMPLE_RATE = 1_000_000
TX_BW       = 1_000_000
TX_ATTEN    = -10          # TX attenuation in dB (0 = max power, -89 = min)
BUF_SIZE    = 2**16

def main():
    parser = argparse.ArgumentParser(description="Transmit LoRa packet from Pluto SDR")
    parser.add_argument("payload", nargs="?", default="Hello", help="Payload string")
    parser.add_argument("--repeat", type=int, default=1, help="Number of transmissions")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between repeats")
    parser.add_argument("--attn", type=float, default=TX_ATTEN, help="TX attenuation (dB)")
    args = parser.parse_args()

    params = LoRaParams(sf=7, bw=125e3, cr=1, fs=1e6)
    payload = args.payload.encode()

    print(f"Generating LoRa packet: \"{args.payload}\" ({len(payload)} bytes)")
    print(f"Params: SF={params.sf}, BW={params.bw/1e3:.0f}kHz, CR=4/{params.cr+4}")

    iq = modulate(payload, params)

    # Scale to Pluto's 16-bit DAC range. The IQ from modulate() is unit-amplitude
    # complex64. Pluto expects int16 samples scaled to ~2**14.
    scale = 2**14
    iq_tx = (iq * scale).astype(np.complex64)
    print(f"Waveform: {len(iq_tx)} samples ({len(iq_tx)/params.fs*1000:.1f} ms)")

    # --- Setup SDR ---
    print("Connecting to Pluto SDR...", end=" ", flush=True)
    sdr = adi.Pluto("usb:")
    sdr.tx_lo              = FREQ_HZ
    sdr.sample_rate        = SAMPLE_RATE
    sdr.tx_rf_bandwidth    = TX_BW
    sdr.tx_hardwaregain_chan0 = args.attn
    # Enable cyclic mode for clean single-burst TX
    sdr.tx_cyclic_buffer   = False
    print("ok")

    print(f"TX @ {FREQ_HZ/1e6:.0f} MHz, attn={args.attn} dB")
    print(f"Transmitting {args.repeat}x, {args.interval}s interval\n")

    for i in range(args.repeat):
        sdr.tx(iq_tx)
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] TX #{i+1}/{args.repeat}", flush=True)
        if i < args.repeat - 1:
            time.sleep(args.interval)

    # Disable TX (send zeros to stop)
    sdr.tx_destroy_buffer()
    print("\nDone.")

if __name__ == "__main__":
    main()
