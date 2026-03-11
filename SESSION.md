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
  pinephone.py       PinePhone backplate modem (I2C-SPI bridge + SX1262)
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
  test_pinephone.py         PinePhone backplate hardware test
  test_pinephone_sync.py    I2C-SPI bridge transport test (12 steps)
  test_pinephone_tx.py      PinePhone TX standalone test
  test_pinephone_rx.py      PinePhone RX standalone test
  test_pinephone_modem.py   PinePhoneModem interface test
  test_pinephone_pingpong.py  Half-duplex TX/RX ping-pong test
  test_pinephone_stress.py  I2C poll stress test (60s)
  test_pinephone_chat.py    Headless chat protocol test
  test_pinephone_ack.py     ACK debugging test (historical)
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
- **Half-duplex discipline**: the app treats all devices as half-duplex,
  even if the hardware supports full-duplex (e.g. Pluto SDR). After
  transmitting, a device needs time to transition back to RX — up to ~300ms
  on the PinePhone backplate (SPI command overhead through the ATtiny
  bridge), less on other hardware, but always nonzero. Any automated
  response (ACKs, protocol handshakes, future relay confirmations) must be
  delayed by `HALFDUPLEX_DELAY` (500ms, defined in `chat.py`) before
  transmission. Human-typed messages don't need the delay — typing is slow
  enough that the sender is always back in RX. This is an app-level
  constraint, not a radio constraint: the RAK firmware and SX1262 are
  capable of near-instant TX, but the remote device won't hear it.
  The constant is intentionally generous (500ms vs ~300ms worst case) to
  absorb jitter from the I2C bridge, OS scheduling, and Textual's event
  loop. Reducing it risks dropped responses on slower devices.

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
| `tools/test_pinephone.py` | PinePhone backplate hardware test (pre-sync) |
| `tools/test_pinephone_sync.py` | I2C-SPI bridge transport test (12 steps, post-sync) |
| `tools/test_pinephone_tx.py` | PinePhone standalone TX test |
| `tools/test_pinephone_rx.py` | PinePhone standalone RX test |
| `tools/test_pinephone_modem.py` | PinePhoneModem interface test |
| `tools/test_pinephone_pingpong.py` | Half-duplex TX/RX cycle test (partner: RAK PingPong firmware) |
| `tools/test_pinephone_stress.py` | 60s sustained I2C poll stress test |
| `tools/test_pinephone_chat.py` | Headless chat protocol test (pack/unpack through modem) |
| `tools/test_pinephone_ack.py` | ACK debugging test (historical) |

## What's next (rough ideas, not ordered)

- ~~**PinePhone Python driver**~~ — done. `modem/pinephone.py` implements
  `LoRaModem` ABC. TX and RX verified against RAK (see 2026-03-10 sessions).
- **Robustness testing** — longer sessions, different environments, varying
  antenna distances. The demod works at close range with high SNR; behavior at
  lower SNR is untested. PinePhone modem had one transient I2C OSError during
  extended polling — needs investigation under sustained use.
- ~~**Pluto SDR verification**~~ — done. Pluto modem works with 3-byte header.
- Expand TUI: better layout, status bar
- ~~Wire `chat.py` to accept `pinephone` as modem type (like `sdr`)~~ — done.
  Auto-detects via `/dev/i2c-2`.
- Move TX/RX further apart to test at lower SNR
- Low power / sleep mode experimentation
- Frequency hopping, different modulation parameters
- ~~Copy repo to a fresh git init as `lora-messenger` (clean history)~~ — done

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

### 2026-03-09: PinePhone backplate bringup

First session with the PinePhone LoRa backplate. Goal: verify the full
hardware path and get basic radio operations working.

**Hardware**: PinePhone (original) running postmarketOS v22.06.1 (Alpine
3.16, kernel 5.17.5, Python 3.10.4). LoRa backplate with SX1262 behind
ATtiny84 I2C-to-SPI bridge on /dev/i2c-2 at 0x28.

