"""Tests for PlutoSDR modem packet parsing and dedup logic (no hardware)."""

import pytest
from modem.sdr import PlutoModem
from modem.base import RxPacket


class TestParseAirPacket:
    """Test PlutoModem._parse_air_packet (static method)."""

    def test_basic(self):
        # [TTL=3][DEDUP_HI=0xAB][DEDUP_LO=0x12][app_payload...]
        raw = bytes([3, 0xAB, 0x12]) + b"\x00hello"
        pkt = PlutoModem._parse_air_packet(raw)
        assert pkt is not None
        assert pkt.ttl == 3
        assert pkt.dedup == 0xAB12
        assert pkt.payload == b"\x00hello"

    def test_minimum_valid(self):
        """Minimum valid packet: 3-byte header + 1-byte payload."""
        raw = bytes([1, 0x00, 0x01, 0xFF])
        pkt = PlutoModem._parse_air_packet(raw)
        assert pkt is not None
        assert pkt.ttl == 1
        assert pkt.dedup == 0x0001
        assert pkt.payload == bytes([0xFF])

    def test_too_short(self):
        """Packets shorter than 4 bytes are rejected."""
        assert PlutoModem._parse_air_packet(b"") is None
        assert PlutoModem._parse_air_packet(b"\x01") is None
        assert PlutoModem._parse_air_packet(b"\x01\x02") is None
        assert PlutoModem._parse_air_packet(b"\x01\x02\x03") is None

    def test_ttl_too_high(self):
        """TTL > MAX_TTL should be rejected."""
        raw = bytes([6, 0x00, 0x01, 0x00])  # TTL=6, MAX_TTL=5
        pkt = PlutoModem._parse_air_packet(raw)
        assert pkt is None

    def test_max_ttl_accepted(self):
        raw = bytes([5, 0x00, 0x01, 0x00])  # TTL=5 = MAX_TTL
        pkt = PlutoModem._parse_air_packet(raw)
        assert pkt is not None
        assert pkt.ttl == 5

    def test_dedup_byte_order(self):
        """Verify dedup is big-endian: first byte is high."""
        raw = bytes([1, 0x01, 0x02, 0x00])
        pkt = PlutoModem._parse_air_packet(raw)
        assert pkt.dedup == 0x0102

    def test_no_rssi_snr(self):
        """SDR packets have no RSSI/SNR."""
        raw = bytes([1, 0x00, 0x01, 0x00])
        pkt = PlutoModem._parse_air_packet(raw)
        assert pkt.rssi is None
        assert pkt.snr is None


class TestDedupRing:
    """Test PlutoModem dedup check/add logic."""

    def _make_modem(self):
        """Create a PlutoModem without connecting to hardware."""
        m = PlutoModem.__new__(PlutoModem)
        m._dedup_ring = []
        return m

    def test_first_seen_not_duplicate(self):
        m = self._make_modem()
        assert m._dedup_check(0x0101) is False

    def test_second_seen_is_duplicate(self):
        m = self._make_modem()
        m._dedup_check(0x0101)
        assert m._dedup_check(0x0101) is True

    def test_different_tokens_not_duplicate(self):
        m = self._make_modem()
        m._dedup_check(0x0101)
        assert m._dedup_check(0x0102) is False

    def test_ring_evicts_oldest(self):
        """After DEDUP_RING_SIZE+1 entries, the oldest is evicted."""
        m = self._make_modem()
        # Fill ring with 16 entries (0-15), then add one more to push out 0
        for i in range(17):
            m._dedup_check(i)
        # Entry 0 should be evicted, entry 1 still present
        assert m._dedup_check(0) is False  # evicted
        assert m._dedup_check(2) is True   # still in ring

    def test_add_prevents_self_echo(self):
        m = self._make_modem()
        m._dedup_add(0x4201)
        assert m._dedup_check(0x4201) is True  # seen via add

    def test_add_idempotent(self):
        m = self._make_modem()
        m._dedup_add(0x4201)
        m._dedup_add(0x4201)
        assert len(m._dedup_ring) == 1
