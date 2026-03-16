"""RAK11300 modem — userspace USB-CDC bulk transport via pyusb.

Drop-in replacement for RAKModem when the kernel lacks cdc_acm
(no /dev/ttyACM* device).  Uses the same binary framing protocol
as RAKModem but talks directly to the RP2040's USB bulk endpoints.

Requires: pyusb  (pip install pyusb)
"""

import struct
import threading
import time

import usb.core
import usb.util

from modem.base import LoRaModem, MAX_TTL
from modem.rak import parse_rx_frame, build_tx_frame

# RAK11300 (RP2040) USB identifiers
_VID = 0x2E8A
_PID = 0x00C0

# CDC Data interface and endpoints
_DATA_INTF = 1
_CTRL_INTF = 0
_EP_IN = 0x81   # Bulk IN
_EP_OUT = 0x01  # Bulk OUT

# USB bulk timeout (ms).
_READ_TIMEOUT_MS = 1000
_WRITE_TIMEOUT_MS = 500


def find_rak_usb():
    """Return a pyusb Device for the RAK11300, or None."""
    return usb.core.find(idVendor=_VID, idProduct=_PID)


class RAKUSBModem(LoRaModem):
    """LoRa modem using RAK11300 over raw USB bulk (no kernel cdc_acm)."""

    def __init__(self):
        self._dev: usb.core.Device | None = None
        self._rx_cb = None
        self._running = False
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def send(self, ttl: int, dedup: int, payload: bytes) -> None:
        dev = self._dev
        if dev is None:
            return
        try:
            frame = build_tx_frame(ttl, dedup, payload)
            dev.write(_EP_OUT, frame, timeout=_WRITE_TIMEOUT_MS)
        except (usb.core.USBTimeoutError, usb.core.USBError):
            # Mark disconnected.  Don't call _emit_status here —
            # send() runs on the Textual UI thread, and _emit_status
            # uses call_from_thread which is illegal from that thread.
            # The reader loop will notice _dev is None and emit the
            # status + attempt reconnect from its own thread.
            self._dev = None

    def set_receive_callback(self, cb):
        self._rx_cb = cb

    def start(self) -> None:
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True,
        )
        self._reader_thread.start()

    def stop(self) -> None:
        self._running = False
        dev = self._dev
        self._dev = None
        if dev is not None:
            try:
                usb.util.release_interface(dev, _DATA_INTF)
                usb.util.release_interface(dev, _CTRL_INTF)
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        return self._dev is not None

    @property
    def port(self) -> str:
        return "usb:rak11300"

    # -- internals ---------------------------------------------------

    def _claim(self) -> bool:
        """Find the RAK and claim its CDC data interface."""
        dev = find_rak_usb()
        if dev is None:
            return False
        try:
            # Detach kernel driver if one grabbed it (unlikely given
            # the missing cdc_acm, but defensive).
            for intf in (_CTRL_INTF, _DATA_INTF):
                try:
                    if dev.is_kernel_driver_active(intf):
                        dev.detach_kernel_driver(intf)
                except (usb.core.USBError, NotImplementedError):
                    pass

            # set_configuration() fails if the device is already
            # configured (common — the host set it on plug).  Only
            # call it if there's no active configuration.
            try:
                cfg = dev.get_active_configuration()
            except usb.core.USBError:
                cfg = None
            if cfg is None:
                dev.set_configuration()

            # Claim both the control and data interfaces.  The control
            # interface must be claimed before CDC class requests work.
            usb.util.claim_interface(dev, _CTRL_INTF)
            usb.util.claim_interface(dev, _DATA_INTF)

            # CDC-ACM: assert DTR+RTS and set line coding.  The
            # RP2040 TinyUSB stack ignores bulk OUT data until DTR
            # is asserted, so this is required for TX to work.
            # wIndex=0 targets the control interface.
            dev.ctrl_transfer(0x21, 0x22, 0x03, _CTRL_INTF, None,
                              timeout=5000)
            line_coding = struct.pack('<IBBB', 115200, 0, 0, 8)
            dev.ctrl_transfer(0x21, 0x20, 0, _CTRL_INTF, line_coding,
                              timeout=5000)

            # Drain any stale data sitting in the IN pipe (boot
            # messages, leftover framing from a previous session).
            for _ in range(10):
                try:
                    dev.read(_EP_IN, 64, timeout=50)
                except (usb.core.USBTimeoutError, usb.core.USBError):
                    break

            self._dev = dev
            return True
        except usb.core.USBError:
            return False

    def _reader_loop(self) -> None:
        first_connect = True
        buf = bytearray()

        while self._running:
            # --- (re)connect ---
            if self._dev is None:
                if not first_connect:
                    time.sleep(2)
                if self._claim():
                    label = "connected" if first_connect else "reconnected"
                    self._emit_status(f"{label} on usb:rak11300")
                    first_connect = False
                    buf.clear()
                else:
                    first_connect = False
                    continue

            # --- read ---
            try:
                data = self._dev.read(_EP_IN, 64, timeout=_READ_TIMEOUT_MS)
                buf.extend(data)
            except usb.core.USBTimeoutError:
                # Check if send() marked us disconnected while we
                # were blocking on read.
                if self._dev is None:
                    self._emit_status("disconnected")
                continue
            except usb.core.USBError:
                self._dev = None
                self._emit_status("disconnected")
                continue

            # --- frame extraction (same protocol as RAKModem) ---
            while len(buf) >= 1:
                length = buf[0]
                if length < 6:
                    # Bad length byte — discard and hunt for next frame
                    buf.pop(0)
                    continue
                if len(buf) < 1 + length:
                    break  # need more data

                frame = bytes(buf[1:1 + length])
                del buf[:1 + length]

                if frame[0] > MAX_TTL:
                    continue  # skip text boot messages

                pkt = parse_rx_frame(frame)
                if self._rx_cb:
                    self._rx_cb(pkt)
