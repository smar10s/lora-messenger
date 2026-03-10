"""Loopback modem — in-memory, no hardware, for testing."""

from modem.base import LoRaModem, RxPacket


class LoopbackModem(LoRaModem):
    """Modem that delivers sent packets to connected peers.

    Useful for testing the chat/mesh/encryption stack without hardware.
    Can also be wired to other LoopbackModem instances to simulate a
    multi-node network.
    """

    def __init__(self):
        self._rx_cb = None
        self._running = False
        self._peers: list["LoopbackModem"] = []

    def send(self, ttl: int, dedup: int, payload: bytes) -> None:
        if not self._running:
            return
        pkt = RxPacket(
            ttl=ttl,
            dedup=dedup,
            payload=payload,
            rssi=-50,
            snr=10,
        )
        # Deliver to all peers (not self — half-duplex, you don't hear yourself)
        for peer in self._peers:
            if peer._running and peer._rx_cb:
                peer._rx_cb(pkt)

    def set_receive_callback(self, cb):
        self._rx_cb = cb

    def start(self) -> None:
        self._running = True
        self._emit_status("loopback started")

    def stop(self) -> None:
        self._running = False

    @property
    def connected(self) -> bool:
        return self._running

    def connect_to(self, other: "LoopbackModem") -> None:
        """Wire two loopback modems together (bidirectional)."""
        if other not in self._peers:
            self._peers.append(other)
        if self not in other._peers:
            other._peers.append(self)
