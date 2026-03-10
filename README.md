# LoRa Messenger

Encrypted chat and mesh relay over LoRa, using RAK11300 (RP2040 + SX1262)
devices or any SDR supported by the software LoRa PHY.

Almost entirely Python. One small C firmware handles the radio hardware;
everything else — chat, encryption, protocol, mesh relay, and a complete
LoRa PHY (modulator + demodulator) — is stock Python with pip-installable
dependencies. Flash a device if you need one, otherwise `pip install` and go.

## What it does

- IRC-style terminal chat over 915 MHz LoRa
- AES-256-GCM encryption with shared passphrase
- Automatic mesh relay: devices with no serial connection repeat messages
- Single firmware for all devices — messenger when connected, repeater when not
- Software LoRa PHY (pure Python/NumPy) for SDR-based nodes

## Hardware

**Minimal**: one RAK19007 + RAK11300 WisBlock system with a LoRa antenna.
Always connect the antenna before powering — TX without antenna damages the
SX1262 PA.

**Optional**: ADALM-Pluto SDR (or other supported SDR) as an additional node
using the software LoRa PHY.

## Setup

### Python (RAK-only)

```bash
pip install textual pyserial cryptography
```

That's it. Run `python chat.py` from the project root. Works on any
platform with Python 3.10+ — tested on macOS (arm64) and Linux (aarch64).

### Python (with SDR support)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install textual pyserial cryptography pyadi-iio scipy numpy
```

On macOS, use `./run chat.py sdr` instead of `python chat.py sdr` — the
wrapper sets `DYLD_LIBRARY_PATH` for libiio.

### Firmware

```bash
brew install arduino-cli
arduino-cli config init
arduino-cli config add board_manager.additional_urls \
  https://raw.githubusercontent.com/RAKWireless/RAKwireless-Arduino-BSP-Index/main/package_rakwireless_index.json
arduino-cli core update-index
arduino-cli core install rakwireless:mbed_rp2040
arduino-cli lib install "SX126x-Arduino" "RPI_PICO_TimerInterrupt"
```

Flash a device:

```bash
cd firmware
make flash PORT=/dev/cu.usbmodem101
```

Flash every device with the same firmware. No per-device configuration.

## Usage

### Chat

```bash
python chat.py                     # auto-detect serial port
python chat.py /dev/cu.usbmodem101 # explicit port
python chat.py sdr                 # use Pluto SDR as modem
```

Type a message and press Enter to broadcast. Up/Down arrows recall history.

### Commands

| Command | Description |
|---|---|
| `/help` | show commands |
| `/key <passphrase>` | enable AES-256-GCM encryption |
| `/key` | disable encryption |
| `/signal` | toggle RSSI/SNR display |
| `/ttl N` | set hop count (1-5, default 3) |
| `/name <nick>` | set your nickname |
| `/ack` | toggle delivery acknowledgement |
| `/exit` | quit |

### Encryption

`/key` derives a 256-bit AES key from the passphrase using PBKDF2
(100k iterations, SHA-256). Messages are encrypted with AES-256-GCM:
12-byte random nonce + ciphertext + 16-byte auth tag. All nodes must
use the same passphrase. Without `/key`, messages are plaintext.

### Mesh relay

Power a flashed device from a USB battery (without opening the serial
port) and place it between endpoints to extend range. It automatically
relays messages with TTL-based loop prevention.

## Project layout

```
chat.py              TUI chat application
protocol.py          chat protocol (1-byte command header)
modem/               modem abstraction (LoRaModem ABC + implementations)
  base.py            LoRaModem interface and RxPacket dataclass
  rak.py             RAK serial modem (pyserial)
  sdr.py             PlutoSDR modem (software LoRa PHY)
  loopback.py        loopback modem (testing)
lora/                software LoRa PHY (pure Python/NumPy)
  common.py          shared primitives (whitening, CRC, chirps)
  demod.py           CSS demodulator
  mod.py             CSS modulator
firmware/            device firmware (arduino-cli)
  Makefile           build/flash/monitor targets
  config.mk          board config
  sketches/
    LoRaMessenger/   messenger + relay firmware
    examples/        standalone TX/RX sketches
tools/               SDR development tools
  capture.py         IQ capture from Pluto
  listen.py          live LoRa receiver
  transmit.py        LoRa packet transmitter
  test_pluto.py      Pluto burst detector
tests/               test suite
```

## Acknowledgements

The LoRa PHY implementation (`lora/`) was developed independently through
hardware experimentation with SX1262 transceivers. Implementation details
for whitening, CRC, and header encoding were informed by
[gr-lora_sdr](https://github.com/tapparelj/gr-lora_sdr)
(Tapparel et al., EPFL, GPL-3.0) and the public reverse-engineering work
it builds on.

## License

[MIT](LICENSE)