**What works:**
- SSH from laptop to PinePhone (key-based, non-interactive)
- I2C bus opens, ATtiny bridge responds at 0x28
- SPI read/write through bridge: version register (0x0320) = 0x53,
  consistent across reads. WriteRegister + ReadRegister roundtrips on
  sync word registers (0x0740/0x0741). Buffer write/readback works.
- GetStatus returns mode=STDBY_RC, cmd=ok
- SetStandby, SetPacketType, SetRfFrequency, SetModulationParams,
  SetPaConfig, SetTxParams, SetDioIrqParams, SetBufferBaseAddress,
  WriteBuffer — all accepted (cmd=ok, no errors)
- TCXO on DIO3: 1.7V with max timeout (~262s). Needs 500ms to stabilize.
  Calibrate(all) succeeds after TCXO warmup. Image calibration for
  902-928 MHz band also works.
- `tools/test_pinephone.py`: 5 automated tests all pass

**What doesn't work:**
- **SetTx, SetRx, SetFs have no effect.** The SX1262 accepts these
  commands (cmd=ok, zero errors) but never transitions out of STDBY_RC.
  No TxDone IRQ, no mode change, no error flags.

**Investigation so far:**
- Tried with and without TCXO, all voltage levels, with/without explicit
  Calibrate, with/without CalibrateImage, STDBY_RC vs STDBY_XOSC, SX1261
  vs SX1262 PA config, TX clamp errata workaround, with and without timeout
  on SetTx, SetFs standalone. None produce a state transition.
- SPI data integrity verified: 10-byte buffer write/readback is bit-perfect.
- ATtiny circular buffer sync verified: total_written == total_read (212/212).
- smbus2 block write limit: 32 bytes (31 SPI bytes + CMD_TRANSMIT). Not an
  issue for any SX1262 command, but limits WriteBuffer to 31 payload bytes
  per call.

**Likely root cause: BUSY pin.** The SX1262 requires BUSY to be low before
accepting new commands. The ATtiny bridge doesn't check BUSY — it fires SPI
immediately. Register read/write commands are fast (~2us BUSY), so they work
fine with our 5ms inter-command delay. But state-change commands (SetTx,
SetRx, SetFs) need the PLL to lock, and the chip may reject them if BUSY is
still high from a previous command. The SX1262 doesn't report this as an
error — it just silently ignores the command.

**Prior art — JF's working TX/RX on this exact hardware:**
- **Driver + chat app**: https://codeberg.org/JF002/pinedio-lora-driver
  (`pinephone-communicator` — bidirectional LoRa chat, C++/CMake, LGPLv3)
- **Selftest tool**: https://codeberg.org/JF002/pine64_lora_backplate_selftest
- **SX1262 driver used**: SudoMaker/sx126x_driver (C++ header-only SX126x)
- **Blog series**: codingfield.com/blog/2021-11/ (first look, flashing ATtiny,
  driver writeup with photo of successful RX from PyCom LoPy)

**Dev setup established:**
- PinePhone: postmarketOS, Python 3.10.4, smbus2, i2c-tools, udev rule
  for /dev/i2c-2 permissions
- SSH: `ssh user@<pinephone-ip>` (key auth, no password)
- Dependencies: `sudo apk add i2c-tools py3-pip cmake g++ make git linux-headers && sudo pip3 install smbus2`
- JF's driver: `~/pinedio-lora-driver/` (built, pinephone-communicator works)

### 2026-03-10: PinePhone TX/RX confirmed with JF's driver

Goal: get SetTx/SetRx working on the PinePhone LoRa backplate. The previous
session established SPI connectivity but state-change commands were silently
ignored.

**Investigation (Python driver):**
- Studied JF's pinedio-lora-driver on Codeberg. Key finding: the PinePhone
  backplate has **no BUSY pin access** — it's unconnected. JF fakes
  BUSY=low with 10ms fixed delays. NRESET is tied to the ATtiny's own
  reset pin (PB3), so it can't be toggled independently.
