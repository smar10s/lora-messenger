#!/usr/bin/env python3
"""Test LoRa demodulator: decode "Hello" from captured IQ.

Usage:
    ./run tests/test_demod.py capture.npy
"""

import sys
import pytest
import numpy as np
from lora.demod import LoRaParams, demodulate

pytestmark = pytest.mark.skipif(True, reason="requires IQ capture file (hardware test)")


def main():
    capture = sys.argv[1] if len(sys.argv) > 1 else "capture.npy"
    print(f"Loading {capture}...")
    iq = np.load(capture)
    print(f"{len(iq)} samples ({len(iq)/1e6:.1f}M), {iq.dtype}\n")

    params = LoRaParams(sf=7, bw=125e3, cr=1, fs=1e6)
    results = demodulate(iq, params)

    print("\n" + "=" * 50)
    success = False
    for r in results:
        if "payload" in r:
            text = r["payload"].decode("ascii", errors="replace")
            if text == "Hello":
                print(f"SUCCESS: decoded \"{text}\"")
                success = True
            else:
                print(f"DECODED: \"{text}\" (expected \"Hello\")")
        else:
            print(f"ERROR: {r.get('error', '?')}")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
