# SESSION

Ongoing context for LLM sessions working on this project.

## How this project works

**Small steps with fast feedback.** You can flash firmware, send messages,
capture RF, and decode — all from the same session. Use this. The hardware
is your test infrastructure: change something, verify it immediately, then
build the next thing. If you can't verify a change within a minute or two,
you're making too large a jump.

Concretely: don't write 200 lines of firmware and hope it works. Write 20,
flash, send a test message, confirm it arrives. Then write the next 20.
The devices are right here and the flash-test cycle is seconds. Use it.

This applies equally to the software demodulator, the chat TUI, and the
protocol. If a change can't be tested end-to-end quickly, the architecture
is wrong — fix the architecture, don't skip the test.

## What this is

LoRa messenger with encrypted chat and mesh relay. Supports RAK11300
(RP2040 + SX1262) devices over serial and ADALM-Pluto SDR via a
pure-Python LoRa PHY. Designed to be portable — the core app needs
only pyserial + cryptography; SDR support is optional.

## Current state

- **Software LoRa PHY** (`lora/`): full modulator + demodulator in pure
  Python/NumPy.
  - RX (SX1262 -> Pluto): 15/15 live packets decoded with CRC OK
  - TX (Pluto -> SX1262): **working for all payload sizes** (5B, 8B, 10B, 12B,
    20B, 32B verified on hardware). Two bugs fixed: demod dechirp ±1 bin errors
    and missing header checksum.
  - Round-trip (synthetic): **128/128 sizes pass** (1-128 bytes).
- **PlutoSDR Modem** (`modem/sdr.py`): `PlutoModem` wraps the Pluto behind the
  `LoRaModem` ABC. Two-thread architecture (reader + demod). Chat TUI works:
  `python chat.py sdr`. RX works reliably. TX now works for all payload sizes.
  - RX LO offset: `rx_lo` is offset by 100 kHz from `tx_lo` to prevent the
    AD9363 from corrupting TX when RX has been active. Compensated in software.
  - TX uses connection teardown/rebuild: the reader thread stops RX, creates a
    TX-only Pluto connection, transmits, then restores RX.
- **Chat TUI** (`chat.py`): IRC-style chat over LoRa. AES-256-GCM encryption,
  binary serial framing, input history, /help. Modem-agnostic via the
  `LoRaModem` ABC.