- Increased inter-command delays from 5ms to 10ms (matching JF), added
  missing `SetRegulatorMode(USE_DCDC)`, reordered init to match JF's
  sequence. Fixed `get_irq()` response byte parsing (was off by 2).
- **Root cause identified**: the ATtiny84's 128-byte circular SPI response
  buffer needs precise synchronization. Without JF's `SyncI2CBuffer()`
  pattern-matching approach, read/write pointer drift causes command
  responses to be misaligned. Commands appear to fail because we read
  stale status bytes instead of actual response data.
- Additional finding: `Calibrate(0x7F)` permanently wedges the SX1262's
  command processor (register R/W still works, but all state-change
  commands silently ignored). Survives `SetSleep(cold start)`. Only a
  full power cycle recovers. This happens both with and without TCXO
  configuration. Root cause unclear — may be related to the ATtiny sending
  SPI garbage during simultaneous power-on (CS floats during ATtiny boot
  before `main()` sets it high).
- The ATtiny boot-glitch theory is supported by the observation that
  backplate hot-reattach (powering SX1262 while ATtiny is already running
  with CS high) produces a clean chip state where all commands work.
  Cold boot (simultaneous power-on) intermittently corrupts the SX1262.

**Resolution: built JF's C++ driver on the PinePhone.**
- Cloned `codeberg.org/JF002/pinedio-lora-driver` with SudoMaker/sx126x_driver
  submodule. Built with `cmake -DBUILD_FOR_PINEPHONE=1`.
- Patched LoRa params to match our RAK config: 915 MHz, SF7, BW125, CR4/5,
  standard IQ (was 868 MHz, SF12, BW500, inverted IQ).
- **TX confirmed**: PinePhone sent "hello world", RAK beacon (LoRaP2P_RX
  sketch) received at -28 dBm, SNR 13. "PINEPHONE TX OK" also received.
- **RX confirmed**: RAK (LoRaP2P_TX sketch) sent "Hello" every 5s,
  PinePhone received all packets cleanly — no byte errors.

**Backplate hardware notes:**
- BUSY pin: floating/unconnected (not on any ATtiny pin or pogo pin)
- NRESET: tied to ATtiny PB3 (ATtiny's own reset pin — can't toggle independently)
- DIO1: connected to ATtiny PA7 + PB2 via 0-ohm resistors, but bridge
  firmware ignores them. Pogo INT pin via R42 is NOT populated by default.
- Pogo pins: VCC, GND, SCL(PA4), SDA(PA6), INT(unpopulated). Only 4
  functional pins (power + I2C).

**What's next:**
- Port JF's buffer sync and init sequence to Python. The C++ driver's
  `SyncI2CBuffer()` and `SX126x::Init()` are the reference implementations.
  Key difference from our Python attempt: JF's sync sends blind writes then
  scans for a known pattern, and the SX126x driver's `WriteCommand()` does
  `WaitOnBusy()` (10ms) before every command plus `WaitOnCounter()` (126us)
  after.
- Investigate the Calibrate wedge: compare JF's calibration flow vs ours.
  His driver calls `Init()` which does `Reset() -> Wakeup() -> SetStandby(RC)
  -> SetPacketType(LORA)`, then the app calls `SetDio2AsRfSwitchCtrl`,
  `SetStandby`, `SetRegulatorMode`, etc. No explicit `Calibrate(0x7F)` call
  in the app — the SX126x driver may handle it internally during `Init()`.

### 2026-03-10: I2C-SPI bridge transport verified

Ported JF's `SyncI2CBuffer` to Python and systematically tested the ATtiny84
I2C-to-SPI bridge. The transport is rock solid once synced.

**What changed:**
- `tools/test_pinephone_sync.py`: 12-step bridge transport test. Buffer sync,
  register roundtrips, buffer write/readback, overflow recovery, empty-read
  behavior, size guards.

