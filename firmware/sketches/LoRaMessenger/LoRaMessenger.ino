/**
 * @file LoRaMessenger.ino
 * @brief LoRa mesh relay with automatic serial/repeater mode switching
 *
 * All devices run the same firmware. When a host has the serial port open,
 * the device acts as a messenger (serial <-> LoRa). When no host is connected,
 * it acts as a dumb repeater (receive, decrement TTL, rebroadcast).
 *
 * Packet format (over LoRa):
 *   Byte 0:   TTL (uint8, capped to MAX_TTL on receive)
 *   Byte 1-2: Dedup token (uint16, opaque — used for deduplication)
 *   Byte 3+:  Payload (up to 252 bytes, binary-safe)
 *
 * Serial protocol (binary, length-prefixed):
 *   Host -> device:  [LEN] [TTL] [DEDUP_HI] [DEDUP_LO] [payload...]
 *   Device -> host:  [LEN] [TTL] [DEDUP_HI] [DEDUP_LO] [RSSI_lo] [RSSI_hi] [SNR] [payload...]
 *
 *   LEN = number of bytes following (not including LEN itself)
 *   RSSI = signed 16-bit little-endian
 *   SNR = signed 8-bit
 *
 * Boot message: "LoRaMessenger ready\n" (text, for human/TUI detection)
 */

#include <Arduino.h>
#include "LoRaWan-Arduino.h"
#include <SPI.h>
#include <stdio.h>
#include <string.h>
#include "mbed.h"
#include "rtos.h"

using namespace std::chrono_literals;
using namespace std::chrono;

// Function declarations
void OnTxDone(void);
void OnTxTimeout(void);
void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr);
void OnRxTimeout(void);
void OnRxError(void);
void broadcastRaw(const uint8_t *buf, uint16_t len);
void startRx(void);
bool dedupCheck(uint16_t id);
void dedupAdd(uint16_t id);

// LoRa parameters — must match on all devices
#define RF_FREQUENCY 915000000   // Hz (915 MHz ISM band)
#define TX_OUTPUT_POWER 22       // dBm
#define LORA_BANDWIDTH 0         // 125 kHz
#define LORA_SPREADING_FACTOR 7  // SF7
#define LORA_CODINGRATE 1        // 4/5
#define LORA_PREAMBLE_LENGTH 8
#define LORA_SYMBOL_TIMEOUT 0
#define LORA_FIX_LENGTH_PAYLOAD_ON false
#define LORA_IQ_INVERSION_ON false
#define TX_TIMEOUT_VALUE 3000

// Mesh protocol constants
#define MAX_TTL 5
#define DEDUP_SIZE 16
#define MAX_PACKET 255

static RadioEvents_t RadioEvents;
static uint8_t TxBuffer[MAX_PACKET];
static uint8_t RxBuffer[MAX_PACKET];

// Serial RX state machine
static uint8_t serialBuf[MAX_PACKET];
static uint8_t serialExpected = 0;  // 0 = waiting for LEN byte
static uint8_t serialPos = 0;

// TX state — prevent calling Radio.Send() while TX is in progress
static volatile bool txBusy = false;

// Dedup ring buffer (16-bit tokens)
static uint16_t dedupRing[DEDUP_SIZE];
static uint8_t dedupIdx = 0;
static uint8_t dedupCount = 0;

void setup()
{
  time_t timeout = millis();
  Serial.begin(115200);
  while (!Serial)
  {
    if ((millis() - timeout) < 5000)
      delay(100);
    else
      break;
  }

  memset(dedupRing, 0, sizeof(dedupRing));

  lora_rak11300_init();

  RadioEvents.TxDone = OnTxDone;
  RadioEvents.TxTimeout = OnTxTimeout;
  RadioEvents.RxDone = OnRxDone;
  RadioEvents.RxTimeout = OnRxTimeout;
  RadioEvents.RxError = OnRxError;
  RadioEvents.CadDone = NULL;

  Radio.Init(&RadioEvents);
  Radio.SetChannel(RF_FREQUENCY);

  Radio.SetTxConfig(MODEM_LORA, TX_OUTPUT_POWER, 0, LORA_BANDWIDTH,
                    LORA_SPREADING_FACTOR, LORA_CODINGRATE,
                    LORA_PREAMBLE_LENGTH, LORA_FIX_LENGTH_PAYLOAD_ON,
                    true, 0, 0, LORA_IQ_INVERSION_ON, TX_TIMEOUT_VALUE);

  Radio.SetRxConfig(MODEM_LORA, LORA_BANDWIDTH, LORA_SPREADING_FACTOR,
                    LORA_CODINGRATE, 0, LORA_PREAMBLE_LENGTH,
                    LORA_SYMBOL_TIMEOUT, LORA_FIX_LENGTH_PAYLOAD_ON,
                    0, true, 0, 0, LORA_IQ_INVERSION_ON, true);

  if (Serial)
    Serial.println("LoRaMessenger ready");
  startRx();
}

