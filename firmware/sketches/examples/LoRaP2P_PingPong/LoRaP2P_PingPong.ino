/**
 * @file LoRaP2P_PingPong.ino
 * @brief Responder for half-duplex ping-pong test.
 *
 * Listens for "PING XX" packets. On receive, waits 200ms, sends
 * "PONG XX" back, re-enters RX. Prints every event to serial for
 * logging.
 *
 * Partner: tools/test_pinephone_pingpong.py (or any script that
 * sends PING and expects PONG).
 */

#include <Arduino.h>
#include "LoRaWan-Arduino.h"
#include <SPI.h>
#include <stdio.h>
#include "mbed.h"
#include "rtos.h"

using namespace std::chrono_literals;
using namespace std::chrono;

// --- LoRa parameters (must match all devices) ---
#define RF_FREQUENCY              915000000
#define TX_OUTPUT_POWER           22
#define LORA_BANDWIDTH            0       // 125 kHz
#define LORA_SPREADING_FACTOR     7
#define LORA_CODINGRATE           1       // 4/5
#define LORA_PREAMBLE_LENGTH      8
#define LORA_SYMBOL_TIMEOUT       0
#define LORA_FIX_LENGTH_PAYLOAD_ON false
#define LORA_IQ_INVERSION_ON      false
#define TX_TIMEOUT_VALUE          3000

// --- Ping-pong protocol ---
#define REPLY_DELAY_MS            500
#define MAX_PAYLOAD               64

// --- State ---
static RadioEvents_t RadioEvents;
static uint8_t RxBuffer[MAX_PAYLOAD];
static uint8_t TxBuffer[MAX_PAYLOAD];
static volatile bool txDone = false;

// Forward declarations
void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr);
void OnRxTimeout(void);
void OnRxError(void);
void OnTxDone(void);
void OnTxTimeout(void);

void setup()
{
    time_t timeout = millis();
    Serial.begin(115200);
    while (!Serial) {
        if ((millis() - timeout) < 5000) delay(100);
        else break;
    }

    lora_rak11300_init();

    Serial.println("=====================================");
    Serial.println("LoRaP2P PingPong Responder");
    Serial.println("=====================================");

    RadioEvents.TxDone    = OnTxDone;
    RadioEvents.RxDone    = OnRxDone;
    RadioEvents.TxTimeout = OnTxTimeout;
    RadioEvents.RxTimeout = OnRxTimeout;
    RadioEvents.RxError   = OnRxError;
    RadioEvents.CadDone   = NULL;

    Radio.Init(&RadioEvents);
    Radio.SetChannel(RF_FREQUENCY);

    // RX config
    Radio.SetRxConfig(MODEM_LORA, LORA_BANDWIDTH, LORA_SPREADING_FACTOR,
                      LORA_CODINGRATE, 0, LORA_PREAMBLE_LENGTH,
                      LORA_SYMBOL_TIMEOUT, LORA_FIX_LENGTH_PAYLOAD_ON,
                      0, true, 0, 0, LORA_IQ_INVERSION_ON, true);

    // TX config
    Radio.SetTxConfig(MODEM_LORA, TX_OUTPUT_POWER, 0, LORA_BANDWIDTH,
                      LORA_SPREADING_FACTOR, LORA_CODINGRATE,
                      LORA_PREAMBLE_LENGTH, LORA_FIX_LENGTH_PAYLOAD_ON,
                      true, 0, 0, LORA_IQ_INVERSION_ON, TX_TIMEOUT_VALUE);

    Serial.println("Listening for PING...");
    Radio.Rx(0);
}

void loop()
{
    // Nothing here -- all work in callbacks
}

void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr)
{
    if (size > MAX_PAYLOAD) size = MAX_PAYLOAD;
    memcpy(RxBuffer, payload, size);

    // Print what we got
    Serial.printf("RX: \"");
    for (int i = 0; i < size; i++) {
        if (RxBuffer[i] >= 0x20 && RxBuffer[i] <= 0x7E)
            Serial.printf("%c", RxBuffer[i]);
        else
            Serial.printf("\\x%02X", RxBuffer[i]);
    }
    Serial.printf("\" (%d bytes, rssi=%d, snr=%d)\n", size, rssi, snr);

    // Check for "PING" prefix
    if (size >= 4 && memcmp(RxBuffer, "PING", 4) == 0) {
        // Build PONG: replace "PING" with "PONG", keep the rest
        TxBuffer[0] = 'P';
        TxBuffer[1] = 'O';
        TxBuffer[2] = 'N';
        TxBuffer[3] = 'G';
        for (int i = 4; i < size; i++) {
            TxBuffer[i] = RxBuffer[i];
        }

        Serial.printf("  -> waiting %dms before reply\n", REPLY_DELAY_MS);
        delay(REPLY_DELAY_MS);

        Serial.printf("  -> TX: \"");
        for (int i = 0; i < size; i++) {
            Serial.printf("%c", TxBuffer[i]);
        }
        Serial.printf("\" (%d bytes)\n", size);

        txDone = false;
        Radio.Send(TxBuffer, size);
        // OnTxDone will re-enter RX
    } else {
        Serial.println("  -> not a PING, ignoring");
        Radio.Rx(0);
    }
}

void OnTxDone(void)
{
    Serial.println("  -> TxDone, back to RX");
    txDone = true;
    Radio.Rx(0);
}

void OnTxTimeout(void)
{
    Serial.println("  -> TxTimeout!");
    Radio.Rx(0);
}

void OnRxTimeout(void)
{
    // Continuous RX, shouldn't happen, but re-enter just in case
    Radio.Rx(0);
}

void OnRxError(void)
{
    Serial.println("RX: CRC error");
    Radio.Rx(0);
}
