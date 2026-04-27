///////////////////////////////////////////////////////////////////////////////
/*
  can-gateway — SLCAN CAN Bus Gateway
  XIAO ESP32S3

  Implements the SLCAN serial protocol, compatible with python-can
  (interface='slcan'), slcand, Wireshark, and other standard CAN tools.

  SLCAN commands:
    S<n>                 set speed  S0=10k S3=100k S4=125k S5=250k S6=500k S8=1M
    O                    open channel
    C                    close channel
    t<III><N><DD…>       transmit standard frame  (11-bit ID, 3 hex chars)
    T<IIIIIIII><N><DD…>  transmit extended frame  (29-bit ID, 8 hex chars)
    F                    read status flags
    V / v / N            version / serial number
  Responses: \r = OK, \a = error, z/Z = TX ack, t/T = received frame

  Toggle between Serial and WebSocket transport:
    Define USE_SERIAL to use USB Serial (python-can slcan interface).
    Comment out USE_SERIAL to use WiFi + WebSocket (same SLCAN text protocol).

  TWAI pins: TX=D6 (GPIO43), RX=D7 (GPIO44)

  Copyright © 2026 Nicholas Stedman
*/
///////////////////////////////////////////////////////////////////////////////

// Comment out to use WebSocket instead
#define USE_SERIAL

// Uncomment to enable latency logging (ser_rx / twai_rtt / ser_tx)
// #define LOG_LATENCY

#include <Arduino.h>
#include <ESP32-TWAI-CAN.hpp>

#ifndef USE_SERIAL
#include <WiFi.h>
#include <WebSocketsServer.h>
#include <ESPmDNS.h>
#endif

// ── WiFi credentials ────────────────────────────────────────────────────────
const char *ssid = "YOUR_SSID";
const char *password = "YOUR_PASSWORD";

// ── TWAI (CAN) pins ─────────────────────────────────────────────────────────
#define TWAI_TX_PIN ((gpio_num_t)43) // GPIO43
#define TWAI_RX_PIN ((gpio_num_t)44) // GPIO44

static int currentBaudKbps = 1000; // track active baud rate

// ── WebSocket server ─────────────────────────────────────────────────────────
#ifndef USE_SERIAL
WebSocketsServer wsServer(80);
#endif

// ── Serial input buffer ──────────────────────────────────────────────────────
#ifdef USE_SERIAL
static char serialBuf[128];
static size_t serialBufLen = 0;
#endif

// ── LED flash state ───────────────────────────────────────────────────────────
static uint32_t ledOffMs = 0; // millis() when LED should turn back off (0 = off)

void ledFlash()
{
    digitalWrite(LED_BUILTIN, LOW); // on (active-low)
    ledOffMs = millis() + 5;        // 5 ms pulse
}

void ledUpdate()
{
    if (ledOffMs && millis() >= ledOffMs)
    {
        digitalWrite(LED_BUILTIN, HIGH);
        ledOffMs = 0;
    }
}

// ── twaiInit — install and start TWAI at given kbps ──────────────────────────
bool twaiInit(int kbps)
{
    if (kbps != 100 && kbps != 125 && kbps != 250 &&
        kbps != 500 && kbps != 800 && kbps != 1000) {
        Serial.printf("baud: unsupported rate %d kbps\n", kbps);
        return false;
    }
    // Pass custom gConfig to use TWAI_MODE_NORMAL for proper two-way CAN comms
    twai_general_config_t g_config = TWAI_GENERAL_CONFIG_DEFAULT(
        TWAI_TX_PIN, TWAI_RX_PIN, TWAI_MODE_NORMAL);
    g_config.tx_queue_len = 10;
    g_config.rx_queue_len = 10;
    if (!ESP32Can.begin(ESP32Can.convertSpeed(kbps),
                        -1, -1, 0xFFFF, 0xFFFF, nullptr, &g_config)) {
        Serial.println("baud: TWAI init failed");
        return false;
    }
    return true;
}

// ── twaiSetBaud — stop, uninstall, reinitialise at new rate ──────────────────
void twaiSetBaud(int kbps)
{
    ESP32Can.end();
    if (twaiInit(kbps))
        currentBaudKbps = kbps;
}

// ── SLCAN speed table  (index = S command digit) ─────────────────────────────
static const int slcanSpeeds[] = {10, 20, 50, 100, 125, 250, 500, 800, 1000};
static bool canOpen = false;

// ── Latency tracking ─────────────────────────────────────────────────────────
#ifdef LOG_LATENCY
static uint32_t latSerialFirstByteUs = 0;
static uint32_t latTxEnqueueUs       = 0;
#endif

