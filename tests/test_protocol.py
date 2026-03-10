"""Tests for the chat protocol command byte."""

import pytest
from protocol import (
    CMD_MSG, CMD_MSG_ACK_REQ, CMD_ACK, CMD_SET_NAME,
    pack_message, unpack_message,
)


class TestPackUnpack:
    def test_pack_regular_message(self):
        data = pack_message(CMD_MSG, b"hello")
        assert data[0] == CMD_MSG
        assert data[1:] == b"hello"

    def test_unpack_regular_message(self):
        data = bytes([CMD_MSG]) + b"hello"
        cmd, payload = unpack_message(data)
        assert cmd == CMD_MSG
        assert payload == b"hello"

    def test_pack_ack(self):
        acked = (0xAB << 8) | 0x12  # 16-bit dedup token
        data = pack_message(CMD_ACK, acked.to_bytes(2, "big"))
        assert len(data) == 3  # cmd byte + 2-byte dedup
        cmd, payload = unpack_message(data)
        assert cmd == CMD_ACK
        assert int.from_bytes(payload, "big") == acked

    def test_pack_set_name(self):
        data = pack_message(CMD_SET_NAME, b"alice")
        cmd, payload = unpack_message(data)
        assert cmd == CMD_SET_NAME
        assert payload == b"alice"

    def test_unpack_empty_payload_rejected(self):
        with pytest.raises(ValueError):
            unpack_message(b"")

    def test_pack_all_commands(self):
        for cmd in (CMD_MSG, CMD_MSG_ACK_REQ, CMD_ACK, CMD_SET_NAME):
            data = pack_message(cmd, b"x")
            got_cmd, got_payload = unpack_message(data)
            assert got_cmd == cmd
            assert got_payload == b"x"

    def test_pack_rejects_bad_command(self):
        with pytest.raises(ValueError):
            pack_message(256, b"x")
        with pytest.raises(ValueError):
            pack_message(-1, b"x")

    def test_cmd_byte_full_range(self):
        """CMD is a full byte — values 0-255 should all work."""
        for cmd in range(256):
            data = pack_message(cmd, b"")
            got_cmd, _ = unpack_message(data)
            assert got_cmd == cmd

    def test_unpack_cmd_only(self):
        """A message with just a command byte and no payload is valid."""
        cmd, payload = unpack_message(bytes([CMD_MSG]))
        assert cmd == CMD_MSG
        assert payload == b""