- **Chat protocol v2** (`protocol.py`): 1-byte command header (full byte, 0-255).
  User identity (UID) is carried in the relay header as the high byte of the
  16-bit dedup token, not in the protocol layer. Supports nicknames (`/name`),
  message acknowledgement (`/ack`), regular messages. CMD byte is inside the
  encryption envelope; UID is in cleartext (acceptable — it's a random byte).
- **Modem abstraction** (`modem/`): `LoRaModem` base class with RAK serial,
  PlutoSDR, and loopback implementations. Chat TUI is modem-agnostic.
- **Firmware** (`firmware/`): single firmware for all devices. Messenger when
  serial is connected, relay when not. `make flash` from `firmware/`.
  Relay header: `[TTL:8][DEDUP_HI:8][DEDUP_LO:8]` — 16-bit opaque dedup token.
- **Portability**: core app (chat.py, protocol.py, modem/rak.py) runs on any
  platform with Python 3.10+ and pyserial + cryptography. Tested on macOS
  (arm64) and Pinebook Pro (aarch64 Linux). `./run` wrapper only needed for
  SDR support on macOS.
- **Dev environment**: arduino-cli with RAK BSP, Python venv with textual +
  pyserial + cryptography. SDR extras: pyadi-iio + scipy + numpy + libiio v0.25.

## Project structure

```
chat.py              TUI chat application
protocol.py          chat protocol (1-byte command header)
modem/               modem abstraction
  base.py            LoRaModem ABC, RxPacket dataclass
  rak.py             RAK serial modem
  sdr.py             PlutoSDR modem (software LoRa PHY)
  loopback.py        loopback modem (testing)
lora/                software LoRa PHY (pure Python/NumPy)
  common.py          shared primitives (whitening, CRC, chirp gen)
  demod.py           CSS demodulator
  mod.py             CSS modulator
firmware/            device firmware (arduino-cli)
  Makefile           build/flash/monitor
  config.mk          board config (FQBN, port, baud)
  sketches/
    LoRaMessenger/   messenger + relay firmware
    examples/        standalone TX/RX sketches
tools/               SDR development tools
  capture.py         IQ capture from Pluto
  listen.py          live LoRa receiver
  transmit.py        LoRa packet transmitter
  test_pluto.py      Pluto burst detector
tests/               test suite
docs/plans/          design documents (historical)
```

## Key decisions made

- **Lean toolchain**: arduino-cli + Makefile + Python/scipy. No Arduino IDE, no
  GNURadio, no heavy GUI tools. Everything scriptable and LLM-friendly.
- **Python-first, minimal C**: one small firmware for the radio hardware;
  everything else is pure Python with pip-installable dependencies. This
  extends to the LoRa PHY — a complete modulator and demodulator in ~850
  lines of Python/NumPy, rather than wrapping GNURadio + gr-lora_sdr.
  Rationale: gr-lora_sdr is good research code, but integrating it means
  building GNURadio (C++, cmake, boost, volk, platform-specific pain),
  building the OOT module against a compatible GNURadio version, wiring
  Python through the flowgraph scheduler, and managing a separate Pluto
  source/sink block (gr-iio). A version mismatch at any layer breaks the
  whole stack. The Python PHY has one dependency (numpy), works on every
  platform, and can be read, modified, and debugged in a single context
  window. The tradeoff is performance and low-SNR robustness — neither
  matters here (close-range links, not real-time streaming).
- **Modem abstraction**: `modem/base.py` defines the interface (send, receive,
  start, stop, connected). Chat TUI takes any modem implementation.
- **Minimal core dependencies**: `chat.py` + `protocol.py` + `modem/rak.py`
  need only pyserial + cryptography. SDR support (`modem/sdr.py`, `lora/`)
  adds numpy + scipy + pyadi-iio. No forced dependency on SDR stack.
- **No crypto antipatterns**: either proper AES-256-GCM or plaintext. No
  unauthenticated encryption, no XOR, no half-measures. The auth tag matters
  even for a toy — it detects tampering from anyone on the same frequency.
- **Chat protocol inside encryption**: the CMD byte is inside the encrypted
  payload. The command type is authenticated by the GCM tag — an attacker
  can't forge a `/name` or inject fake acks without the key. The UID byte
  is in the relay header (cleartext) because the firmware needs it for dedup.
  This is an acceptable metadata leak — the UID is a random byte, not a
  real identity.
- **16-bit dedup token**: the relay header is `[TTL][UID][SEQ]` where UID is
  a random byte at boot and SEQ is a sequential counter. The firmware treats
  bytes 1-2 as an opaque 16-bit dedup key. This eliminates cross-node
  collisions (the old 8-bit global msg_id would collide when two nodes
  picked the same ID). The client constructs `(uid << 8) | seq`; the relay
  doesn't know what the bytes mean.
- **Demodulator verified via round-trip test**: the modulator (`lora/mod.py`)
  generates mathematically perfect LoRa waveforms. The round-trip test
  (`tests/test_lora_roundtrip.py`) sweeps 1-128 byte payloads and proves the full
  encode/decode chain correct, isolating RF issues from coding bugs.
- **Chip-rate dechirp reference**: the demodulator uses a chip-rate (N-sample)
  downchirp reference for symbol extraction, not the oversampled (N*os-sample)
  one. The oversampled approach causes ±1 bin errors due to mismatched phase
  coefficients (C1 != C2) across the chirp frequency-wrap boundary when the
  fold and multiply operations interact. Oversampled references are still used
  for preamble/SFD alignment where ±1 tolerance is acceptable. See
  `_upchirp_cr` and `_dechirp` vs `_dechirp_os` in `lora/demod.py`.

## Hardware details

- **RAK19007**: base board, USB-C, power, reset button (single button near USB).
- **RAK11300**: RP2040 + SX1262 module. Has a tiny BootSel button (opposite corner
  from antenna) but normal flashing works via `make flash` without it.
- **FQBN**: `rakwireless:mbed_rp2040:WisCoreRAK11300Board`
- **Flashing**: just `make flash` with the board plugged in. Port auto-detects from
  `/dev/cu.usbmodem*`. No bootloader button dance needed for normal uploads.
- **ADALM-PLUTO**: USB SDR, 325-3800 MHz. Connected via USB, accessed as `usb:` in
  pyadi-iio. RX gain needs to be at max (73 dB) to see LoRa bursts from small
  antennas at close range. TX verified working at -10 dB attenuation.

## Gotchas

- Always have a LoRa antenna connected before powering the RAK11300. TX without
  antenna damages the SX1262 PA.
- **Flashing two RP2040 boards simultaneously is unreliable**. The boards reset and
  re-enumerate USB during upload; with two connected, port assignments can shuffle
  and you may flash the same board twice. Flash one at a time, or verify behavior
  after flashing.
- **Radio lockup**: calling `Radio.Send()` while a TX is in progress corrupts the
  SX1262 state permanently (until power cycle). The firmware guards this with a
  `txBusy` flag. Messages sent while busy are silently dropped.
- **Dedup token**: the 16-bit dedup space is scoped per node (UID in high byte).
  Collisions between different nodes are eliminated. Within a single node, the
  256-value SEQ counter wraps, so messages more than 256 apart can't be deduped
  — but the 16-slot ring buffer forgets after 16 messages anyway.
- The RAK board manager URL is `package_rakwireless_index.json` (underscores, not
  dots). The `_rp_` URL that appears in some old docs is 404.
- Pluto's pyadi-iio returns 12-bit ADC values in 16-bit containers (range ±2048,
  not ±32768). At max gain the noise floor sits around +22 dBFS and LoRa bursts
  hit +68 dBFS.