**Key findings:**
- **Buffer sync works**: JF's pattern-match approach (write known data to
  SX1262 buffer, issue ReadBuffer, scan I2C output for the pattern) aligns
  the ATtiny circular buffer reliably. Takes ~28 bytes on clean start.
- **Transport is deterministic**: 50/50 rapid-fire register roundtrips, all
  edge values (0x00, 0xFF), buffer transfers up to 28 bytes — zero errors.
- **10ms inter-command delay not needed for register ops**: back-to-back
  commands with only 126us post-delay work fine. The 10ms is only needed
  for SX1262 state-change commands (PLL lock time etc.).
- **Overflow is recoverable**: flooding 160 response bytes into the 128-byte
  circular buffer wraps and overwrites, but re-sync always recovers (takes
  ~40 bytes instead of 28).
- **Empty reads return stale data**: no sentinel — the buffer recirculates
  old bytes. Must always read exactly as many bytes as SPI bytes sent.
- **smbus2 silently accepts oversized writes**: the Linux kernel does NOT
  reject >32-byte block writes. Added ValueError guard in `i2c_write()` and
  `spi_command()` to catch this before it reaches the kernel.

**Transport constraints (documented in test):**
- Max 31 SPI bytes per transfer (smbus2 block limit: 32 including CMD_TRANSMIT)
- WriteBuffer: max 29 data bytes per chunk (31 - opcode - offset)
- ReadBuffer: max 28 data bytes per chunk (31 - opcode - offset - NOP)
- Always read exactly len(spi_data) response bytes — no more, no less
- Re-sync after any suspected desynchronization

**JF's C++ driver (reference, studied in detail this session):**
- `PinephoneBackplate::SyncI2CBuffer()`: blind writes then pattern scan
- `PinephoneBackplate::HalGpioRead()`: returns 0 after 10ms sleep (fake BUSY)
- `SX126x::WriteCommand()`: WaitOnBusy (10ms) before, WaitOnCounter (126us) after
- `SX126x::Init()`: Reset -> Wakeup -> SetStandby(RC) -> SetPacketType(LORA)
- `PinedioLoraRadio::Initialize()`: SetDio2AsRfSwitchCtrl, SetStandby(RC),
  SetRegulatorMode(DCDC), SetBufferBaseAddresses(0,127), SetTxParams(22,RAMP_3400),
  SetDioIrqParams, SetRfFrequency(915M), SetPacketType(LORA),
  SetStopRxTimerOnPreambleDetect(false), SetModulationParams(SF7/BW125/CR4_5),
  SetPacketParams, ClearIrqStatus, SetRx(0xffffffff)
- No explicit Calibrate(0x7F) — SetRfFrequency internally calls CalibrateImage
- Reset/NRESET: can't actually toggle (tied to ATtiny reset), just a 30ms pause
- Wakeup: sends GetStatus, waits for BUSY (which returns immediately)

### 2026-03-10: PinePhone Python TX/RX confirmed

Built on the verified transport layer to implement full SX1262 init + TX + RX
in pure Python. Both directions work — no C++ driver needed.

**What changed:**
- `tools/test_pinephone_tx.py`: full init sequence + TX. Follows JF's driver
  order exactly: sync, Init (wakeup/standby/packettype), configure (dio2 RF
  switch, DCDC, PA config, freq with CalibrateImage, modulation/packet params),
  write payload, SetTx, poll for TxDone IRQ.
- `tools/test_pinephone_rx.py`: same init, then SetRx(continuous), poll for
  RxDone IRQ, read payload via GetRxBufferStatus + ReadBuffer.

**Results:**
- **TX (Pine -> RAK)**: `PINE TX OK` received by RAK at -34 dBm, SNR 13.
  TxDone IRQ fires correctly. Bit-perfect.
