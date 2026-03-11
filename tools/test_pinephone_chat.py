#!/usr/bin/env python3
"""Headless chat test — exercises the full chat protocol stack over PinePhoneModem.

Sends a properly formatted chat message (with protocol framing), then listens
for incoming messages and decodes them. This tests the same code path as
chat.py without needing a terminal.

Run on PinePhone. RAK should have LoRaMessenger firmware with chat.py running.
"""
import sys
import time
sys.path.insert(0, ".")

from modem.pinephone import PinePhoneModem
from protocol import CMD_MSG, pack_message, unpack_message

received = []

def on_rx(pkt):
    """Same decode path as chat.py."""
    try:
        cmd, payload = unpack_message(pkt.payload)
        text = payload.decode("utf-8", errors="replace")
        print(f"  RX [cmd={cmd}]: {text!r}  (rssi={pkt.rssi} snr={pkt.snr})")
    except Exception:
        print(f"  RX raw: {pkt.payload.hex()}  (rssi={pkt.rssi} snr={pkt.snr})")
    received.append(pkt)

def on_status(msg):
    print(f"  [{msg}]")

def main():
    modem = PinePhoneModem()
    modem.set_receive_callback(on_rx)
    modem.set_status_callback(on_status)

    print("starting modem...")
    modem.start()

    for _ in range(50):
        if modem.connected:
            break
        time.sleep(0.1)
    else:
        print("FAIL: not connected")
        modem.stop()
        return 1

    print()

    # Send a chat message using protocol framing
    uid = 0x42
    seq = 1
    dedup = (uid << 8) | seq
    ttl = 3
    payload = pack_message(CMD_MSG, b"hello from pinephone!")
    print(f"TX: 'hello from pinephone!' (dedup=0x{dedup:04x})")
    modem.send(ttl, dedup, payload)
    time.sleep(1)

    seq += 1
    dedup = (uid << 8) | seq
    payload = pack_message(CMD_MSG, b"second msg")
    print(f"TX: 'second msg' (dedup=0x{dedup:04x})")
    modem.send(ttl, dedup, payload)
    time.sleep(1)

    # Listen
    print(f"\nlistening 20s for RAK messages...")
    time.sleep(20)

    modem.stop()
    print(f"\ndone: sent 2, received {len(received)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
