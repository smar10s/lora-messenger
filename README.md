# LoRa Messenger

Encrypted chat and mesh relay over LoRa. Supports RAK11300 (RP2040 +
SX1262) devices over serial, the PinePhone LoRa backplate over I2C, and
ADALM-Pluto SDR via a pure-Python LoRa PHY.

Almost entirely Python. One small C firmware handles the RAK radio
hardware; everything else — chat, encryption, protocol, mesh relay, the
PinePhone SX1262 driver, and a complete LoRa PHY (modulator +
demodulator) — is stock Python with pip-installable dependencies.

## What it does

- IRC-style terminal chat over 915 MHz LoRa
- AES-256-GCM encryption with shared passphrase
- Automatic mesh relay: devices with no serial connection repeat messages
- Single firmware for RAK devices — messenger when connected, repeater when not
- PinePhone LoRa backplate as a standalone chat node (no firmware needed)
- Software LoRa PHY (pure Python/NumPy) for SDR-based nodes

## Hardware

Any combination of:

- **RAK11300** (RAK19007 + RAK11300 WisBlock) with a LoRa antenna.
  Always connect the antenna before powering — TX without antenna
  damages the SX1262 PA.
- **PinePhone** (original) with the LoRa backplate (v1.0). The backplate
  has an SX1262 behind an ATtiny84 I2C-to-SPI bridge on `/dev/i2c-2`.
  No firmware to flash — the Python driver talks directly to the radio.
- **ADALM-Pluto SDR** (or other supported SDR) using the software LoRa
  PHY.

One device is enough to run the app. Two or more to actually chat.

## Setup

### RAK

```bash
pip install textual pyserial cryptography
```

Run `python chat.py` from the project root. Works on any platform with
Python 3.10+ — tested on macOS (arm64) and Linux (aarch64).

#### Firmware

```bash
brew install arduino-cli   # or your platform's equivalent
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

### PinePhone

Requires postmarketOS (or similar Linux) with Python 3.10+ and I2C
access to the backplate:

```bash
sudo apk add i2c-tools py3-pip
sudo pip3 install smbus2 textual cryptography
```

Verify the backplate is detected:

```bash
i2cdetect -y 2   # should show device at 0x28
```

No firmware to flash. The Python driver handles all SX1262 configuration
and radio management directly over I2C.

### SDR (Pluto)

Requires [libiio](https://github.com/analogdevicesinc/libiio) (the native
C library, not just the Python bindings). Install the latest
[v0.x release](https://github.com/analogdevicesinc/libiio/releases/tag/v0.26)
for your platform — `pip install` only gets the Python ctypes wrapper, not
the shared library itself.

> **Why v0.x?** The PyPI bindings (`pylibiio`) use the libiio v0 API.
> v1.x has an [incompatible API](https://github.com/analogdevicesinc/libiio/wiki/libiio_0_to_1)
> and its [Python bindings](https://github.com/analogdevicesinc/libiio/tree/main/bindings/python)
> are not yet on PyPI.

On macOS, the `./run` wrapper sets `DYLD_LIBRARY_PATH` to `.venv/lib/`,
so place the dylib there:

```bash
# macOS only — after extracting libiio.dylib:
cp libiio.dylib .venv/lib/
ln -sf libiio.dylib .venv/lib/libiio.so.1
```

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install textual pyserial cryptography pyadi-iio scipy numpy
```

Verify: `./run -c "import iio; print(iio.version)"`

On macOS, use `./run chat.py sdr` instead of `python chat.py sdr`.

## Usage

### Chat

```bash
python chat.py                     # auto-detect (RAK serial or PinePhone I2C)
python chat.py /dev/cu.usbmodem101 # explicit RAK serial port
python chat.py pinephone           # PinePhone backplate
python chat.py sdr                 # Pluto SDR
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
| `/nick <nick>` | set your nickname |
| `/ack` | toggle delivery acknowledgement |
| `/exit` | quit |

### Encryption

`/key` derives a 256-bit AES key from the passphrase using PBKDF2
(100k iterations, SHA-256). Messages are encrypted with AES-256-GCM:
12-byte random nonce + ciphertext + 16-byte auth tag. All nodes must
use the same passphrase. Without `/key`, messages are plaintext.

### Mesh relay

Power a flashed RAK device from a USB battery (without opening the serial
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
  pinephone.py       PinePhone backplate modem (I2C-SPI bridge + SX1262)
  loopback.py        loopback modem (testing)
lora/                software LoRa PHY (pure Python/NumPy)
  common.py          shared primitives (whitening, CRC, chirps)
  demod.py           CSS demodulator
  mod.py             CSS modulator
firmware/            device firmware (arduino-cli, RAK only)
  Makefile           build/flash/monitor targets
  config.mk          board config
  sketches/
    LoRaMessenger/   messenger + relay firmware
    examples/        standalone TX/RX/PingPong sketches
tools/               development and test tools
  capture.py         IQ capture from Pluto
  listen.py          live LoRa receiver
  transmit.py        LoRa packet transmitter
  test_pluto.py      Pluto burst detector
  test_pinephone_*.py  PinePhone backplate test suite
tests/               test suite
```

