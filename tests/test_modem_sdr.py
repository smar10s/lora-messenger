#!/usr/bin/env python3
"""Standalone PlutoModem test — verify RX and TX against RAK beacons.

Usage:
    ./run tests/test_modem_sdr.py

Expects: at least one RAK beacon running LoRaMessenger firmware.
         A beacon chat TUI (or the RAK sending periodic messages) on the
         other end so there's traffic to receive.

What it does:
    1. Connect to Pluto SDR via PlutoModem
    2. Listen for packets, print them as they arrive
    3. Press Enter to send a test message (visible on beacon's chat TUI)
    4. Ctrl-C to stop
"""

import sys
import os
import time
import random
import threading

import pytest

from modem.sdr import PlutoModem
from modem.base import RxPacket

pytestmark = pytest.mark.skipif(True, reason="requires ADALM-Pluto SDR hardware")

received: list[RxPacket] = []
rx_event = threading.Event()
tx_count = 0
test_uid = random.randint(0, 255)


def on_rx(pkt: RxPacket):
    ts = time.strftime("%H:%M:%S")
    print(f"\n[{ts}] RX: ttl={pkt.ttl} dedup=0x{pkt.dedup:04x} "
          f"({len(pkt.payload)} bytes) payload={pkt.payload.hex()}")
    try:
        text = pkt.payload.decode("utf-8", errors="replace")
        print(f"       text: \"{text}\"")
    except Exception:
        pass
    received.append(pkt)
    rx_event.set()


def main():
    global tx_count

    print("PlutoModem standalone test")
    print("=" * 40)

    modem = PlutoModem()
    modem.set_receive_callback(on_rx)
    modem.set_status_callback(lambda msg: print(f"[status] {msg}"))
    modem.start()

    print("Waiting for Pluto connection...")
    for _ in range(30):
        if modem.connected:
            break
        time.sleep(0.5)
    else:
        print("ERROR: could not connect to Pluto after 15s")
        modem.stop()
        return 1

    print("\nListening for packets.")
    print("Press Enter to send a test message, Ctrl-C to quit.\n")

    try:
        while True:
            try:
                input()
            except EOFError:
                break

            # Send a test message with CMD_MSG (0x00) as the app payload.
            tx_count += 1
            seq = (tx_count + 200) % 256
            dedup = (test_uid << 8) | seq
            test_payload = bytes([0x00]) + f"PlutoTest #{tx_count}".encode()
            print(f"TX: ttl=3 dedup=0x{dedup:04x} payload={test_payload.hex()}")
            modem.send(3, dedup, test_payload)
            print("Sent.")

    except KeyboardInterrupt:
        pass

    print(f"\nStopping. Received {len(received)} packet(s), sent {tx_count}.")
    modem.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
