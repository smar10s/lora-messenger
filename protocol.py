"""Chat protocol: 1-byte command header.

The command byte is the first byte of the encrypted app-layer payload.
User identity (UID) is carried in the relay header as the high byte of
the 16-bit dedup token, not in the protocol layer.
"""

CMD_MSG = 0
CMD_MSG_ACK_REQ = 1
CMD_ACK = 2
CMD_SET_NAME = 3


def pack_message(cmd: int, payload: bytes) -> bytes:
    """Pack a command byte + payload into an app-layer message."""
    if cmd < 0 or cmd > 255:
        raise ValueError(f"command {cmd} out of range 0-255")
    return bytes([cmd]) + payload


def unpack_message(data: bytes) -> tuple[int, bytes]:
    """Unpack app-layer data into (command, payload).

    Raises ValueError if data is empty.
    """
    if not data:
        raise ValueError("empty message")
    return data[0], data[1:]
