/**
 * LTP Stream (ESP32) - configuration
 *
 * Pattern-only LED controller on WiFi: an LTP sink that shows its own
 * patterns and takes no pixel stream. Every pixel is calculated while the
 * frame goes out, so there is no frame buffer.
 *
 * Runs on the ESP32 family supported by the Arduino core 3.x (ESP32, C3, S3).
 */

#ifndef LTP_STREAM_ESP32_CONFIG_H
#define LTP_STREAM_ESP32_CONFIG_H

#include <Arduino.h>

// ============================================================================
// LED OUTPUT
// ============================================================================

// Frame-rate limit only, since nothing is stored per pixel: a WS2812 frame
// takes 30 us per pixel, so 4000 pixels refresh 8 times a second.
#define MAX_PIXELS              4000

// WS2812 data pin (RMT output). Avoid the strapping pins of the chip in use.
#ifndef WS2812_PIN
#if CONFIG_IDF_TARGET_ESP32
#define WS2812_PIN              16
#else
#define WS2812_PIN              3
#endif
#endif

// APA102 / SK9822 on the board's default SPI pins unless overridden.
#ifndef APA102_DATA_PIN
#define APA102_DATA_PIN         MOSI
#endif
#ifndef APA102_CLOCK_PIN
#define APA102_CLOCK_PIN        SCK
#endif
// The rate ltp_serial_v2 drives APA102 at; wiring limits it before the LEDs do.
#define APA102_SPI_HZ           4000000

// Quiet time that makes WS2812 show a frame. Newer WS2812B need 280 us.
#define LED_LATCH_US            300

// A frame that never changes (Off, Solid) is sent again at this interval, so
// a strip that lost power for a moment recovers.
#define STATIC_REFRESH_MS       1000

// ============================================================================
// NETWORK
// ============================================================================

#define WIFI_CONNECT_TIMEOUT    30000   // ms
#define WIFI_RECONNECT_DELAY    5000    // ms between retries
#define TCP_SERVER_PORT         5000    // LTP control channel

// 0.3: device-auth claim protocol and X25519 + PIN pairing, as ltp_esp32c3_oled.
#define PROTOCOL_VERSION        "0.3"

// Telnet console (port 23). Off by default: it is an unauthenticated shell on
// the LAN. Build with -DLTP_ENABLE_TELNET=1 to have it for debugging.
#ifndef LTP_ENABLE_TELNET
#define LTP_ENABLE_TELNET       0
#endif

// ============================================================================
// DEVICE
// ============================================================================

#define FIRMWARE_VERSION_MAJOR  1
#define FIRMWARE_VERSION_MINOR  0
#define DEVICE_NAME_DEFAULT     "LTP-Stream-ESP"
#define FIRMWARE_NAME           "ltp-stream-esp32"
#define DEVICE_DESCRIPTION      "ESP32 LED pattern generator"

#define NVS_NAMESPACE           "ltp_stream"

#define SERIAL_BAUD             115200
#define TERMINAL_LINE_MAX       128

// ============================================================================
// SETTINGS KEPT BESIDE THE PATTERN SETTINGS (FxConfig, ltp_fx_controls.h)
// ============================================================================

struct DeviceConfig {
    char wifiSsid[33];
    char wifiPassword[65];
    // Layer 2 device auth, as in ltp_esp32c3_oled: off means open (Level 0).
    uint8_t authEnabled;        // 0 = open, 1 = a controller must claim the device
    uint8_t authPsk[16];        // pre-shared key, set over USB or by pairing
    uint8_t authReadOpen;       // 1 = reads allowed without a claim
};

#endif // LTP_STREAM_ESP32_CONFIG_H
