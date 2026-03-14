#!/usr/bin/env python3
"""LoRa Chat — IRC-style TUI over LoRa P2P."""

import os
import sys
import glob
import random
from collections import deque
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static, Input

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from modem.base import RxPacket
from protocol import (
    CMD_MSG, CMD_MSG_ACK_REQ, CMD_ACK, CMD_SET_NAME,
    pack_message, unpack_message,
)


# 24-bit colors
COLOR_MSG = "rgb(230,230,230)"
COLOR_SYSTEM = "rgb(140,140,140)"

# Max message length: 255 (LoRa packet) - 3 (TTL+DEDUP_HI+DEDUP_LO)
# - 1 (CMD byte) - 28 (AES-GCM nonce+tag) = 223 bytes.
MAX_MSG_LEN = 223

# Crypto constants
NONCE_LEN = 12
TAG_LEN = 16
CRYPTO_OVERHEAD = NONCE_LEN + TAG_LEN
_KDF_SALT = b"LoRaMessenger-v1"

# Half-duplex TX→RX turnaround delay (seconds).
# After transmitting, a half-duplex device needs time to transition back
# to RX before it can receive a response. Any automated response (ACKs,
# protocol handshakes, future features) must be delayed by at least this
# amount. Human-typed messages don't need the delay — typing is slow
# enough that the sender will always be back in RX by the time a reply
# arrives. See SESSION.md "half-duplex discipline" design decision.
HALFDUPLEX_DELAY = 0.5


