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

## What we need to build

1. **I2C transport layer** -- Python wrapper around `/dev/i2c-2` that
   speaks the ATtiny bridge protocol (CMD_TRANSMIT = 0x01 prefix for
   writes, single-byte reads for responses).

2. **SX1262 driver in Python** -- the command subset needed for LoRa P2P:
   init, SetStandby, SetPacketType, SetRfFrequency, SetModulationParams,
   SetPacketParams, SetBufferBaseAddress, WriteBuffer, ReadBuffer,
   SetTx, SetRx, GetIrqStatus, ClearIrqStatus, GetRxBufferStatus,
   GetPacketStatus, ReadRegister. ~15-20 commands, sourced from the
   datasheet and Semtech reference driver.

3. **`PinePhoneModem`** -- `LoRaModem` ABC implementation that wraps the
   SX1262 driver. Slots into chat.py like RAKModem and PlutoModem.

## Implementation plan

### Phase 1: hardware bringup (no code yet)

- Charge the phone (battery may be below threshold after years off --
  may need replacement or jump-start). The PinePhone keyboard accessory
  has its own battery and can power the phone independently -- charge
  the keyboard first, then attach it to bypass the dead-battery loop.
- Install a Linux distro: Mobian, postmarketOS, or Arch Linux ARM.
  Needs Python 3.10+, i2c-tools, smbus2.
- Attach the LoRa backplate, boot, run `i2cdetect -y 2`. Device at
  0x28 = ATtiny is flashed and bridge is working. If not, the ATtiny
  needs ISP flashing (see JF's blog post on that).

### Phase 2: I2C bridge verification

- `test_i2c_bridge.py` -- start with reading the SX1262 silicon version
  register (ReadRegister 0x0320, expect 0x58 or similar). This proves
  the full hardware path: pogo pins, I2C, ATtiny, SPI, SX1262.
- Test the one-byte-read quirk: try `smbus2` block reads vs single-byte
  reads, see what actually works. JF hit this in C -- may or may not
  apply to the Python smbus2 interface.
- Grow the test: write a register, read it back, verify. Set standby
  mode, read status. Build confidence in the transport before adding
  radio complexity.

### Phase 3: raw TX/RX with a RAK counterpart

- PinePhone sends a hardcoded LoRa packet (just bytes, no protocol
  framing, no TTL/msg_id, no encryption).
- RAK receives with an example sketch (LoRaP2P_RX) or Pluto with
  tools/listen.py. Print and compare.
- Reverse: RAK sends (LoRaP2P_TX), PinePhone receives.
- Hammer it: 100 packets, count arrivals, vary payload sizes, look for
  corruption. Both directions.
- This is deliberately below the protocol layer. No dedup, no msg_id,
  no encryption. If something fails here, the cause is the radio driver
  or the transport, not protocol edge cases.

### Phase 4: protocol integration

- Add TTL + msg_id framing to match the over-the-air format.
- Implement PinePhoneModem (LoRaModem ABC).
- Run chat.py against RAK nodes. At this point it's just another node
  in the mesh.

## Known issues and things to watch

- **One-byte I2C read quirk**: JF found that multi-byte I2C reads only
  return valid data for the first byte. May be an ATtiny USI library
  limitation. Test early -- if block reads work from smbus2, great. If
  not, read byte-by-byte (slow but fine for LoRa data rates).

- **128-byte circular buffer**: the ATtiny's SPI response buffer is 128
  bytes with no flow control. Every write produces SPI response bytes
  that must be read back to keep the buffer indices in sync. Lose sync
  and recovery requires power cycling.

- **BUSY pin**: the SX1262 has a BUSY output that must be low before
  sending new commands. Not clear if this is exposed on the backplate.
  If not, add conservative delays after each command per datasheet
  worst-case times.

- **DIO1 interrupt**: connected to the INT pogo pin. Need to identify
  which GPIO this maps to in Linux and whether it's accessible. Without
  it, poll GetIrqStatus in a loop. Works but wastes power.

- **DIO2/DIO3**: DIO2 controls the RF switch (TX/RX antenna path),
  DIO3 controls TCXO voltage. Both need to be configured correctly
  during SX1262 init or the radio won't transmit/receive. Check the
  backplate schematic for how these are wired.

- **I2C clock speed**: likely 100 kHz. A 255-byte SPI transaction takes
  ~20ms over I2C at that rate. Fine for LoRa, but configuration
  sequences with many small commands will feel sluggish.

## Dev environment

The PinePhone should be SSH-accessible from the laptop. Ideal setup:
- Shell on the PinePhone for running Python test scripts
- RAK11300 + Pluto on the laptop as known-good TX/RX counterparts
- Same frequency (915 MHz, SF7, BW 125 kHz) for interop, or a second
  frequency for isolated testing

## Reference links

- Pine64 PinePhone docs: https://pine64.org/documentation/PinePhone/
- LoRa backplate docs: https://pine64.org/documentation/Phone_Accessories/LoRa/
- Backplate schematic (v1.0): https://files.pine64.org/doc/PinePhone/Pinephone%20LoRa%20Back%20Cover%20Panel%20Schematic-v1.0-20210425.pdf
- SX1262 datasheet: https://files.pine64.org/doc/datasheet/pinephone/DS_SX1261-2_V1.1-1307803.pdf
- ATtiny I2C-SPI bridge firmware: https://github.com/zschroeder6212/tiny-i2c-spi
- Semtech SX126x reference driver (JF's fork): https://github.com/JF002/LoRaMac-node
- JF blog - first look at backplate: https://codingfield.com/blog/2021-11/first-look-at-lora-pinephone-backplate/
- JF blog - flashing the ATtiny: https://codingfield.com/blog/2021-11/flash-the-lora-pinephone-backplate/
- JF blog - driver and demo: https://codingfield.com/blog/2021-11/a-driver-for-the-pinephone-lora-backplate/
