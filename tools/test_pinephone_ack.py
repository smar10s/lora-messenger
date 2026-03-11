#!/usr/bin/env python3
"""Test: send a message requiring ACK, measure time to receive the ACK back.

Sends CMD_MSG_ACK_REQ, then polls RX for up to 5s looking for any packet.
Prints every packet received (with timing) to diagnose the ACK gap.

Run on PinePhone. RAK should have chat.py running with /ack mode or ready to ack.
"""
import sys
import time
sys.path.insert(0, ".")

from modem.pinephone import PinePhoneModem
from protocol import CMD_MSG_ACK_REQ, CMD_ACK, pack_message, unpack_message

def on_status(msg):
    print(f"  [{msg}]")

def main():
    modem = PinePhoneModem()
    modem.set_status_callback(on_status)

    # Don't use the normal callback — we'll poll the modem's internals
    # Actually, let's use the callback but with timestamps
    rx_log = []
    def on_rx(pkt):
        t = time.monotonic()
        rx_log.append((t, pkt))
        try:
            cmd, payload = unpack_message(pkt.payload)
            if cmd == CMD_ACK and len(payload) >= 2:
                acked = int.from_bytes(payload[:2], "big")
                print(f"  RX ACK for 0x{acked:04x} at +{t - tx_time:.3f}s "
                      f"(rssi={pkt.rssi} snr={pkt.snr})")
            else:
                text = payload.decode("utf-8", errors="replace")
                print(f"  RX cmd={cmd}: {text!r} at +{t - tx_time:.3f}s "
                      f"(rssi={pkt.rssi} snr={pkt.snr})")
        except Exception as e:
            print(f"  RX raw: {pkt.payload.hex()} at +{t - tx_time:.3f}s ({e})")

    modem.set_receive_callback(on_rx)
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

    # Send 5 messages, each time waiting for potential ACK
    uid = 0x42
    for seq in range(1, 6):
        dedup = (uid << 8) | seq
        payload = pack_message(CMD_MSG_ACK_REQ, f"ack test {seq}".encode())
        print(f"TX: 'ack test {seq}' (dedup=0x{dedup:04x})")
        tx_time = time.monotonic()
        modem.send(3, dedup, payload)

        # Wait up to 3s for the ACK
        time.sleep(3)
        if rx_log:
            last_t, last_pkt = rx_log[-1]
            print(f"  last RX was {last_t - tx_time:.3f}s after TX")
        else:
            print(f"  no RX in 3s window")
        rx_log.clear()
        print()

    modem.stop()
    return 0

if __name__ == "__main__":
    sys.exit(main())