// ── handleSlcan — SLCAN protocol parser ──────────────────────────────────────
// Called from both Serial and WebSocket paths.
void handleSlcan(const char *cmd)
{
    switch (cmd[0]) {

        case 'S': { // S<n> — set CAN speed
            int idx = cmd[1] - '0';
            if (idx >= 0 && idx <= 8) {
                twaiSetBaud(slcanSpeeds[idx]);
                sendFeedback("\r");
            } else {
                sendFeedback("\a");
            }
            return;
        }

        case 'O': // open channel
            canOpen = true;
            sendFeedback("\r");
            return;

        case 'C': // close channel
            canOpen = false;
            sendFeedback("\r");
            return;

        case 'Z': // timestamp mode — not supported, just ack
            sendFeedback("\r");
            return;

        case 't': // standard frame  tIIINDD…
        case 'T': { // extended frame TIIIIIIIINDD…
            bool extd  = (cmd[0] == 'T');
            int  idLen = extd ? 8 : 3;
            const char *p = cmd + 1;

            char idBuf[9] = {};
            memcpy(idBuf, p, idLen);
            uint32_t id = (uint32_t)strtoul(idBuf, nullptr, 16);
            p += idLen;

            int dlc = *p - '0';
            if (dlc < 0 || dlc > 8) { sendFeedback("\a"); return; }
            p++;

            CanFrame frame = {};
            frame.identifier      = id;
            frame.extd            = extd ? 1 : 0;
            frame.data_length_code = dlc;
            for (int i = 0; i < dlc; i++) {
                char b[3] = {p[i*2], p[i*2+1], '\0'};
                frame.data[i] = (uint8_t)strtoul(b, nullptr, 16);
            }

#ifdef LOG_LATENCY
            latTxEnqueueUs = micros();
#endif
            if (ESP32Can.writeFrame(frame)) {
                ledFlash();
                sendFeedback(extd ? "Z\r" : "z\r");
            } else {
#ifdef LOG_LATENCY
                latTxEnqueueUs = 0;
#endif
                sendFeedback("\a");
            }
            return;
        }

        case 'F': { // read status flags
            uint8_t flags = 0;
            twai_status_info_t info;
            if (twai_get_status_info(&info) == ESP_OK) {
                if (info.state == TWAI_STATE_BUS_OFF)                       flags |= 0x80;
                if (info.tx_error_counter >= 96 ||
                    info.rx_error_counter >= 96)                            flags |= 0x04;
            }
            char buf[8];
            snprintf(buf, sizeof(buf), "F%02X\r", flags);
            sendFeedback(buf);
            return;
        }

        case 'V': sendFeedback("V0101\r"); return;
        case 'v': sendFeedback("v0101\r"); return;
        case 'N': sendFeedback("NCG01\r"); return;

        default: sendFeedback("\a"); return;
    }
}

// ── sendFeedback ─────────────────────────────────────────────────────────────
void sendFeedback(const char *buf)
{
#ifdef USE_SERIAL
    Serial.print(buf); // SLCAN strings already carry their \r terminator
    Serial.flush();    // force HWCDC to push bytes immediately, bypassing 1s timer
#else
    wsServer.broadcastTXT(buf);
#endif
}

// ── pollRx ───────────────────────────────────────────────────────────────────
void pollRx()
{
    CanFrame msg;
    while (ESP32Can.readFrame(msg, 0))
    {
#ifdef LOG_LATENCY
        uint32_t rxFrameUs = micros();
#endif
        char buf[32];
        if (msg.extd)
            snprintf(buf, sizeof(buf), "T%08X%d", msg.identifier, msg.data_length_code);
        else
            snprintf(buf, sizeof(buf), "t%03X%d", msg.identifier, msg.data_length_code);
        for (int i = 0; i < msg.data_length_code; i++)
            snprintf(buf + strlen(buf), sizeof(buf) - strlen(buf), "%02X", msg.data[i]);
        strcat(buf, "\r");
        sendFeedback(buf);
#ifdef LOG_LATENCY
        uint32_t txDoneUs = micros();
        if (latTxEnqueueUs) {
            uint32_t tSerRx = latSerialFirstByteUs ? latTxEnqueueUs - latSerialFirstByteUs : 0;
            uint32_t tRtt   = rxFrameUs - latTxEnqueueUs;
            uint32_t tSerTx = txDoneUs  - rxFrameUs;
            Serial.printf("ser_rx=%luus twai_rtt=%luus ser_tx=%luus total=%luus\n",
                tSerRx, tRtt, tSerTx, tSerRx + tRtt + tSerTx);
            latTxEnqueueUs       = 0;
            latSerialFirstByteUs = 0;
        }
#endif
    }
}