- **LoRa CRC-16 is non-standard**: CRC-16/CCITT (poly 0x1021, init 0) over the
  first `(payload_len - 2)` bytes, XORed with `payload[-1] | (payload[-2] << 8)`,
  stored little-endian. This matches gr-lora_sdr's implementation. Our demod and
  modulator both handle this correctly.
- **Whitening sequence**: 64 bytes hardcoded. Payloads > 64 bytes skip whitening
  for the overflow bytes (matches SX1262 behavior). The sequence is identical to
  gr-lora_sdr's `tables.h`.
- **LoRa header checksum**: the 8-symbol explicit header encodes 5 nibbles:
  payload_len (2), cr+crc (1), checksum (2). The SX1262 verifies the header
  checksum and rejects packets with wrong values (OnRxError, not silence).
  For payload_len 5 and 8 the checksum happens to be zero, masking missing
  checksum bugs. The checksum formula is in `_encode_header` in
  `lora/mod.py`, matching gr-lora_sdr's `header_impl.cc`.
- **Oversampled dechirp ±1 bin errors**: when dechirping LoRa chirps with an
  oversampled (N*os) reference and fold+FFT, ~27% of symbols get ±1 bin errors.
  Root cause: the fold-then-FFT mixes two different phase-sum coefficients (C1
  and C2) across the chirp's frequency-wrap boundary, creating spectral leakage.
  Fix: fold the oversampled input to chip rate first, then multiply by a
  chip-rate (N-sample) reference. This gives 0/128 symbol errors. The
  oversampled approach is still fine for preamble detection where ±1 is
  tolerable.

## Tools

| Script | Purpose |
|---|---|
| `tools/transmit.py` | Transmit LoRa packet from Pluto SDR |
| `tools/listen.py` | Live listener: capture-decode loop, prints payloads |
| `tools/capture.py` | Capture IQ to `.npy` file |
| `tools/test_pluto.py` | Quick burst power detector |

## What's next (rough ideas, not ordered)

- **Robustness testing** — longer sessions, different environments, varying
  antenna distances. The demod works at close range with high SNR; behavior at
  lower SNR is untested.
- ~~**Pluto SDR verification**~~ — done. Pluto modem works with 3-byte header.
- Expand TUI: better layout, status bar
- Move TX/RX further apart to test at lower SNR
- Low power / sleep mode experimentation
- Frequency hopping, different modulation parameters
- Copy repo to a fresh git init as `lora-messenger` (clean history)

## Session log

### 2026-03-08: repo restructure + cleanup

Reorganized from experimental layout to publishable project structure.

**What changed:**
- MIT license + gr-lora_sdr attribution (SPDX headers in lora/demod.py, lora/mod.py)
- `sdr/lora_demod.py` + `sdr/lora_mod.py` → `lora/` package (demod.py, mod.py)
- `sdr/` scripts → `tools/` (capture, listen, transmit, test_pluto)
- `messenger/chat.py` + `messenger/protocol.py` → project root
- Two firmware dirs merged into one `firmware/` with examples/ for old sketches
- All tests consolidated into `tests/`
- All `sys.path.insert` hacks removed; `./run` sets PYTHONPATH instead
- Added `pyproject.toml` with dependency groups (core vs sdr vs dev)
- README rewritten for the messenger project (human-facing)
- SESSION.md updated to reflect new structure
- `.gitignore` expanded (was just `__pycache__/`)

