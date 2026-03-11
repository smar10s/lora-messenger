# PinePhone LoRa Backplate

Adding the PinePhone (original, not Pro) as a third device alongside
the RAK11300 and Pluto SDR. Same SX1262 radio, but the host drives it
directly over I2C via an ATtiny84 bridge -- no intermediary firmware.

## Hardware architecture

```
PinePhone (Linux, Python)
  │
  │  I2C  /dev/i2c-2, addr 0x28
  ▼
ATtiny84  (dumb I2C-to-SPI bridge, 128-byte circular buffer)
  │
  │  SPI
  ▼
SX1262  (same chip as the RAK11300)
```

The ATtiny runs a fixed firmware (zschroeder6212/tiny-i2c-spi). It has
no radio awareness -- it just shuttles bytes between I2C and SPI. All
SX1262 configuration and radio management happens in Python on the phone.

## What we built

1. **I2C transport layer** -- Python wrapper around `/dev/i2c-2` that
   speaks the ATtiny bridge protocol. Includes JF's `SyncI2CBuffer`
   pattern for circular buffer alignment. Verified with 12-step
   transport test (`tools/test_pinephone_sync.py`).

2. **SX1262 driver in Python** -- ~15 commands, matching JF's init
   sequence exactly. No explicit `Calibrate(0x7F)` (causes wedge).
   `CalibrateImage` for 902-928 MHz only.

3. **`PinePhoneModem`** (`modem/pinephone.py`) -- `LoRaModem` ABC
   implementation. Single background thread, I2C error recovery with
   re-sync + re-init. Slots into `chat.py` like RAKModem and PlutoModem.

All four implementation phases (hardware bringup, bridge verification,
raw TX/RX, protocol integration) are complete. Bidirectional chat with
encryption and ACKs verified between PinePhone and RAK.

## Known issues and things to watch

- **One-byte I2C read quirk**: confirmed. `bus.read_byte()` one at a time
  works, block reads don't. All code reads byte-by-byte.

- **128-byte circular buffer**: the ATtiny's SPI response buffer is 128
  bytes with no flow control. Must be synchronized at startup using JF's
  `SyncI2CBuffer` pattern (write known data, scan for it). Re-sync after
  any suspected desynchronization. Overflow is recoverable.

- **No BUSY pin**: the SX1262 BUSY output is unconnected on the backplate.
  The driver fakes BUSY with 10ms fixed delays before each command. This
  works reliably for all operations tested. The earlier hypothesis that
  BUSY was causing SetTx/SetRx to be ignored was wrong — the actual fix
  was buffer synchronization.

- **No independent NRESET**: tied to ATtiny PB3 (its own reset pin).
  Can't reset the SX1262 without resetting the bridge. The driver does a
  fake 20ms pause where JF's driver would toggle NRESET.

- **Calibrate(0x7F) wedges the chip**: calling full calibration permanently
  corrupts the SX1262's command processor. Register R/W still works but
  all state-change commands are silently ignored. Only a power cycle
  recovers. Use `CalibrateImage` (902-928 MHz band) only, matching JF's
  driver. No explicit `Calibrate(0x7F)` anywhere.

- **Thermal constraints**: the SX1262 at +22 dBm generates ~800mW total,
  sandwiched between battery and plastic with no heatsink. Under sustained
  rapid TX/RX cycling the demodulator can enter a degraded state (CRC
  errors on all received packets while TX and SPI still work). Power cycle
  + cooldown recovers. Not fully characterized — may be thermal, may be
  the ATtiny sending SPI during internal SX1262 transitions that BUSY
  would normally guard.

- **DIO1 interrupt not available**: the INT pogo pin has resistor R42
  **not populated** on the v1.0 board. The driver polls `GetIrqStatus`
  every 50ms instead.

- **DIO2**: configured as RF switch control via `SetDio2AsRfSwitchCtrl`.

- **smbus2 block write limit**: 32 bytes total (1 CMD_TRANSMIT + 31 SPI
  bytes). WriteBuffer chunked at 29 data bytes, ReadBuffer at 28.

- **SetPacketParams.PayloadLength controls TX size**: the SX1262
  transmitter reads exactly `PayloadLength` bytes from the buffer. Must
  call `SetPacketParams` with the actual payload length before each TX.
  The receiver decodes length from the LoRa header.

## Dev environment

- **PinePhone**: postmarketOS v22.06.1 (Alpine 3.16, kernel 5.17.5,
  Python 3.10.4). SSH from laptop: `ssh user@192.168.1.83`
- **Deps**: `sudo apk add i2c-tools py3-pip && sudo pip3 install smbus2 textual cryptography`
- **Chat**: `python3 chat.py pinephone` (or auto-detects via `/dev/i2c-2`)
- **Files on phone**: `~/chat.py`, `~/protocol.py`, `~/modem/{base,pinephone}.py`,
  `~/test_pinephone_*.py`
- **Partner devices**: RAK11300 + Pluto on the laptop as known-good
  TX/RX counterparts. 915 MHz, SF7, BW 125 kHz.
- **JF's C++ driver**: `~/pinedio-lora-driver/` (built, patched to 915/SF7/BW125)

## Reference links

- Pine64 PinePhone docs: https://pine64.org/documentation/PinePhone/
- LoRa backplate docs: https://pine64.org/documentation/Phone_Accessories/LoRa/
- Backplate schematic (v1.0): https://files.pine64.org/doc/PinePhone/Pinephone%20LoRa%20Back%20Cover%20Panel%20Schematic-v1.0-20210425.pdf
- SX1262 datasheet: https://files.pine64.org/doc/datasheet/pinephone/DS_SX1261-2_V1.1-1307803.pdf
- ATtiny I2C-SPI bridge firmware: https://github.com/zschroeder6212/tiny-i2c-spi
- Semtech SX126x reference driver (JF's fork): https://github.com/JF002/LoRaMac-node
- **JF's PinePhone LoRa driver (Codeberg)**: https://codeberg.org/JF002/pinedio-lora-driver
  - C++ driver with working TX and RX, `pinephone-communicator` chat app
  - Uses SudoMaker/sx126x_driver for SX126x abstraction
  - Previously tested on our exact phone/backplate with a RAK monitoring serial — both TX and RX confirmed working
- **JF's backplate selftest**: https://codeberg.org/JF002/pine64_lora_backplate_selftest
  - Simple C tool: write/read SX1262 buffer via I2C bridge, verifies hardware path
- JF blog - first look at backplate: https://codingfield.com/blog/2021-11/first-look-at-lora-pinephone-backplate/
- JF blog - flashing the ATtiny: https://codingfield.com/blog/2021-11/flash-the-lora-pinephone-backplate/
- JF blog - driver and demo: https://codingfield.com/blog/2021-11/a-driver-for-the-pinephone-lora-backplate/