// ── twaiStatus — print error counters and bus state ──────────────────────────
void twaiStatus()
{
    twai_status_info_t info;
    if (twai_get_status_info(&info) != ESP_OK) {
        Serial.println("status: error reading TWAI status");
        return;
    }
    const char *state_str = "?";
    switch (info.state) {
        case TWAI_STATE_STOPPED:    state_str = "STOPPED";    break;
        case TWAI_STATE_RUNNING:    state_str = "RUNNING";    break;
        case TWAI_STATE_BUS_OFF:    state_str = "BUS_OFF";    break;
        case TWAI_STATE_RECOVERING: state_str = "RECOVERING"; break;
    }
    Serial.printf("status: state=%s tx_err=%lu rx_err=%lu tx_fail=%lu rx_miss=%lu arb_lost=%lu bus_err=%lu\n",
        state_str,
        info.tx_error_counter, info.rx_error_counter,
        info.tx_failed_count,  info.rx_missed_count,
        info.arb_lost_count,   info.bus_error_count);
}

// ── pollSerial ───────────────────────────────────────────────────────────────
#ifdef USE_SERIAL
void pollSerial()
{
    while (Serial.available())
    {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r')
        {
            if (serialBufLen > 0)
            {
                serialBuf[serialBufLen] = '\0';
#ifdef LOG_LATENCY
                Serial.printf(">%s\n", serialBuf);
#endif
                handleSlcan(serialBuf);
                serialBufLen = 0;
            }
        }
        else if (c >= 0x20 && c < 0x7F && serialBufLen < sizeof(serialBuf) - 1)
        {
#ifdef LOG_LATENCY
            if (serialBufLen == 0) latSerialFirstByteUs = micros();
#endif
            serialBuf[serialBufLen++] = c;
        }
    }
}
#endif

// ── WebSocket event handler ───────────────────────────────────────────────────
#ifndef USE_SERIAL
void onWsEvent(uint8_t num, WStype_t type, uint8_t *payload, size_t length)
{
    if (type == WStype_TEXT)
    {
        payload[length] = '\0'; // null-terminate in place
        handleSlcan((const char *)payload);
    }
}
#endif

// ── setup ────────────────────────────────────────────────────────────────────
void setup()
{
    pinMode(LED_BUILTIN, OUTPUT);
    digitalWrite(LED_BUILTIN, HIGH); // off (active-low)

    Serial.begin(115200);
    delay(500);
    Serial.println("can-gateway booting...");

#ifdef USE_SERIAL
    Serial.println("Transport: USB Serial (SLCAN)");
    Serial.println("Ready. Send S<n>/O/t/T per SLCAN spec.");
#else
    // ── WiFi ──────────────────────────────────────────────────────────────────
    Serial.printf("Connecting to %s", ssid);
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED)
    {
        delay(250);
        digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
        Serial.print(".");
    }
    digitalWrite(LED_BUILTIN, LOW);
    Serial.printf("\nWiFi connected: %s\n", WiFi.localIP().toString().c_str());

    // ── mDNS ──────────────────────────────────────────────────────────────────
    if (MDNS.begin("can-gw"))
        Serial.println("mDNS: can-gw.local");
    else
        Serial.println("mDNS failed");

    // ── WebSocket server ───────────────────────────────────────────────────────
    wsServer.begin();
    wsServer.onEvent(onWsEvent);
    Serial.println("WebSocket server started on port 80");
#endif

    // ── TWAI (CAN) ────────────────────────────────────────────────────────────
    if (!twaiInit(currentBaudKbps))
        while (true) ;
    Serial.printf("TWAI started at %d kbps\n", currentBaudKbps);
}

// ── checkBusOff — periodic bus-off recovery, called from loop ────────────────
static uint32_t busOffCheckMs = 0;
void checkBusOff()
{
    uint32_t now = millis();
    if (now - busOffCheckMs < 10) return;
    busOffCheckMs = now;
    twai_status_info_t st;
    if (twai_get_status_info(&st) == ESP_OK && st.state == TWAI_STATE_BUS_OFF)
        twai_initiate_recovery();
}

// ── loop ──────────────────────────────────────────────────────────────────────
void loop()
{
    pollRx();
#ifdef USE_SERIAL
    pollSerial();
#else
    wsServer.loop();
#endif
    checkBusOff();
    ledUpdate();
}
