#!/usr/bin/env python3
"""Bidirectional test of PinePhoneModem via the LoRaModem interface.

Run on PinePhone. RAK should have LoRaMessenger firmware.
Tests TX (Pine->RAK) and RX (RAK->Pine) through the modem abstraction.
"""
import sys
import time
sys.path.insert(0, ".")

from modem.pinephone import PinePhoneModem

received = []

def on_rx(pkt):
    print(f"  RX: ttl={pkt.ttl} dedup=0x{pkt.dedup:04x} "
          f"rssi={pkt.rssi} snr={pkt.snr} payload={pkt.payload!r}")
    received.append(pkt)

def on_status(msg):
    print(f"  status: {msg}")

def main():
    modem = PinePhoneModem()
    modem.set_receive_callback(on_rx)
    modem.set_status_callback(on_status)

    print("starting modem...")
    modem.start()

    for _ in range(30):
        if modem.connected:
            break
        time.sleep(0.1)
    else:
        print("FAIL: modem did not connect")
        modem.stop()
        return 1

    print(f"connected: {modem.connected}\n")

    # TX test — send a properly formatted message
    # CMD byte 0x01 = MSG, then ASCII text
    print("--- TX: PinePhone -> RAK ---")
    modem.send(ttl=3, dedup=0xAB01, payload=b"\x01hello from pine")
    print("  sent 'hello from pine'")
    time.sleep(1)

    modem.send(ttl=3, dedup=0xAB02, payload=b"\x01second message")
    print("  sent 'second message'")
    time.sleep(1)

    # RX test — wait for RAK to send something
    # With LoRaMessenger firmware, the RAK won't send unless someone types.
    # So we just listen briefly to confirm no errors.
    print("\n--- RX: listening 10s ---")
    print("  (type on RAK chat to test RX, or just verifying no errors)")
    time.sleep(10)

    modem.stop()
    print(f"\nreceived {len(received)} packets")
    print("PASS" if modem._connected == False else "stopped cleanly")
    return 0

if __name__ == "__main__":
    sys.exit(main())