void loop()
{
  if (!Serial)
    return;

  while (Serial.available())
  {
    uint8_t b = Serial.read();

    if (serialExpected == 0)
    {
      // Waiting for LEN byte — minimum frame is 3 bytes (TTL + DEDUP_HI + DEDUP_LO)
      if (b >= 3 && b <= MAX_PACKET)
      {
        serialExpected = b;
        serialPos = 0;
      }
      // else: ignore (could be boot message remnants, newlines, etc.)
    }
    else
    {
      // Collecting frame bytes
      serialBuf[serialPos++] = b;

      if (serialPos >= serialExpected)
      {
        // Complete frame: [TTL][DEDUP_HI][DEDUP_LO][payload...]
        uint8_t ttl = serialBuf[0];
        uint16_t dedupId = ((uint16_t)serialBuf[1] << 8) | serialBuf[2];
        uint8_t payloadLen = serialExpected - 3;

        if (ttl > MAX_TTL)
          ttl = MAX_TTL;

        if (!txBusy)
        {
          dedupAdd(dedupId);

          TxBuffer[0] = ttl;
          TxBuffer[1] = serialBuf[1];  // DEDUP_HI
          TxBuffer[2] = serialBuf[2];  // DEDUP_LO
          if (payloadLen > 0)
            memcpy(TxBuffer + 3, serialBuf + 3, payloadLen);
          broadcastRaw(TxBuffer, 3 + payloadLen);
        }

        serialExpected = 0;
        serialPos = 0;
      }
    }
  }
}

void broadcastRaw(const uint8_t *buf, uint16_t len)
{
  if (len > MAX_PACKET)
    len = MAX_PACKET;
  if (buf != TxBuffer)
    memcpy(TxBuffer, buf, len);
  txBusy = true;
  Radio.Send(TxBuffer, len);
}

void startRx(void)
{
  Radio.Rx(0);
}

// --- Dedup ring buffer (16-bit tokens) ---

bool dedupCheck(uint16_t id)
{
  uint8_t limit = dedupCount < DEDUP_SIZE ? dedupCount : DEDUP_SIZE;
  for (uint8_t i = 0; i < limit; i++)
  {
    if (dedupRing[i] == id)
      return true;
  }
  return false;
}

void dedupAdd(uint16_t id)
{
  dedupRing[dedupIdx] = id;
  dedupIdx = (dedupIdx + 1) % DEDUP_SIZE;
  if (dedupCount < DEDUP_SIZE)
    dedupCount++;
}

// --- Radio callbacks ---

void OnTxDone(void)
{
  txBusy = false;
  startRx();
}

void OnTxTimeout(void)
{
  txBusy = false;
  startRx();
}

void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr)
{
  if (size < 3)
  {
    startRx();
    return;
  }

  memcpy(RxBuffer, payload, size);

  uint8_t ttl = RxBuffer[0];
  uint16_t dedupId = ((uint16_t)RxBuffer[1] << 8) | RxBuffer[2];

  if (ttl > MAX_TTL)
    ttl = MAX_TTL;

  if (ttl == 0)
  {
    startRx();
    return;
  }

  if (dedupCheck(dedupId))
  {
    startRx();
    return;
  }
  dedupAdd(dedupId);

  if (Serial)
  {
    // Messenger mode: send binary frame to host
    // Frame: [LEN] [TTL] [DEDUP_HI] [DEDUP_LO] [RSSI_lo] [RSSI_hi] [SNR] [payload...]
    uint16_t payloadLen = size - 3;
    uint8_t frameLen = 6 + payloadLen;  // TTL + DEDUP(2) + RSSI(2) + SNR + payload
    uint8_t frame[MAX_PACKET + 8];

    frame[0] = frameLen;
    frame[1] = ttl;
    frame[2] = RxBuffer[1];                      // DEDUP_HI
    frame[3] = RxBuffer[2];                      // DEDUP_LO
    frame[4] = (uint8_t)(rssi & 0xFF);           // RSSI low byte
    frame[5] = (uint8_t)((rssi >> 8) & 0xFF);    // RSSI high byte
    frame[6] = (int8_t)snr;
    if (payloadLen > 0)
      memcpy(frame + 7, RxBuffer + 3, payloadLen);

    Serial.write(frame, 1 + frameLen);
  }
  else
  {
    // Relay mode: decrement TTL, rebroadcast
    ttl--;
    if (ttl > 0 && !txBusy)
    {
      RxBuffer[0] = ttl;
      broadcastRaw(RxBuffer, size);
      return;
    }
  }

  startRx();
}

void OnRxTimeout(void)
{
  startRx();
}

void OnRxError(void)
{
  startRx();
}
