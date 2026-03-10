"""Tests for the loopback modem — verifies packet delivery between peers."""

import pytest
from modem.loopback import LoopbackModem
from modem.base import RxPacket


class TestLoopbackDelivery:
    def test_send_delivers_to_peer(self):
        a = LoopbackModem()
        b = LoopbackModem()
        a.connect_to(b)

        received = []
        b.set_receive_callback(lambda pkt: received.append(pkt))

        a.start()
        b.start()

        a.send(ttl=3, dedup=0xAB01, payload=b"hello")

        assert len(received) == 1
        pkt = received[0]
        assert pkt.ttl == 3
        assert pkt.dedup == 0xAB01
        assert pkt.payload == b"hello"

    def test_dedup_token_structure(self):
        """Verify dedup high byte = uid, low byte = seq."""
        a = LoopbackModem()
        b = LoopbackModem()
        a.connect_to(b)

        received = []
        b.set_receive_callback(lambda pkt: received.append(pkt))

        a.start()
        b.start()

        uid = 0x42
        seq = 0x07
        dedup = (uid << 8) | seq
        a.send(ttl=1, dedup=dedup, payload=b"x")

        assert len(received) == 1
        assert received[0].dedup >> 8 == uid
        assert received[0].dedup & 0xFF == seq

    def test_no_self_delivery(self):
        """Sender should NOT receive its own message."""
        a = LoopbackModem()
        b = LoopbackModem()
        a.connect_to(b)

        a_received = []
        a.set_receive_callback(lambda pkt: a_received.append(pkt))

        a.start()
        b.start()

        a.send(ttl=1, dedup=0x0101, payload=b"echo?")
        assert len(a_received) == 0

    def test_bidirectional(self):
        a = LoopbackModem()
        b = LoopbackModem()
        a.connect_to(b)

        a_received = []
        b_received = []
        a.set_receive_callback(lambda pkt: a_received.append(pkt))
        b.set_receive_callback(lambda pkt: b_received.append(pkt))

        a.start()
        b.start()

        a.send(ttl=1, dedup=0x0101, payload=b"from-a")
        b.send(ttl=1, dedup=0x0201, payload=b"from-b")

        assert len(b_received) == 1
        assert b_received[0].payload == b"from-a"
        assert len(a_received) == 1
        assert a_received[0].payload == b"from-b"

    def test_not_running_drops(self):
        """Messages are dropped if modem is not started."""
        a = LoopbackModem()
        b = LoopbackModem()
        a.connect_to(b)

        received = []
        b.set_receive_callback(lambda pkt: received.append(pkt))
        b.start()
        # a is NOT started

        a.send(ttl=1, dedup=0x0101, payload=b"dropped")
        assert len(received) == 0

    def test_three_nodes(self):
        """Three-node mesh: A->B and A->C both receive."""
        a = LoopbackModem()
        b = LoopbackModem()
        c = LoopbackModem()
        a.connect_to(b)
        a.connect_to(c)

        b_rx = []
        c_rx = []
        b.set_receive_callback(lambda pkt: b_rx.append(pkt))
        c.set_receive_callback(lambda pkt: c_rx.append(pkt))

        a.start()
        b.start()
        c.start()

        a.send(ttl=2, dedup=0xFF01, payload=b"broadcast")

        assert len(b_rx) == 1
        assert len(c_rx) == 1
        assert b_rx[0].payload == b"broadcast"
        assert c_rx[0].payload == b"broadcast"