- **RX (RAK -> Pine)**: 8/8 `Hello` packets received in 30s. RSSI ~-40 dBm,
  SNR 12-13. Zero CRC errors. Mode transitions to RX correctly.
- **Root cause confirmed**: the buffer sync was the fix. SetTx/SetRx now work
  because SPI command/response framing is aligned. The 10ms inter-command delay
  and JF's init order also matter but sync is the critical piece.

**Key implementation details:**
- Buffer read chunking: max 28 data bytes per ReadBuffer (smbus2 limit).
  `read_buffer()` handles multi-chunk reads automatically.
- No explicit Calibrate(0x7F) — CalibrateImage(902-928 MHz) only, matching JF.
- IQ polarity workaround (REG 0x0736 bit 2) and TX modulation workaround
  (REG 0x0889 bit 2) from SX1262 datasheet errata, matching JF's driver.
- SetRx re-entered after each received packet (JF's pattern).

### 2026-03-10: PinePhoneModem driver

Consolidated the standalone TX/RX test scripts into `modem/pinephone.py` —
a `PinePhoneModem` implementing the `LoRaModem` ABC. Same interface as
`RAKModem` and `PlutoModem`: `start()`, `stop()`, `send()`, callbacks.

**What changed:**
- `modem/pinephone.py`: full driver. Bridge transport (sync, spi_command,
  size guards), SX1262 init (JF's sequence), TX with TxDone polling, RX
  with IRQ polling in a background thread. Dedup ring for air packets
  (same as PlutoModem — PinePhone sees raw relay headers, unlike RAK where
  firmware strips them).
- `tools/test_pinephone_modem.py`: exercises the LoRaModem interface.

**Verified:**
- **TX via modem.send()**: RAK (LoRaMessenger firmware) received both
  `hello from pine` and `second message` with correct TTL, dedup, CMD byte.
  RSSI -31 dBm, SNR 13.
- **RX via modem callback**: received 4/4 `Hello` packets from RAK TX beacon.
  RSSI ~-42 dBm, SNR 12. CRC OK. (Protocol filter correctly rejects these
  since the beacon sends raw payload without relay header.)
- **No I2C errors** during sustained 10s RX poll loop (one transient OSError
  seen in an earlier run — not reproduced).

**Transport constraints (enforced in driver):**
- `i2c_write()`: ValueError if >32 bytes (kernel silently truncates)
- `spi_command()`: ValueError if >31 SPI bytes
- `_write_buffer()`: auto-chunks at 29 bytes
- `_read_buffer()`: auto-chunks at 28 bytes

### 2026-03-10: chat.py wired + bidirectional protocol test

Wired `chat.py` to accept `pinephone` as a modem type. Added auto-detection
(falls back to `pinephone` when `/dev/i2c-2` exists and no serial ports found).
Added I2C error recovery (re-sync + re-init, 3 attempts with backoff).

**What changed:**
- `chat.py`: lazy imports for RAKModem/PlutoModem/PinePhoneModem. `detect_port()`
  auto-detects PinePhone via `/dev/i2c-2`. Usage: `python chat.py pinephone` or
  auto-detect on PinePhone.
- `modem/pinephone.py`: added `_recover()` — on OSError, attempts re-sync + re-init
  up to 3 times with backoff before disconnecting. Extracted `_init_and_enter_rx()`
  for reuse between initial connect and recovery.
- `tools/test_pinephone_stress.py`: 60s sustained I2C poll stress test. 1317 polls,
  zero errors, zero register mismatches. 22 polls/s throughput.
- `tools/test_pinephone_chat.py`: headless chat protocol test (pack/unpack messages
  through the modem).

**Verified:**
- **I2C stress**: 60s, 1317 polls, 0 errors. The earlier transient OSError did not
  reproduce. Recovery logic added as defensive measure.
- **Bidirectional chat protocol**: RAK received `hello from pinephone!` (rssi=-33,
  snr=13). PinePhone received `rak msg 2` (rssi=-48, snr=12). Each side missed one
  message due to TX/RX timing overlap — normal half-duplex behavior.
- **chat.py**: runs on PinePhone with `python chat.py pinephone`. Dependencies:
  textual + cryptography + smbus2 (all pip-installable on postmarketOS).

**PinePhone dev setup (updated):**
- `~/modem/` — modem package (base.py, pinephone.py)
- `~/chat.py`, `~/protocol.py` — chat app
- Deps: `pip3 install textual cryptography smbus2`
- Run: `python3 chat.py pinephone`

### 2026-03-10: ACK debugging + SX1262 wedge

Investigated why PinePhone never receives ACKs from the laptop (the reverse
direction works: laptop receives ACKs from PinePhone fine). Wedged the chip
during debug iterations. The ACK problem was **solved in a later session** —
see the ping-pong session below.

**Original (incorrect) diagnosis:**
The CRC errors seen during this session were from thermal/state degradation
caused by rapid debug iterations, not from the TX→RX transition itself.
The actual root cause was simpler: the ACK was transmitted before the
sender finished its TX→RX transition (~266ms of SPI overhead), so the
ACK was already gone by the time the phone entered RX. Fix: delay
automated responses by `HALFDUPLEX_DELAY` (500ms) before transmitting.

**What was tried (for reference):**
During ACK debugging, we iterated rapidly through several variations of
the post-TxDone sequence, deploying and testing each:

1. (working) Original: `_transmit()` returns, main loop calls `_set_rx()`
2. (broke TX) Moved `_set_rx()` into `_transmit()` + added
   `SetBufferBaseAddresses` + `SetPacketParams` between TxDone and SetRx
3. (broke TX) Same but with `_clear_irq()` after `_set_rx()` + 10ms settle
4. Several more variations with extra SPI commands between TxDone and SetRx

Each iteration was scp'd and run immediately. The chip stopped responding
correctly around iteration 3-4. By the time we reverted to known-good code,
the chip was already wedged — version register returning `0xa2` (status byte),
register writes having no effect, stuck in RX mode ignoring SetStandby.

**Likely wedge mechanism:** Sending SPI commands to the SX1262 while it's
in an intermediate state between TX completion and standby. The SX1262
transitions TX→STDBY_RC automatically after TxDone. If we send commands
(especially SetBufferBaseAddresses or SetPacketParams) while that transition
is in progress, and the ATtiny doesn't check BUSY, the chip may receive
partial/corrupt commands. After enough of these, the command processor locks
up. This is the same failure mode as the Calibrate(0x7F) wedge from the
earlier session — the SX1262 enters a state where SPI register R/W works
but the command processor ignores state-change commands.

**Prevention:** After TxDone, only send `ClearIrqStatus` and `SetRx` — no
other commands. Any reconfiguration (PacketParams, BufferBaseAddresses)
should happen BEFORE `SetTx`, not after. The init configures everything
once; the TX→RX cycle should be minimal.

**Recovery:** Power cycle only. No SPI command (including SetSleep with
cold start) recovers the command processor once wedged. We confirmed this
in the 2026-03-09 session.

**State of files on PinePhone after this session:**
- `~/modem/pinephone.py` — has debug status messages in RX path (showing
  CRC errors, TTL drops, dedup drops). Remove these once ACK is fixed.
- `~/chat.py`, `~/protocol.py`, `~/modem/base.py` — current, working
- `~/test_pinephone_*.py` — all test scripts present
- The SX1262 is **wedged** — needs power cycle before any testing.

### 2026-03-10: half-duplex ping-pong test — 20/20 pass

Built and verified a ping-pong test to validate the TX→RX half-duplex
cycle that chat ACKs need. Two new files: `LoRaP2P_PingPong` RAK firmware
(responder) and `tools/test_pinephone_pingpong.py` (initiator). Protocol:
PinePhone sends "PING NN", RAK waits 500ms and replies "PONG NN".

**Result: 20/20 rounds, zero CRC errors, zero timeouts.** RTT 854–857ms
(consistent). RSSI −40 to −57 dBm, SNR 12–14.

**What changed:**
- `firmware/sketches/examples/LoRaP2P_PingPong/`: RAK responder firmware.
  Listens for "PING" prefix, waits 500ms, echoes back as "PONG", re-enters
  RX. Prints all events to serial.
- `tools/test_pinephone_pingpong.py`: PinePhone initiator. Raw SX1262
  commands (no modem abstraction, no protocol, no encryption). Reports
  per-round RTT, RSSI/SNR, and CRC errors.

**Key findings:**

1. **SetPacketParams.PayloadLength controls TX size.** The SX1262 transmitter
   reads exactly `PayloadLength` bytes from the data buffer — it does not
   auto-detect from buffer contents. With `PayloadLength=64` and a 7-byte
   payload, the chip sends 64 bytes (7 real + 57 garbage). Must call
   `SetPacketParams` with actual length before each TX. The receiver decodes
   length from the LoRa header, so RX-side `PayloadLength` only sets buffer
   allocation and does not need updating.

2. **Minimal TX→RX transition is best.** After TxDone: `ClearIrq` + `SetRx`.
   No `SetStandby`, no `SetBufferBaseAddresses`, no IQ workaround re-apply.
   Adding commands increases the transition time and risks missing the
   incoming preamble if the reply arrives quickly.

3. **SX1262 thermal/state degradation under rapid iteration.** During
   debugging, repeated fast TX→RX cycles caused the SX1262 to enter a state
   where RX demodulation was corrupted: `RxDone` fires with correct metadata
   (length, offset, RSSI, SNR) but payload bytes are garbled, always
   triggering CRC errors. TX continues to work. Transport (SPI register R/W)
   continues to work. This is a partial wedge — the demodulator is broken
   but the command processor still responds. Power cycle + cooldown fully
   recovers. Suspect thermal: the SX1262 is sandwiched between the phone
   battery and a plastic backplate with no heatsink, generating ~400mW RF +
   ~400mW DC at +22 dBm. This has not been characterized by anyone else —
   the Pine64 website has no software for the backplate, and JF's C++ driver
   PoC was not tested under sustained TX/RX cycling.

4. **Pure RX after fresh power cycle: 8/8 clean.** Confirms the radio works
   perfectly from cold start. The CRC errors in earlier runs were from
   accumulated state degradation, not a fundamental driver bug.

**RTT breakdown (~856ms):**
- PinePhone TX path: ~266ms (SPI overhead: 10ms CMD_DELAY × ~20 commands)
- PING airtime: ~92ms (SF7/BW125, 7 bytes with preamble)
- RAK processing + 500ms reply delay: ~510ms
- PONG airtime: ~92ms
- PinePhone poll latency: ~50ms (poll interval)

**State of files on PinePhone:**
- `~/test_pinephone_pingpong.py` — deployed and working
- `~/modem/pinephone.py` — still has debug status messages; needs the same
  `SetPacketParams` fix applied in the ping-pong script before ACKs work
- All other files unchanged from previous session

**ACK fix (same session, after ping-pong):**

Root cause of the original ACK problem: the ACK was transmitted while the
sender was still in its TX→RX transition. The PinePhone's TX path takes
~266ms of SPI overhead; the laptop's ACK was sent back through the RAK
nearly instantly, arriving before the phone entered RX.

Fix: `chat.py` delays all automated responses by `HALFDUPLEX_DELAY` (500ms)
before transmitting. ACKs are scheduled via `set_timer` instead of sent
immediately. This is codified as a design decision: the app is half-duplex
regardless of hardware capability, and any automated response must respect
the turnaround delay. Human-typed messages are exempt — typing is slow
enough to be self-throttling.

**Verified:** bidirectional chat with `/ack` between laptop (RAK) and
PinePhone — ACKs delivered in both directions.
