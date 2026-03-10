#!/usr/bin/env python3
"""Bidirectional PlutoModem test — verify RX and TX against RAK beacons.

Usage:
    ./run tests/test_modem_roundtrip.py

Expects: both RAK beacons running LoRaMessenger firmware.

Sends multiple packets in each direction to account for the PlutoModem's
windowed demodulator (single packets may fall between demod windows).
"""

import sys
import os
import time
import random
import threading

import pytest

from modem.sdr import PlutoModem
from modem.rak import RAKModem
from modem.base import RxPacket

pytestmark = pytest.mark.skipif(True, reason="requires ADALM-Pluto SDR and RAK hardware")

pluto_received: list[RxPacket] = []
rak_received: list[RxPacket] = []


def on_pluto_rx(pkt: RxPacket):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] PLUTO RX: ttl={pkt.ttl} dedup=0x{pkt.dedup:04x} "
          f"({len(pkt.payload)} bytes) payload={pkt.payload.hex()}")
    pluto_received.append(pkt)


def on_rak_rx(pkt: RxPacket):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] RAK RX: ttl={pkt.ttl} dedup=0x{pkt.dedup:04x} "
          f"rssi={pkt.rssi} snr={pkt.snr} ({len(pkt.payload)} bytes)")
    rak_received.append(pkt)


def main():
    # --- Setup Pluto ---
    print("Setting up PlutoModem...")
    pluto = PlutoModem()
    pluto.set_receive_callback(on_pluto_rx)
    pluto.set_status_callback(lambda msg: print(f"[pluto] {msg}"))
    pluto.start()

    for _ in range(30):
        if pluto.connected:
            break
        time.sleep(0.5)
    else:
        print("ERROR: Pluto did not connect")
        pluto.stop()
        return 1

    # --- Setup RAK beacon ---
    rak_port = "/dev/cu.usbmodem101"
    print(f"Setting up RAK beacon on {rak_port}...")
    rak = RAKModem(rak_port)
    rak.set_receive_callback(on_rak_rx)
    rak.set_status_callback(lambda msg: print(f"[rak] {msg}"))
    rak.start()

    for _ in range(10):
        if rak.connected:
            break
        time.sleep(0.5)
    else:
        print("ERROR: RAK did not connect")
        pluto.stop()
        rak.stop()
        return 1

    print("\nWaiting 3s for RX loops to settle...")
    time.sleep(3)

    # Use a random uid byte for dedup tokens in this test
    test_uid = random.randint(0, 255)

    # --- Test 1: RAK -> Pluto ---
    print("\n=== TEST 1: RAK -> Pluto (5 packets, 2s apart) ===")
    for i in range(5):
        dedup = (test_uid << 8) | (i & 0xFF)
        rak.send(3, dedup, bytes([0x0A]) + f"R2P-{i}".encode())
        print(f"  RAK TX #{i}: dedup=0x{dedup:04x}")
        time.sleep(2)
        if pluto_received:
            break

    # Extra wait for demod pipeline
    deadline = time.time() + 5
    while time.time() < deadline and not pluto_received:
        time.sleep(0.5)

    if pluto_received:
        pkt = pluto_received[0]
        print(f"SUCCESS: Pluto received {len(pluto_received)} packet(s), "
              f"first: ttl={pkt.ttl} dedup=0x{pkt.dedup:04x} payload={pkt.payload!r}")
    else:
        print("FAIL: Pluto did not receive any packets")

    # --- Test 2: Pluto -> RAK (3 packets, 3s apart) ---
    print("\n=== TEST 2: Pluto -> RAK (3 packets, 3s apart) ===")
    for i in range(3):
        dedup = (test_uid << 8) | ((i + 100) & 0xFF)
        pluto.send(3, dedup, bytes([0x1F]) + f"P2R-{i}".encode())
        print(f"  Pluto TX #{i}: dedup=0x{dedup:04x}")
        time.sleep(3)
        if rak_received:
            break

    deadline = time.time() + 3
    while time.time() < deadline and not rak_received:
        time.sleep(0.5)

    if rak_received:
        pkt = rak_received[0]
        print(f"SUCCESS: RAK received {len(rak_received)} packet(s), "
              f"first: ttl={pkt.ttl} dedup=0x{pkt.dedup:04x} rssi={pkt.rssi} snr={pkt.snr}")
    else:
        print("FAIL: RAK did not receive any packets")

    # --- Summary ---
    print(f"\n{'=' * 40}")
    print(f"RAK -> Pluto: {'PASS' if pluto_received else 'FAIL'} "
          f"({len(pluto_received)} received)")
    print(f"Pluto -> RAK: {'PASS' if rak_received else 'FAIL'} "
          f"({len(rak_received)} received)")

    pluto.stop()
    rak.stop()
    return 0 if (pluto_received and rak_received) else 1


if __name__ == "__main__":
    sys.exit(main())