def derive_key(passphrase: str) -> bytes:
    """Derive a 256-bit AES key from a passphrase using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_KDF_SALT,
        iterations=100_000,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_payload(key: bytes, plaintext: bytes) -> bytes:
    """Encrypt plaintext with AES-256-GCM. Returns nonce + ciphertext + tag."""
    nonce = os.urandom(NONCE_LEN)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext, None)
    return nonce + ct


def decrypt_payload(key: bytes, data: bytes) -> bytes | None:
    """Decrypt nonce + ciphertext + tag. Returns plaintext or None on failure."""
    if len(data) < NONCE_LEN + TAG_LEN:
        return None
    nonce = data[:NONCE_LEN]
    ct = data[NONCE_LEN:]
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ct, None)
    except Exception:
        return None


def timestamp():
    return datetime.now().strftime("%H:%M:%S")


def detect_port():
    """Auto-detect a single USB serial port (macOS or Linux)."""
    # macOS: /dev/cu.usbmodem*, Linux: /dev/ttyACM*
    ports = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/ttyACM*"))
    if len(ports) == 1:
        return ports[0]
    if len(ports) == 0:
        # Check for PinePhone backplate
        if os.path.exists("/dev/i2c-2"):
            return "pinephone"
        print("error: no USB serial device found", file=sys.stderr)
        sys.exit(1)
    print(
        f"error: multiple devices found: {', '.join(ports)}\n"
        f"specify one: python chat.py <port>",
        file=sys.stderr,
    )
    sys.exit(1)


class HistoryInput(Input):
    """Input widget with up/down arrow history recall and tab completion."""

    def __init__(self, completions_fn=None, **kwargs):
        super().__init__(**kwargs)
        self._history: list[str] = []
        self._history_idx: int = 0
        self._draft: str = ""
        self._completions_fn = completions_fn  # callable(text) -> list[str]
        self._tab_matches: list[str] = []
        self._tab_idx: int = 0
        self._tab_prefix: str = ""

    def record(self, text: str) -> None:
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._history_idx = len(self._history)
        self._draft = ""

    def _key_up(self, event) -> None:
        if not self._history:
            return
        event.prevent_default()
        if self._history_idx == len(self._history):
            self._draft = self.value
        if self._history_idx > 0:
            self._history_idx -= 1
            self.value = self._history[self._history_idx]
            self.cursor_position = len(self.value)

    def _key_down(self, event) -> None:
        if not self._history:
            return
        event.prevent_default()
        if self._history_idx < len(self._history):
            self._history_idx += 1
            if self._history_idx == len(self._history):
                self.value = self._draft
                self._draft = ""
            else:
                self.value = self._history[self._history_idx]
            self.cursor_position = len(self.value)
        else:
            # Already past end of history — clear input
            self.value = ""
            self._draft = ""

    def on_key(self, event) -> None:
        if event.key == "up":
            self._key_up(event)
        elif event.key == "down":
            self._key_down(event)
        elif event.key == "tab":
            event.prevent_default()
            self._key_tab()
        else:
            # Any non-tab key resets tab completion state
            self._tab_matches = []

    def _key_tab(self) -> None:
        if not self._completions_fn:
            return
        text = self.value
        if self._tab_matches and text == self._tab_matches[self._tab_idx]:
            # Cycle to next match
            self._tab_idx = (self._tab_idx + 1) % len(self._tab_matches)
        else:
            # New completion request
            self._tab_prefix = text
            self._tab_matches = self._completions_fn(text)
            self._tab_idx = 0
        if self._tab_matches:
            self.value = self._tab_matches[self._tab_idx]
            self.cursor_position = len(self.value)


HELP_TEXT = """/help       show this message
/name <n>   set your display name (broadcast to peers)
/name       show current name and user id
/ack        toggle ack-request mode on/off
/ack <msg>  send a single message requesting acknowledgement
/key <pass> enable AES-256-GCM encryption
/key        disable encryption
/signal     toggle signal info display
/ttl N      set TTL for outgoing messages (1-5)
/theme <t>  set color theme (/theme to list)
/exit       quit (or ctrl+q)"""

SLASH_COMMANDS = ["/help", "/name", "/ack", "/key", "/signal", "/ttl", "/theme", "/exit"]


class LoRaChat(App):
    """LoRa Chat TUI.

    Quit: ctrl+q. Copy/paste: cmd+c/cmd+v (or ctrl+c/ctrl+v in input).
    """

    ENABLE_COMMAND_PALETTE = False

    theme = "tokyo-night"

    CSS = """
    Screen {
        layout: vertical;
    }

    #chat-log {
        height: 1fr;
        border-bottom: solid rgb(60,60,60);
        scrollbar-size: 1 1;
    }

    .message {
        width: 100%;
        padding: 0 1;
        color: """ + COLOR_MSG + """;
    }

    .system {
        color: """ + COLOR_SYSTEM + """;
        text-style: italic;
    }

    #input {
        dock: bottom;
        height: 1;
        border: none;
        padding: 0 1;
    }
    """

    def __init__(self, modem):
        super().__init__()
        self._modem = modem
        self._show_signal = True
        self._ttl = 3
        self._user_id = random.randint(0, 255)
        self._seq = random.randint(0, 255)
        self._encryption_key: bytes | None = None
        self._user_name: str | None = None
        self._names: dict[int, str] = {}       # user_id -> display name
        self._ack_mode = False
        self._sent_dedups: deque[int] = deque(maxlen=64)

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat-log")
        yield HistoryInput(
            completions_fn=self._complete,
            placeholder="type a message... (/help for commands)",
            max_length=MAX_MSG_LEN,
            id="input",
        )

    def action_focus_next(self) -> None:
        """Disable tab focus cycling — input always has focus."""
        pass

    def action_focus_previous(self) -> None:
        """Disable shift-tab focus cycling."""
        pass

    def _complete(self, text: str) -> list[str]:
        """Return tab-completion candidates for the current input."""
        if not text.startswith("/"):
            return []
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()

        # Complete command names
        if len(parts) == 1 and not text.endswith(" "):
            return [c for c in SLASH_COMMANDS if c.startswith(cmd)]

        # Complete arguments for specific commands
        if cmd == "/theme":
            prefix = parts[1].lower() if len(parts) > 1 else ""
            themes = sorted(self.available_themes)
            matches = [t for t in themes if t.startswith(prefix)]
            return [f"/theme {t}" for t in matches]

        return []

    def on_mount(self) -> None:
        self.title = "LoRa Chat"
        self.sub_title = getattr(self._modem, "port", "SDR")
        self._modem.set_receive_callback(self._on_rx)
        self._modem.set_status_callback(
            lambda msg: self.call_from_thread(self._add_system, msg)
        )
        self._modem.start()
        self.query_one("#input", HistoryInput).focus()
        self._add_system(f"your id is {self._user_id}. use /name to set a display name")

    def _on_rx(self, pkt: RxPacket) -> None:
        """Called from modem's reader thread on packet receive."""
        sender_uid = pkt.dedup >> 8

        raw = pkt.payload
        if self._encryption_key is not None:
            raw = decrypt_payload(self._encryption_key, raw)
            if raw is None:
                self.call_from_thread(self._add_decrypt_failed, pkt.rssi, pkt.snr)
                return

        try:
            cmd, payload = unpack_message(raw)
        except ValueError:
            return  # malformed, drop silently

        # Collision detection: if someone else is using our user ID, re-roll.
        # Firmware dedup ensures we never see our own messages, so any
        # incoming message with our uid is a genuine collision.
        if sender_uid == self._user_id:
            self.call_from_thread(self._resolve_uid_collision)

        if cmd == CMD_MSG or cmd == CMD_MSG_ACK_REQ:
            text = payload.decode("utf-8", errors="replace")
            name = self._sender_name(sender_uid)
            self.call_from_thread(self._add_received_msg, name, text, pkt.rssi, pkt.snr)
            if cmd == CMD_MSG_ACK_REQ:
                self.call_from_thread(self._schedule_ack, pkt.dedup, sender_uid)
        elif cmd == CMD_ACK:
            if len(payload) >= 2:
                acked_dedup = int.from_bytes(payload[:2], "big")
                name = self._sender_name(sender_uid)
                self.call_from_thread(self._add_received_ack, name, acked_dedup, pkt.rssi, pkt.snr)
        elif cmd == CMD_SET_NAME:
            new_name = payload.decode("utf-8", errors="replace").strip()
            if new_name:
                old = self._names.get(sender_uid)
                self._names[sender_uid] = new_name
                self.call_from_thread(self._add_name_change, sender_uid, old, new_name)

    def _add_message(self, text: str, css_class: str = "", markup: bool = False) -> None:
        log = self.query_one("#chat-log", VerticalScroll)
        classes = f"message {css_class}".strip()
        msg = Static(text, classes=classes, markup=markup)
        log.mount(msg)
        msg.scroll_visible()

    def _signal_str(self, rssi: int | None, snr: int | None) -> str:
        if rssi is not None and snr is not None:
            return f"[{rssi} dBm, {snr} SNR] "
        return "[--] "

    def _own_name(self) -> str:
        """Display name for the local user."""
        return self._user_name or f"user-{self._user_id}"

    def _add_sent(self, text: str, dedup: int | None = None) -> None:
        sig = self._signal_str(None, None) if self._show_signal else ""
        ack = "\\[ack] " if self._ack_mode and not text.startswith("[ack]") else ""
        mid = f" (msg {dedup & 0xFF})" if dedup is not None else ""
        name = self._own_name()
        # Escape [ in signal and user text so Rich markup only applies to [bold]
        sig_esc = sig.replace("[", "\\[")
        text_esc = text.replace("[", "\\[")
        mid_esc = mid.replace("[", "\\[")
        self._add_message(
            f"\\[{timestamp()}] {sig_esc}{ack}[bold]{name}[/bold]: {text_esc}{mid_esc}",
            markup=True,
        )

    def _add_received_msg(self, name: str, text: str, rssi=None, snr=None) -> None:
        sig = self._signal_str(rssi, snr) if self._show_signal else ""
        self._add_message(f"[{timestamp()}] {sig}{name}: {text}")

    def _add_decrypt_failed(self, rssi: int | None, snr: int | None) -> None:
        sig = self._signal_str(rssi, snr) if self._show_signal else ""
        self._add_message(f"[{timestamp()}] {sig}-- decryption failed", "system")

    def _add_system(self, text: str) -> None:
        self._add_message(f"[{timestamp()}] -- {text}", "system")

    def _sender_name(self, uid: int) -> str:
        """Display name for a user ID."""
        return self._names.get(uid, f"user-{uid}")

    def _resolve_uid_collision(self) -> None:
        """Re-roll our user ID and re-broadcast name if set."""
        old_id = self._user_id
        self._user_id = random.choice([i for i in range(256) if i != old_id])
        self._add_system(f"id collision, changed {old_id} -> {self._user_id}")
        if self._user_name:
            self._names[self._user_id] = self._user_name
            if self._modem.connected:
                payload = pack_message(CMD_SET_NAME, self._user_name.encode("utf-8"))
                if self._encryption_key is not None:
                    payload = encrypt_payload(self._encryption_key, payload)
                dedup = self._next_dedup()
                self._modem.send(self._ttl, dedup, payload)

    def _schedule_ack(self, acked_dedup: int, sender_uid: int) -> None:
        """Schedule an ACK after HALFDUPLEX_DELAY.

        The sender needs time to transition from TX back to RX before
        it can receive our ACK. See HALFDUPLEX_DELAY.
        """
        self.set_timer(HALFDUPLEX_DELAY, lambda: self._send_ack(acked_dedup, sender_uid))

    def _send_ack(self, acked_dedup: int, sender_uid: int) -> None:
        """Send an ACK for the given dedup token."""
        if not self._modem.connected:
            return
        payload = pack_message(CMD_ACK, acked_dedup.to_bytes(2, "big"))
        if self._encryption_key is not None:
            payload = encrypt_payload(self._encryption_key, payload)
        dedup = self._next_dedup()
        self._modem.send(self._ttl, dedup, payload)

    def _add_received_ack(self, name: str, acked_dedup: int, rssi=None, snr=None) -> None:
        sig = self._signal_str(rssi, snr) if self._show_signal else ""
        seq = acked_dedup & 0xFF
        self._add_message(f"[{timestamp()}] {sig}{name}: received (msg {seq})", "system")

    def _add_name_change(self, uid: int, old: str | None, new: str) -> None:
        if old:
            self._add_system(f"{old} is now {new}")
        else:
            self._add_system(f"user-{uid} is now {new}")

    def _next_dedup(self) -> int:
        """Return the next 16-bit dedup token: (user_id << 8) | seq."""
        dedup = (self._user_id << 8) | self._seq
        self._seq = (self._seq + 1) % 256
        self._sent_dedups.append(dedup)
        return dedup

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        inp = self.query_one("#input", HistoryInput)
        inp.record(text)
        event.input.value = ""

        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            slash = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if slash == "/exit":
                self.exit()
            elif slash == "/help":
                for line in HELP_TEXT.splitlines():
                    self._add_system(line)
            elif slash == "/signal":
                self._show_signal = not self._show_signal
                state = "on" if self._show_signal else "off"
                self._add_system(f"signal display {state}")
            elif slash == "/ttl":
                if arg and arg.isdigit():
                    val = int(arg)
                    if 1 <= val <= 5:
                        self._ttl = val
                        self._add_system(f"TTL set to {val}")
                    else:
                        self._add_system("TTL must be 1-5")
                else:
                    self._add_system(f"TTL is {self._ttl}. usage: /ttl N (1-5)")
            elif slash == "/key":
                if arg:
                    self._encryption_key = derive_key(arg)
                    self._add_system("encryption enabled")
                else:
                    if self._encryption_key is not None:
                        self._encryption_key = None
                        self._add_system("encryption disabled")
                    else:
                        self._add_system("usage: /key <passphrase> to enable, /key to disable")
            elif slash == "/name":
                if arg:
                    self._user_name = arg.strip()
                    self._names[self._user_id] = self._user_name
                    self._add_system(f"name set to {self._user_name}")
                    # broadcast to peers
                    if self._modem.connected:
                        payload = pack_message(CMD_SET_NAME, self._user_name.encode("utf-8"))
                        if self._encryption_key is not None:
                            payload = encrypt_payload(self._encryption_key, payload)
                        dedup = self._next_dedup()
                        self._modem.send(self._ttl, dedup, payload)
                else:
                    if self._user_name:
                        self._add_system(f"you are {self._user_name} (id {self._user_id})")
                    else:
                        self._add_system(f"no name set (id {self._user_id}). usage: /name <name>")
            elif slash == "/ack":
                if arg:
                    if not self._modem.connected:
                        self._add_system("not connected")
                    else:
                        payload = pack_message(CMD_MSG_ACK_REQ, arg.encode("utf-8"))
                        if self._encryption_key is not None:
                            payload = encrypt_payload(self._encryption_key, payload)
                        dedup = self._next_dedup()
                        self._modem.send(self._ttl, dedup, payload)
                        self._add_sent(f"[ack] {arg}", dedup=dedup)
                else:
                    self._ack_mode = not self._ack_mode
                    state = "on" if self._ack_mode else "off"
                    self._add_system(f"ack mode {state}")
            elif slash == "/theme":
                if arg:
                    name = arg.strip().lower()
                    if name in self.available_themes:
                        self.theme = name
                        self._add_system(f"theme set to {name}")
                    else:
                        self._add_system(f"unknown theme: {name}")
                else:
                    names = ", ".join(sorted(self.available_themes))
                    self._add_system(f"themes: {names}")
                    self._add_system(f"current: {self.theme}")
            else:
                self._add_system(f"unknown command: {text}")
            return

        if not self._modem.connected:
            self._add_system("not connected")
            return

        cmd = CMD_MSG_ACK_REQ if self._ack_mode else CMD_MSG
        payload = pack_message(cmd, text.encode("utf-8"))
        if self._encryption_key is not None:
            payload = encrypt_payload(self._encryption_key, payload)

        dedup = self._next_dedup()
        self._modem.send(self._ttl, dedup, payload)
        self._add_sent(text, dedup=dedup if self._ack_mode else None)

    def on_unmount(self) -> None:
        self._modem.stop()


def main():
    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        port = detect_port()

    if port == "sdr":
        from modem.sdr import PlutoModem
        modem = PlutoModem()
    elif port == "pinephone":
        from modem.pinephone import PinePhoneModem
        modem = PinePhoneModem()
    else:
        from modem.rak import RAKModem
        modem = RAKModem(port)

    app = LoRaChat(modem)
    app.run()


if __name__ == "__main__":
    main()
