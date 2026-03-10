#!/usr/bin/env python3
"""Live LoRa receiver: capture from Pluto and print decoded packets.

Captures IQ in 6-second windows (enough for at least one 5s-interval
beacon packet) and runs the demodulator on each window. Prints any
decoded payload as it arrives. Ctrl-C to stop.

Usage:
    ./run tools/listen.py
"""

import sys
import time
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
params = LoRaParams(sf=7, bw=125e3, cr=1, fs=1e6)


def main():
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

    print(f"Listening @ {FREQ_HZ/1e6:.0f} MHz | SF{params.sf} BW{params.bw/1e3:.0f}k "
          f"CR4/{params.cr+4} | {WINDOW_S}s windows")
    print("Ctrl-C to stop\n", flush=True)

    pkt_num = 0

    try:
        while True:
            # Capture one window
            chunks = []
            for _ in range(n_reads):
                chunks.append(sdr.rx().astype(np.complex64))
            iq = np.concatenate(chunks)

            # Demodulate
            results = demodulate(iq, params, verbose=False)

            # Print any decoded payloads
            for r in results:
                if "payload" not in r:
                    continue
                pkt_num += 1
                payload = r["payload"]
                cfo = r.get("cfo", "?")
                crc = r.get("crc_ok")

                try:
                    text = payload.decode("ascii", errors="replace")
                except Exception:
                    text = payload.hex()

                crc_tag = ""
                if crc is True:
                    crc_tag = " [CRC OK]"
                elif crc is False:
                    crc_tag = " [CRC FAIL]"

                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] #{pkt_num}  \"{text}\"  "
                      f"({len(payload)} bytes, CFO={cfo}){crc_tag}",
                      flush=True)

    except KeyboardInterrupt:
        print(f"\nStopped. {pkt_num} packet(s) received.")


if __name__ == "__main__":
    main()
