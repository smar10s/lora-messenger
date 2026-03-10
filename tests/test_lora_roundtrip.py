#!/usr/bin/env python3
"""Round-trip test: modulate -> demodulate, no hardware.

Verifies the encode/decode chain is correct by generating a
perfect LoRa IQ waveform and feeding it to the demodulator.

Run via pytest (parametrized) or standalone:
    pytest tests/test_lora_roundtrip.py
    ./run tests/test_lora_roundtrip.py           # sweep 1-128 bytes
    ./run tests/test_lora_roundtrip.py "Hello"    # test specific payload
"""

import sys
import pytest
import numpy as np
from lora.mod import modulate
from lora.demod import LoRaParams, demodulate


PARAMS = LoRaParams(sf=7, bw=125e3, cr=1, fs=1e6)


# ---------------------------------------------------------------------------
# pytest tests (discovered automatically by pytest)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size", range(1, 129))
def test_roundtrip_size(size):
    """Modulate -> demodulate for a given payload size."""
    payload = bytes([i % 256 for i in range(size)])
    iq = modulate(payload, PARAMS)
    results = demodulate(iq, PARAMS, verbose=False)
    assert any(
        r.get("crc_ok") and r.get("payload") == payload for r in results
    ), f"Round-trip failed for {size}-byte payload"


def test_roundtrip_ascii():
    """Round-trip a human-readable ASCII string."""
    payload = b"Hello, LoRa!"
    iq = modulate(payload, PARAMS)
    results = demodulate(iq, PARAMS, verbose=False)
    decoded = [r for r in results if r.get("crc_ok") and r.get("payload") == payload]
    assert decoded, f"Failed to decode '{payload.decode()}'"


# ---------------------------------------------------------------------------
# Standalone CLI (./run tests/test_lora_roundtrip.py)
# ---------------------------------------------------------------------------

def main():
    params = PARAMS

    if len(sys.argv) > 1:
        payload = sys.argv[1].encode()
        print(f"Encoding: \"{payload.decode()}\"")
        print(f"Params: SF={params.sf}, BW={params.bw/1e3:.0f}kHz, "
              f"CR=4/{params.cr+4}, Fs={params.fs/1e6:.0f}MHz")

        iq = modulate(payload, params)
        print(f"Generated {len(iq)} samples ({len(iq)/params.fs*1000:.1f} ms)\n")

        results = demodulate(iq, params)

        print("\n" + "=" * 50)
        success = False
        for r in results:
            if "payload" in r:
                text = r["payload"].decode("ascii", errors="replace")
                crc_str = f" CRC={'OK' if r.get('crc_ok') else 'FAIL'}" if "crc_ok" in r else ""
                if r["payload"] == payload and r.get("crc_ok", False):
                    print(f"PASS: \"{text}\"{crc_str}")
                    success = True
                else:
                    print(f"FAIL: \"{text}\"{crc_str} (expected \"{payload.decode()}\")")
            else:
                print(f"ERROR: {r.get('error', '?')}")

        if not success:
            print("\nRound-trip FAILED -- this is a mod/demod bug, not an RF issue.")
        return 0 if success else 1

    else:
        print(f"Round-trip sweep: 1-128 bytes")
        print(f"Params: SF={params.sf}, BW={params.bw/1e3:.0f}kHz, "
              f"CR=4/{params.cr+4}, Fs={params.fs/1e6:.0f}MHz\n")

        passed = 0
        failed = 0
        failures = []

        for size in range(1, 129):
            payload = bytes([i % 256 for i in range(size)])
            iq = modulate(payload, params)
            results = demodulate(iq, params, verbose=False)
            ok = any(r.get("crc_ok") and r.get("payload") == payload for r in results)

            if ok:
                passed += 1
                print(f"  {size:3d}B: PASS")
            else:
                failed += 1
                failures.append(size)
                print(f"  {size:3d}B: FAIL")

        print(f"\n{'=' * 50}")
        print(f"{passed}/{passed + failed} passed")
        if failures:
            print(f"Failures: {failures}")
            print("\nRound-trip FAILED for some sizes.")
        else:
            print("All sizes pass.")
        return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
