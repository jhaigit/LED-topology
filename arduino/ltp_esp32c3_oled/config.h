/**
 * ESP32-C3 OLED Sink - Hardware Configuration
 *
 * Pin assignments and constants for the ESP32-C3 with 72x40 I2C OLED.
 */

#ifndef ESP32C3_OLED_CONFIG_H
#define ESP32C3_OLED_CONFIG_H

// ============================================================================
// OLED Display Configuration
// ============================================================================

#define OLED_WIDTH          72
#define OLED_HEIGHT         40
#define OLED_TOTAL_PIXELS   (OLED_WIDTH * OLED_HEIGHT)  // 2880

// I2C pins
#define OLED_SDA            5       // GPIO5
#define OLED_SCL            6       // GPIO6
#define OLED_ADDR           0x3C    // I2C address

// ============================================================================
// Network Configuration
// ============================================================================

#define WIFI_CONNECT_TIMEOUT    30000   // 30 seconds
#define WIFI_RECONNECT_DELAY    5000    // 5 seconds between retries
#define TCP_SERVER_PORT         5000    // LTP protocol port
#define MDNS_SERVICE_NAME       "_ltp"  // mDNS service type
#define MDNS_PROTOCOL           "_tcp"

// Protocol version string (0.2 adds Layer 2 device-auth claim protocol)
#define PROTOCOL_VERSION        "0.2"

// Telnet console (port 23). Off by default: it is an unauthenticated shell
// on the LAN (proposal §Problem). Define LTP_ENABLE_TELNET=1 at build time
// to restore it for debugging.
#ifndef LTP_ENABLE_TELNET
#define LTP_ENABLE_TELNET       0
#endif

// ============================================================================
// Device Information
// ============================================================================

#define FIRMWARE_VERSION_MAJOR  1
#define FIRMWARE_VERSION_MINOR  0
#define DEVICE_NAME_DEFAULT     "LTP-OLED"
#define DEVICE_NAME_MAX_LEN     16
#define FIRMWARE_NAME           "ltp-esp32c3-oled"

// ============================================================================
// Local Mode Configuration
// ============================================================================

#define LOCAL_MODE_BLANK        0       // Display off
#define LOCAL_MODE_INFO         1       // Show IP/name/stats
#define LOCAL_MODE_CLOCK        2       // Digital clock (NTP)
#define LOCAL_MODE_PATTERN      3       // Test pattern
#define LOCAL_MODE_CYCLE        255     // Cycle through modes
#define LOCAL_MODE_COUNT        4       // Number of actual modes (excluding cycle)

#define LOCAL_MODE_CYCLE_TIME   10000   // Time per mode in cycle (ms)

// Clock / NTP
#define CLOCK_NTP_SERVER        "pool.ntp.org"
#define CLOCK_NTP_SYNC_INTERVAL 3600
#define CLOCK_DEFAULT_TZ        "PST8PDT,M3.2.0,M11.1.0"

// ============================================================================
// Configuration Storage (NVS)
// ============================================================================

#define NVS_NAMESPACE           "ltp_oled"
#define CONFIG_MAGIC            0x4C544F    // "LTO"
#define CONFIG_VERSION          2           // v2: adds Layer 2 auth PSK

// ============================================================================
// Serial Terminal
// ============================================================================

// ============================================================================
// Color Format Values (matching libltp/types.py ColorFormat)
// ============================================================================

#define COLOR_FMT_GRAYSCALE     0x04
#define COLOR_FMT_MONO_PACKED   0x05

// ============================================================================
// Serial Terminal
// ============================================================================

#define SERIAL_BAUD             115200
#define TERMINAL_LINE_MAX       128

// ============================================================================
// Idle Timeout
// ============================================================================

#define DEFAULT_IDLE_TIMEOUT    600     // 10 minutes default (seconds)

// ============================================================================
// Control IDs (matching LTP protocol)
// ============================================================================

#define CTRL_ID_BRIGHTNESS      0
#define CTRL_ID_IDLE_TIMEOUT    1
#define CTRL_ID_LOCAL_MODE      2
#define CTRL_ID_CYCLE_TIME      3
#define CTRL_ID_SAVE            4
#define CTRL_ID_REBOOT          5

#define NUM_CONTROLS            6
#define NUM_INPUTS              0       // No touch sensors

// ============================================================================
// Device Configuration Structure (stored in NVS)
// ============================================================================

struct DeviceConfig {
    uint32_t magic;
    uint8_t version;
    char wifiSsid[33];
    char wifiPassword[65];
    char deviceName[DEVICE_NAME_MAX_LEN + 1];
    uint8_t brightness;         // OLED contrast 0-255
    uint16_t idleTimeout;
    uint8_t localMode;
    char timezone[48];
    uint16_t cycleTime;         // Seconds per mode when cycling
    // Layer 2 device-auth (proposal §2). authEnabled + 16-byte PSK. When
    // disabled the device is Level 0 (open), exactly as before pairing.
    uint8_t authEnabled;        // 0 = open, 1 = require claim
    uint8_t authPsk[16];        // pre-shared key (set over USB console)
    uint8_t authReadOpen;       // 1 = allow reads without a claim (default)
};

#endif // ESP32C3_OLED_CONFIG_H