## Known issues

### PinePhone LoRa backplate

The PinePhone LoRa backplate (v1.0) has hardware limitations that aren't
documented by Pine64 — there's no official software for it, and to our
knowledge this project and [JF002's C++ driver](https://codeberg.org/JF002/pinedio-lora-driver)
are the only working implementations.

**No BUSY pin.** The SX1262 requires BUSY to be low before accepting new
commands. On the backplate, BUSY is unconnected — not routed to any ATtiny
pin or pogo pin. The driver fakes BUSY with fixed 10ms delays. This works
for most commands but means state-change commands (SetTx, SetRx) can be
silently ignored if issued while the chip is still processing.

**No independent NRESET.** The SX1262 NRESET is tied to the ATtiny84's own
reset pin (PB3). You can't reset the radio without resetting the bridge.
When the bridge resets, buffer synchronization is lost and must be
re-established.

**Thermal constraints.** The SX1262 at +22 dBm generates ~800mW total
(RF + DC), sandwiched between the phone battery and a plastic backplate
with no heatsink or airflow. Under sustained TX/RX cycling the chip can
enter a degraded state where RX demodulation produces consistent CRC errors
while TX and SPI register access continue to work. A power cycle + cooldown
recovers. We have not fully characterized this — the trigger seems to be
rapid repeated TX→RX transitions rather than total energy, and it may
involve the ATtiny sending SPI commands during the SX1262's internal state
transitions (which BUSY would normally guard against).

**ATtiny circular buffer.** The I2C-to-SPI bridge has a 128-byte circular
buffer with no flow control or framing. Every SPI byte produces a response
byte that must be read back. Without synchronization, read/write pointer
drift causes command responses to be misaligned. The driver implements JF's
`SyncI2CBuffer` pattern (write a known byte sequence, scan for it) at
startup and after errors.

**DIO1 interrupt not available.** The DIO1 interrupt line is connected to
the ATtiny but the bridge firmware ignores it. The INT pogo pin has resistor
R42 **not populated** on the v1.0 board. The driver polls `GetIrqStatus` in
a loop instead of using interrupts.

### RAK11300

**TX while busy corrupts the SX1262.** Calling `Radio.Send()` while a TX is
in progress permanently corrupts the SX1262 state (until power cycle). The
firmware guards this with a `txBusy` flag.

**Flashing two RP2040 boards simultaneously is unreliable.** The boards
reset and re-enumerate USB during upload. Flash one at a time.

## Acknowledgements

The LoRa PHY implementation (`lora/`) was developed independently through
hardware experimentation with SX1262 transceivers. Implementation details
for whitening, CRC, and header encoding were informed by
[gr-lora_sdr](https://github.com/tapparelj/gr-lora_sdr)
(Tapparel et al., EPFL, GPL-3.0) and the public reverse-engineering work
it builds on.

PinePhone LoRa backplate support was informed by
[JF002's pinedio-lora-driver](https://codeberg.org/JF002/pinedio-lora-driver)
(Jean-François Milants, LGPLv3), which provides a working C++ driver for
the PinePhone backplate's ATtiny84 I2C-to-SPI bridge and SX1262 radio.
JF's [blog series](https://codingfield.com/blog/2021-11/a-driver-for-the-pinephone-lora-backplate/)
on reverse-engineering the backplate hardware was invaluable. The ATtiny84
bridge firmware is [tiny-i2c-spi](https://github.com/zschroeder6212/tiny-i2c-spi)
by Zachary Schroeder (GPL-3.0).

## License

[MIT](LICENSE)
