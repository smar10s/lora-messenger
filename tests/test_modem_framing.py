"""Tests for RAK modem serial framing (build_tx_frame / parse_rx_frame)."""

import struct
import pytest
from modem.rak import build_tx_frame, parse_rx_frame


class TestBuildTxFrame:
    def test_basic(self):
        frame = build_tx_frame(ttl=3, dedup=0xAB12, payload=b"hi")
        # [LEN][TTL][DEDUP_HI][DEDUP_LO][payload...]
        assert frame[0] == 5       # LEN = 3 (header) + 2 (payload)
        assert frame[1] == 3       # TTL
        assert frame[2] == 0xAB    # DEDUP_HI
        assert frame[3] == 0x12    # DEDUP_LO
        assert frame[4:] == b"hi"

    def test_empty_payload(self):
        frame = build_tx_frame(ttl=1, dedup=0x0000, payload=b"")
        assert frame == bytes([3, 1, 0, 0])  # LEN=3, TTL=1, DEDUP=0x0000

    def test_max_dedup(self):
        frame = build_tx_frame(ttl=5, dedup=0xFFFF, payload=b"x")
        assert frame[2] == 0xFF
        assert frame[3] == 0xFF

    def test_dedup_byte_order(self):
        """Verify dedup high byte comes first (big-endian in frame)."""
        frame = build_tx_frame(ttl=1, dedup=0x0102, payload=b"")
        assert frame[2] == 0x01  # high byte
        assert frame[3] == 0x02  # low byte


class TestParseRxFrame:
    def _make_rx_frame(self, ttl, dedup, rssi, snr, payload):
        """Build a raw RX frame as firmware would send (after LEN byte)."""
        buf = bytes([ttl, dedup >> 8, dedup & 0xFF])
        buf += struct.pack("<h", rssi)
        buf += struct.pack("<b", snr)
        buf += payload
        return buf

    def test_basic(self):
        frame = self._make_rx_frame(ttl=3, dedup=0xAB12, rssi=-80, snr=7, payload=b"hello")
        pkt = parse_rx_frame(frame)
        assert pkt.ttl == 3
        assert pkt.dedup == 0xAB12
        assert pkt.rssi == -80
        assert pkt.snr == 7
        assert pkt.payload == b"hello"

    def test_negative_rssi(self):
        frame = self._make_rx_frame(ttl=1, dedup=0x0001, rssi=-120, snr=-5, payload=b"")
        pkt = parse_rx_frame(frame)
        assert pkt.rssi == -120
        assert pkt.snr == -5

    def test_positive_rssi(self):
        """RSSI can technically be positive (very strong signal)."""
        frame = self._make_rx_frame(ttl=1, dedup=0x0001, rssi=10, snr=15, payload=b"")
        pkt = parse_rx_frame(frame)
        assert pkt.rssi == 10
        assert pkt.snr == 15

    def test_dedup_roundtrip(self):
        """Build a TX frame, simulate firmware adding RSSI/SNR, parse it back."""
        ttl, dedup, payload = 2, 0x4207, b"test data"
        tx = build_tx_frame(ttl, dedup, payload)
        # TX frame: [LEN][TTL][DEDUP_HI][DEDUP_LO][payload...]
        # RX frame: [TTL][DEDUP_HI][DEDUP_LO][RSSI_lo][RSSI_hi][SNR][payload...]
        body = tx[1:]  # strip LEN
        rssi_bytes = struct.pack("<h", -90)
        snr_byte = struct.pack("<b", 8)
        rx_frame = body[:3] + rssi_bytes + snr_byte + body[3:]
        pkt = parse_rx_frame(rx_frame)
        assert pkt.ttl == ttl
        assert pkt.dedup == dedup
        assert pkt.payload == payload
        assert pkt.rssi == -90
        assert pkt.snr == 8
