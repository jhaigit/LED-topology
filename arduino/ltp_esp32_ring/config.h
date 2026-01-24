/**
 * ESP32 Ring Controller - Hardware Configuration
 *
 * Pin assignments and hardware constants for the ESP32-based
 * ring controller with APA102 ring and WS2812 satellite LEDs.
 */

#ifndef ESP32_RING_CONFIG_H
#define ESP32_RING_CONFIG_H

// ============================================================================
// LED Configuration
// ============================================================================

// APA102 Ring (main strip)
#define RING_NUM_PIXELS     202
#define RING_DATA_PIN       23      // VSPI MOSI
#define RING_CLOCK_PIN      18      // VSPI CLK
#define RING_SPI_SPEED      8000000 // 8 MHz SPI clock

// WS2812 Satellite LEDs
#define WS2812_NUM_LEDS     4
#define WS2812_DATA_PIN     16      // RMT Channel 0
#define WS2812_DEFAULT_OFFSET 0     // Ring position offset for WS2812 mapping

// LED color order
#define RING_COLOR_ORDER    BGR     // APA102 typical order
#define WS2812_COLOR_ORDER  GRB     // WS2812 typical order

// ============================================================================
// Touch Sensor Configuration
// ============================================================================

#define TOUCH_NUM_SENSORS   4
#define TOUCH_PIN_0         4       // T0 - for WS2812 LED 0
#define TOUCH_PIN_1         15      // T3 - for WS2812 LED 1
#define TOUCH_PIN_2         13      // T4 - for WS2812 LED 2
#define TOUCH_PIN_3         12      // T5 - for WS2812 LED 3

// Touch detection parameters
#define TOUCH_THRESHOLD_RATIO   0.6     // Trigger at 60% of baseline
#define TOUCH_DEBOUNCE_MS       100     // Debounce time
#define TOUCH_CALIBRATION_SAMPLES 10    // Samples for calibration average

// ============================================================================
// Status LED Configuration
// ============================================================================

#define STATUS_LED_PIN      2       // Built-in LED on most ESP32 DevKits

// Status LED patterns (period in ms)
#define STATUS_WIFI_CONNECTING  250     // Fast blink
#define STATUS_WIFI_CONNECTED   0       // Solid on
#define STATUS_CLIENT_ACTIVE    100     // Very fast blink
#define STATUS_ERROR            1000    // Slow blink

// ============================================================================
// Network Configuration
// ============================================================================

#define WIFI_CONNECT_TIMEOUT    30000   // 30 seconds
#define WIFI_RECONNECT_DELAY    5000    // 5 seconds between retries
#define TCP_SERVER_PORT         5000    // LTP protocol port
#define MDNS_SERVICE_NAME       "_ltp"  // mDNS service type
#define MDNS_PROTOCOL           "_tcp"

// Protocol version string
#define PROTOCOL_VERSION        "0.1"

// ============================================================================
// Protocol Configuration
// ============================================================================

// Maximum payload size for LTP protocol
// ESP32 has plenty of RAM, use larger buffer for full frames
#define LTP_MAX_PAYLOAD         1024

// Protocol timing
#define PROTOCOL_TIMEOUT_MS     5000    // Command timeout
#define STATUS_UPDATE_DEFAULT   0       // Default status update interval (0 = disabled)

// ============================================================================
// Device Information
// ============================================================================

#define FIRMWARE_VERSION_MAJOR  1
#define FIRMWARE_VERSION_MINOR  0
#define DEVICE_NAME_DEFAULT     "LTP-Ring"
#define DEVICE_NAME_MAX_LEN     16

// ============================================================================
// Local Mode Configuration
// ============================================================================

#define LOCAL_MODE_BLANK        0       // No local animation (LEDs off)
#define LOCAL_MODE_CYLON        1       // Scanning eye (wraps around ring)
#define LOCAL_MODE_RAINBOW      2       // Rainbow rotation
#define LOCAL_MODE_FIRE         3       // Fire effect
#define LOCAL_MODE_SPARKLE      4       // Random sparkles
#define LOCAL_MODE_CHASE        5       // Color chase (seamless loop)
#define LOCAL_MODE_CYCLE        255     // Cycle through all modes
#define LOCAL_MODE_COUNT        6       // Number of actual modes (excluding cycle)

#define LOCAL_MODE_CYCLE_TIME   10000   // Time per mode in cycle (ms)

// ============================================================================
// Configuration Storage (NVS)
// ============================================================================

#define NVS_NAMESPACE           "ltp_ring"
#define CONFIG_MAGIC            0x4C5452    // "LTR" magic number
#define CONFIG_VERSION          1

// ============================================================================
// Serial Terminal
// ============================================================================

#define SERIAL_BAUD             115200
#define TERMINAL_LINE_MAX       128     // Max command line length

// ============================================================================
// Idle Timeout
// ============================================================================

#define DEFAULT_IDLE_TIMEOUT    600     // 10 minutes default (seconds)

// ============================================================================
// WS2812 Mirror Configuration
// ============================================================================

// Each WS2812 samples pixels from the ring to mirror the ring color
// Ring spacing: 202 / 4 = 50.5 pixels between each WS2812
#define WS2812_RING_SPACING     50      // Approximate spacing

// Weighted average sampling for WS2812 colors
// Sample 5 pixels centered on the ring position
// Weights: outer=1, adjacent=2, center=3
#define WS2812_SAMPLE_RADIUS    2       // Sample +-2 pixels from center
#define WS2812_WEIGHT_CENTER    3
#define WS2812_WEIGHT_ADJACENT  2
#define WS2812_WEIGHT_OUTER     1

// Flash parameters for touch feedback
#define WS2812_FLASH_COUNT      3       // Number of flashes
#define WS2812_FLASH_ON_MS      150     // On time per flash
#define WS2812_FLASH_OFF_MS     150     // Off time per flash

// ============================================================================
// Control IDs (matching LTP protocol standard + custom)
// ============================================================================

#define CTRL_ID_BRIGHTNESS      0
#define CTRL_ID_GAMMA           1
#define CTRL_ID_IDLE_TIMEOUT    2
#define CTRL_ID_AUTO_SHOW       3
#define CTRL_ID_FRAME_ACK       4
#define CTRL_ID_STATUS_INTERVAL 5
#define CTRL_ID_LOCAL_MODE      6
#define CTRL_ID_WS2812_OFFSET   7       // Custom: WS2812 ring position offset

#define NUM_CONTROLS            8

// ============================================================================
// Input IDs
// ============================================================================

#define NUM_INPUTS              4       // 4 touch sensors

// ============================================================================
// Device Configuration Structure (stored in NVS)
// ============================================================================

struct DeviceConfig {
    uint32_t magic;
    uint8_t version;
    char wifiSsid[33];
    char wifiPassword[65];
    char deviceName[DEVICE_NAME_MAX_LEN + 1];
    uint8_t brightness;
    uint8_t gamma;
    uint16_t idleTimeout;
    uint8_t localMode;
    uint8_t ws2812Offset;
    bool inputEventsEnabled;
};

#endif // ESP32_RING_CONFIG_H
