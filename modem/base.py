"""Abstract base class for LoRa modems."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

# Maximum relay hop count
MAX_TTL = 5


@dataclass
class RxPacket:
    """A received LoRa packet with metadata."""
    ttl: int
    dedup: int
    payload: bytes
    rssi: int | None = None
    snr: int | None = None
    extra: dict = field(default_factory=dict)


class LoRaModem(ABC):
    """Interface for a LoRa modem (hardware or software).

    Implementations must handle their own threading/async. The receive
    callback is called from whatever context the implementation uses
    (background thread, asyncio, etc.). The caller is responsible for
    thread safety when posting to a UI.
    """

    @abstractmethod
    def send(self, ttl: int, dedup: int, payload: bytes) -> None:
        """Transmit a packet. Non-blocking — returns immediately.

        The modem handles framing and radio TX. If the radio is busy
        (e.g. already transmitting), the packet may be silently dropped.

        dedup is a 16-bit dedup token (opaque to the modem).
        """
        ...

    @abstractmethod
    def set_receive_callback(self, cb: Callable[[RxPacket], None]) -> None:
        """Register a callback for received packets.

        Called from a background thread. Only one callback at a time —
        setting a new one replaces the previous.
        """
        ...

    @abstractmethod
    def start(self) -> None:
        """Start the modem (open port, begin listening, etc.)."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the modem (close port, stop threads, release resources)."""
        ...

    @property
    @abstractmethod
    def connected(self) -> bool:
        """Whether the modem is currently connected and operational."""
        ...

    def set_status_callback(self, cb: Callable[[str], None]) -> None:
        """Optional: register a callback for status messages (connect, disconnect, errors).

        Default implementation does nothing — subclasses override if they
        have status events to report.
        """
        self._status_cb = cb

    def _emit_status(self, msg: str) -> None:
        """Emit a status message if a callback is registered."""
        cb = getattr(self, "_status_cb", None)
        if cb:
            cb(msg)