**Verified:** 13/13 protocol tests, 128/128 lora roundtrip, all import chains clean.

**Design decisions for next session:**
- 2-byte dedup token (see above) — discussed and agreed on approach
- Portability first target: Pinebook Pro + RAK, no SDR deps
- Repo will be copied to a fresh git init as `lora-messenger` (clean history)

### 2026-03-09: 16-bit dedup token + protocol v2 + portability

Major protocol change: expanded relay header from 2 to 3 bytes, rewrote
the chat protocol from a packed 3:5-bit byte to separate full bytes.

**What changed:**

Wire format (before):
```
[TTL:8][MSG_ID:8] | [CMD:3|UID:5][payload...]
 relay (2B)         encrypted app payload
```

Wire format (after):
```
[TTL:8][UID:8][SEQ:8] | [CMD:8][payload...]
 relay (3B)              encrypted app payload
```

- **Firmware**: `uint16_t` dedup ring buffer, 3-byte relay header, updated
  serial framing. Compiles clean, tested RAK-to-RAK.
- **protocol.py**: stripped to just CMD byte (full 0-255 range). Removed
  `encode_proto`/`decode_proto` and all UID handling — UID moved to relay header.
- **modem/**: all three implementations (RAK, SDR, loopback) updated for 16-bit
  dedup. `RxPacket.msg_id` renamed to `RxPacket.dedup`. `send(msg_id=...)` renamed
  to `send(dedup=...)`.
- **chat.py**: `user_id` is now 0-255 (was 0-31). `_next_dedup()` constructs
  `(uid << 8) | seq`. ACK payload expanded to 2 bytes. `detect_port()` now
  handles Linux `/dev/ttyACM*` in addition to macOS `/dev/cu.usbmodem*`.
- **Portability**: `./run` is platform-aware (skips `DYLD_LIBRARY_PATH` on
  non-macOS, falls back to system Python if no venv). README updated with
  separate RAK-only vs SDR setup instructions.
- **Tests**: 23 pytest tests (was 13). New: `test_modem_framing.py` (7 tests
  for RAK serial frame build/parse), `test_loopback.py` (6 tests for loopback
  modem delivery). Updated `test_protocol.py` for new API. Fixed standalone
  test scripts (`test_lora_roundtrip.py`, `test_demod.py`) — wrapped in
  `__main__` guard so pytest collection no longer crashes.

**Verified:**
- 23/23 pytest tests pass
- Firmware compiles clean (14120 bytes flash, 46384 bytes RAM)
- RAK-to-RAK chat on macOS works
- RAK-to-RAK cross-platform (macOS <-> Pinebook Pro aarch64 Linux) works
- 128/128 LoRa roundtrip still passes (PHY layer untouched)

**Pluto SDR:** verified working on hardware (subsequent session).

### 2026-03-09: pre-publish cleanup + test coverage

Code quality pass before publishing. No behavioral changes.

**What changed:**
- **Tests**: 174 pytest tests (was 23). New: `test_lora_roundtrip.py` converted
  from CLI script to parametrized pytest (128 payload sizes + ASCII test),
  `test_sdr_parsing.py` (7 air-packet parsing + 6 dedup ring tests),
  `test_crypto.py` (9 encryption roundtrip tests). Hardware-dependent test
  files (`test_demod.py`, `test_modem_sdr.py`, `test_modem_roundtrip.py`)
  marked with `pytest.mark.skipif`.
- **lora/common.py**: extracted shared primitives (`WHITENING`, `crc16`,
  `upchirp_os`, `bits_msb`, `int_msb`) from demod.py. Both mod.py and demod.py
  now import from common.py instead of cross-importing private symbols.
- **demod.py**: bare `except:` changed to `except Exception:` (2 places).
  `demodulate()` gained `verbose=True` parameter — callers pass `verbose=False`
  instead of wrapping in `redirect_stdout`.
- **modem/base.py**: `MAX_TTL = 5` moved here from rak.py and sdr.py.
- **modem/rak.py**: `build_tx_frame` raises `ValueError` if payload > 252 bytes.
- **modem/sdr.py**: `_tx_queue` type annotation fixed (`list[np.ndarray]` not
  `list[bytes]`). `redirect_stdout` removed in favor of `verbose=False`.
- **tools/**: `capture.py`, `listen.py`, `test_pluto.py` wrapped in `main()` +
  `if __name__ == "__main__"` guards (no longer execute on import).

**Verified:** 174/174 pytest tests pass.

