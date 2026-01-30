/**
 * LTP Serial Protocol v2 - Arduino Implementation
 *
 * LED strip controller using the LTP Serial Protocol v2.
 * Default configuration: 160 LPD8806 pixels.
 *
 * To change LED chip:
 *   1. Include the appropriate driver header
 *   2. Change the LedDriver instantiation in setup()
 *
 * Pin Configuration (Arduino Uno/Nano):
 *   - Data: Pin 11 (MOSI)
 *   - Clock: Pin 13 (SCK)
 *   - Serial: USB (115200 baud default)
 */

// Maximum payload size - MUST be defined before including ltp_protocol.h
// 160 pixels * 3 bytes = 480 bytes for full frame
#define LTP_MAX_PAYLOAD     512
#define MAX_PAYLOAD_SIZE    LTP_MAX_PAYLOAD

#include <EEPROM.h>
#include <ltp_protocol.h>
#include "led_driver.h"
#include "led_driver_lpd8806.h"

// Build version info (set by Makefile, fallback for IDE builds)
#ifndef GIT_COMMIT
#define GIT_COMMIT "unknown"
#endif
#ifndef BUILD_DATE
#define BUILD_DATE "00000000"
#endif

// ============================================================================
// CONFIGURATION - Modify these for your setup
// ============================================================================

// LED strip configuration
#define NUM_PIXELS          160
#define DATA_PIN            11
#define CLOCK_PIN           13
#define USE_HARDWARE_SPI    true

// Serial configuration
#define SERIAL_BAUD         115200

// Device info
#define FIRMWARE_VERSION_MAJOR  1
#define FIRMWARE_VERSION_MINOR  0
#define DEVICE_NAME         "LTP-LPD8806"
#define FIRMWARE_NAME       "ltp-serial-v2"

// ============================================================================
// GLOBALS
// ============================================================================

// LED driver - change this line to use a different LED chip
LedDriverLPD8806 leds(NUM_PIXELS, DATA_PIN, CLOCK_PIN, USE_HARDWARE_SPI);

// Protocol handler
LtpProtocol protocol(Serial, MAX_PAYLOAD_SIZE);

// EEPROM configuration
#define CONFIG_MAGIC        0x4C54  // "LT" - magic number for validation
#define CONFIG_VERSION      2       // Bumped for localMode addition
#define EEPROM_CONFIG_ADDR  0
#define DEFAULT_IDLE_TIMEOUT 600  // 10 minutes default

// Local display modes
#define LOCAL_MODE_BLANK    0   // No local animation (default)
#define LOCAL_MODE_CYLON    1   // Scanning red eye
#define LOCAL_MODE_RAINBOW  2   // Rainbow cycle
#define LOCAL_MODE_FIRE     3   // Fire effect
#define LOCAL_MODE_SPARKLE  4   // Random sparkles
#define LOCAL_MODE_CHASE    5   // Color chase
#define LOCAL_MODE_CYCLE    255 // Cycle through all modes
#define LOCAL_MODE_COUNT    6   // Number of actual modes (excluding cycle)

// Device configuration (stored in EEPROM)
struct Config {
    uint16_t magic;             // Magic number for validation
    uint8_t version;            // Config version
    uint8_t brightness;
    uint8_t gamma;              // 2.2 * 10
    uint16_t idleTimeout;       // 0 = disabled, else seconds
    bool autoShow;
    bool frameAck;
    uint16_t statusInterval;
    uint8_t localMode;          // Local display mode (0 = blank/off)
    uint16_t cycleTime;         // Seconds per mode when cycling
} config = {
    CONFIG_MAGIC,
    CONFIG_VERSION,
    255,                        // brightness
    22,                         // gamma (2.2)
    DEFAULT_IDLE_TIMEOUT,       // idle timeout
    false,                      // autoShow
    false,                      // frameAck
    0,                          // statusInterval
    LOCAL_MODE_BLANK,           // localMode (default blank)
    10                          // cycleTime (seconds)
};

// Idle timeout tracking
uint32_t lastActivityTime = 0;
bool isIdle = false;

// Local mode state
bool localModeActive = false;       // True if local mode is running
uint8_t currentDisplayMode = 0;     // Actual mode being displayed (for cycle)
uint32_t lastModeUpdate = 0;        // Last animation frame time
uint32_t modeStartTime = 0;         // When current mode started (for cycle)
uint16_t modePosition = 0;          // Animation position/state
uint8_t modeHue = 0;                // Hue for rainbow/chase effects

// Statistics
struct {
    uint32_t framesReceived = 0;
    uint32_t framesDisplayed = 0;
    uint32_t bytesReceived = 0;
    uint16_t checksumErrors = 0;
    uint16_t bufferOverflows = 0;
    uint32_t startTime = 0;
} stats;

// Heartbeat LED - only available if LED_BUILTIN doesn't conflict with SPI clock
// On Arduino Uno, LED_BUILTIN (pin 13) is also SCK, so we can't use it with hardware SPI
#if defined(LED_BUILTIN) && (LED_BUILTIN != CLOCK_PIN || !USE_HARDWARE_SPI)
    #define HEARTBEAT_ENABLED   true
    #define HEARTBEAT_PIN       LED_BUILTIN
#else
    #define HEARTBEAT_ENABLED   false
    #define HEARTBEAT_PIN       -1
#endif
#define HEARTBEAT_INTERVAL  500  // ms
uint32_t lastHeartbeat = 0;
bool heartbeatState = false;

// Control definitions
#define NUM_CONTROLS 8

// Control metadata for INFO_CONTROLS response
struct ControlDef {
    uint8_t id;
    uint8_t type;
    int16_t minVal;
    int16_t maxVal;
    const char* name;
};

static const ControlDef controlDefs[NUM_CONTROLS] = {
    { CTRL_ID_BRIGHTNESS,      CTRL_TYPE_UINT8,  0,     255,   "brightness" },
    { CTRL_ID_GAMMA,           CTRL_TYPE_UINT8,  10,    30,    "gamma" },
    { CTRL_ID_IDLE_TIMEOUT,    CTRL_TYPE_UINT16, 0,     32767, "idle_timeout" },
    { CTRL_ID_AUTO_SHOW,       CTRL_TYPE_BOOL,   0,     1,     "auto_show" },
    { CTRL_ID_FRAME_ACK,       CTRL_TYPE_BOOL,   0,     1,     "frame_ack" },
    { CTRL_ID_STATUS_INTERVAL, CTRL_TYPE_UINT16, 0,     32767, "status_interval" },
    { CTRL_ID_LOCAL_MODE,      CTRL_TYPE_UINT8,  0,     255,   "local_mode" },
    { CTRL_ID_CYCLE_TIME,      CTRL_TYPE_UINT16, 1000,  32767, "cycle_time" },
};

// ============================================================================
// CONFIG PERSISTENCE
// ============================================================================

void saveConfig() {
    config.magic = CONFIG_MAGIC;
    config.version = CONFIG_VERSION;
    EEPROM.put(EEPROM_CONFIG_ADDR, config);
}

void loadConfig() {
    Config stored;
    EEPROM.get(EEPROM_CONFIG_ADDR, stored);

    // Validate stored config
    if (stored.magic == CONFIG_MAGIC && stored.version == CONFIG_VERSION) {
        config = stored;
    }
    // Otherwise keep defaults
}

void resetConfig() {
    config.magic = CONFIG_MAGIC;
    config.version = CONFIG_VERSION;
    config.brightness = 255;
    config.gamma = 22;
    config.idleTimeout = DEFAULT_IDLE_TIMEOUT;
    config.autoShow = false;
    config.frameAck = false;
    config.statusInterval = 0;
    config.localMode = LOCAL_MODE_BLANK;
    saveConfig();
}

// ============================================================================
// IDLE TIMEOUT
// ============================================================================

void resetActivityTimer() {
    lastActivityTime = millis();
    if (isIdle) {
        isIdle = false;
        // Restore display by refreshing current pixels
        leds.show();
    }
}

void checkIdleTimeout() {
    if (config.idleTimeout == 0) return;  // Disabled

    uint32_t now = millis();
    uint32_t elapsed = (now - lastActivityTime) / 1000;  // Convert to seconds

    if (!isIdle && elapsed >= config.idleTimeout) {
        isIdle = true;
        leds.clear();
        leds.show();
    }
}

// ============================================================================
// LOCAL DISPLAY MODES
// ============================================================================

// HSV to RGB conversion (h: 0-255, s: 0-255, v: 0-255)
void hsvToRgb(uint8_t h, uint8_t s, uint8_t v, uint8_t& r, uint8_t& g, uint8_t& b) {
    if (s == 0) {
        r = g = b = v;
        return;
    }

    uint8_t region = h / 43;
    uint8_t remainder = (h - (region * 43)) * 6;

    uint8_t p = (v * (255 - s)) >> 8;
    uint8_t q = (v * (255 - ((s * remainder) >> 8))) >> 8;
    uint8_t t = (v * (255 - ((s * (255 - remainder)) >> 8))) >> 8;

    switch (region) {
        case 0:  r = v; g = t; b = p; break;
        case 1:  r = q; g = v; b = p; break;
        case 2:  r = p; g = v; b = t; break;
        case 3:  r = p; g = q; b = v; break;
        case 4:  r = t; g = p; b = v; break;
        default: r = v; g = p; b = q; break;
    }
}

// Exit local mode (called when serial display commands arrive)
void exitLocalMode() {
    if (localModeActive) {
        localModeActive = false;
        leds.clear();  // Clear any local mode data
    }
}

// Start or switch local mode
void startLocalMode(uint8_t mode) {
    config.localMode = mode;
    modePosition = 0;
    modeHue = 0;
    lastModeUpdate = millis();
    modeStartTime = millis();

    if (mode == LOCAL_MODE_BLANK) {
        localModeActive = false;
        leds.clear();
        leds.show();
    } else {
        localModeActive = true;
        if (mode == LOCAL_MODE_CYCLE) {
            currentDisplayMode = LOCAL_MODE_CYLON;  // Start with first real mode
        } else {
            currentDisplayMode = mode;
        }
    }
}

// Cylon (scanning red eye) animation
void updateCylon() {
    static bool direction = true;
    static uint16_t lastPosition = 0xFFFF;
    const uint8_t eyeSize = 3;
    const uint8_t fadeAmount = 64;

    // Detect mode restart (modePosition reset to 0 by startLocalMode)
    // or underflow protection
    if (modePosition == 0 && lastPosition > 1) {
        direction = true;  // Reset direction on mode start
    }
    if (modePosition > NUM_PIXELS) {
        modePosition = 0;
        direction = true;
    }
    lastPosition = modePosition;

    // Fade all pixels using driver abstraction
    for (uint16_t i = 0; i < NUM_PIXELS; i++) {
        uint8_t r, g, b;
        leds.getPixel(i, r, g, b);
        r = r > fadeAmount ? r - fadeAmount : 0;
        g = g > fadeAmount ? g - fadeAmount : 0;
        b = b > fadeAmount ? b - fadeAmount : 0;
        leds.setPixel(i, r, g, b);
    }

    // Draw the eye
    for (uint8_t i = 0; i < eyeSize; i++) {
        int16_t pos = modePosition + i - eyeSize/2;
        if (pos >= 0 && pos < NUM_PIXELS) {
            uint8_t brightness = 255 - abs(i - eyeSize/2) * 60;
            leds.setPixel(pos, brightness, 0, 0);
        }
    }

    // Move position
    if (direction) {
        modePosition++;
        if (modePosition >= NUM_PIXELS - 1) direction = false;
    } else {
        modePosition--;
        if (modePosition == 0) direction = true;
    }

    leds.show();
}

// Rainbow cycle animation
void updateRainbow() {
    for (uint16_t i = 0; i < NUM_PIXELS; i++) {
        uint8_t pixelHue = modeHue + (i * 256 / NUM_PIXELS);
        uint8_t r, g, b;
        hsvToRgb(pixelHue, 255, 200, r, g, b);
        leds.setPixel(i, r, g, b);
    }
    modeHue++;
    leds.show();
}

// Fire effect animation
void updateFire() {
    static uint8_t heat[NUM_PIXELS > 256 ? 256 : NUM_PIXELS];
    const uint8_t cooling = 55;
    const uint8_t sparking = 120;
    uint16_t numPixels = NUM_PIXELS > 256 ? 256 : NUM_PIXELS;

    // Cool down every cell
    for (uint16_t i = 0; i < numPixels; i++) {
        heat[i] = heat[i] > random(0, ((cooling * 10) / numPixels) + 2) ?
                  heat[i] - random(0, ((cooling * 10) / numPixels) + 2) : 0;
    }

    // Heat rises
    for (uint16_t i = numPixels - 1; i >= 2; i--) {
        heat[i] = (heat[i - 1] + heat[i - 2] + heat[i - 2]) / 3;
    }

    // Randomly ignite sparks near bottom
    if (random(255) < sparking) {
        uint8_t y = random(7);
        heat[y] = heat[y] + random(160, 255);
        if (heat[y] > 255) heat[y] = 255;
    }

    // Map heat to colors
    for (uint16_t i = 0; i < numPixels; i++) {
        uint8_t t192 = (heat[i] * 192) / 255;
        uint8_t r, g, b;

        if (t192 < 64) {
            r = t192 * 4;
            g = 0;
            b = 0;
        } else if (t192 < 128) {
            r = 255;
            g = (t192 - 64) * 4;
            b = 0;
        } else {
            r = 255;
            g = 255;
            b = (t192 - 128) * 4;
        }

        leds.setPixel(i, r, g, b);
    }
    leds.show();
}

// Sparkle animation
void updateSparkle() {
    const uint8_t fadeAmount = 20;

    // Fade all pixels using driver abstraction
    for (uint16_t i = 0; i < NUM_PIXELS; i++) {
        uint8_t r, g, b;
        leds.getPixel(i, r, g, b);
        r = r > fadeAmount ? r - fadeAmount : 0;
        g = g > fadeAmount ? g - fadeAmount : 0;
        b = b > fadeAmount ? b - fadeAmount : 0;
        leds.setPixel(i, r, g, b);
    }

    // Add random sparkles
    for (uint8_t i = 0; i < 3; i++) {
        uint16_t pos = random(NUM_PIXELS);
        uint8_t r, g, b;
        hsvToRgb(random(256), 200, 255, r, g, b);
        leds.setPixel(pos, r, g, b);
    }

    leds.show();
}

// Color chase animation
void updateChase() {
    const uint8_t chaseLength = 5;

    leds.clear();

    for (uint8_t i = 0; i < chaseLength; i++) {
        uint16_t pos = (modePosition + i) % NUM_PIXELS;
        uint8_t r, g, b;
        hsvToRgb(modeHue, 255, 255 - i * 40, r, g, b);
        leds.setPixel(pos, r, g, b);
    }

    modePosition = (modePosition + 1) % NUM_PIXELS;
    modeHue += 2;

    leds.show();
}

// Update local mode animation (called from loop)
void updateLocalMode() {
    if (!localModeActive) return;

    uint32_t now = millis();
    uint32_t interval;

    // Different update rates for different modes
    switch (currentDisplayMode) {
        case LOCAL_MODE_CYLON:   interval = 20; break;
        case LOCAL_MODE_RAINBOW: interval = 20; break;
        case LOCAL_MODE_FIRE:    interval = 30; break;
        case LOCAL_MODE_SPARKLE: interval = 30; break;
        case LOCAL_MODE_CHASE:   interval = 40; break;
        default: interval = 50; break;
    }

    if (now - lastModeUpdate < interval) return;
    lastModeUpdate = now;

    // Handle cycle mode - switch based on cycleTime
    if (config.localMode == LOCAL_MODE_CYCLE) {
        if (now - modeStartTime > (uint32_t)config.cycleTime * 1000) {
            modeStartTime = now;
            currentDisplayMode++;
            if (currentDisplayMode >= LOCAL_MODE_COUNT) {
                currentDisplayMode = LOCAL_MODE_CYLON;
            }
            modePosition = 0;
            leds.clear();
        }
    }

    // Run the appropriate animation
    switch (currentDisplayMode) {
        case LOCAL_MODE_CYLON:   updateCylon(); break;
        case LOCAL_MODE_RAINBOW: updateRainbow(); break;
        case LOCAL_MODE_FIRE:    updateFire(); break;
        case LOCAL_MODE_SPARKLE: updateSparkle(); break;
        case LOCAL_MODE_CHASE:   updateChase(); break;
        default: break;
    }
}

// ============================================================================
// PROTOCOL HANDLERS
// ============================================================================

void sendHello() {
    uint8_t payload[12];
    payload[0] = LTP_PROTOCOL_MAJOR;
    payload[1] = LTP_PROTOCOL_MINOR;
    payload[2] = (FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR; // BCD
    payload[3] = 0; // BCD low byte
    payload[4] = 1; // Strip count
    payload[5] = NUM_PIXELS & 0xFF;
    payload[6] = NUM_PIXELS >> 8;
    payload[7] = leds.getColorFormat();
    payload[8] = CAPS_BRIGHTNESS | CAPS_EXTENDED; // Caps byte 1
    payload[9] = CAPS_PIXEL_READBACK | CAPS_SUBMATRIX | CAPS_EEPROM; // Caps byte 2 (extended)
    payload[10] = NUM_CONTROLS; // Control count
    payload[11] = 0; // Input count (no inputs in this example)

    protocol.sendPacket(CMD_HELLO, payload, 12);
}

void handleGetInfo(const uint8_t* payload, uint16_t length) {
    if (length < 1) {
        protocol.sendNak(CMD_GET_INFO, ERR_INVALID_LENGTH);
        return;
    }

    uint8_t infoType = payload[0];
    uint8_t response[200];  // Large enough for INFO_CONTROLS
    uint16_t respLen = 0;

    switch (infoType) {
        case INFO_ALL:
            response[respLen++] = LTP_PROTOCOL_MAJOR;
            response[respLen++] = LTP_PROTOCOL_MINOR;
            response[respLen++] = (FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR;
            response[respLen++] = 0;
            response[respLen++] = 1; // Strip count
            response[respLen++] = NUM_PIXELS & 0xFF;
            response[respLen++] = NUM_PIXELS >> 8;
            response[respLen++] = leds.getColorFormat();
            response[respLen++] = CAPS_BRIGHTNESS | CAPS_EXTENDED;
            response[respLen++] = CAPS_PIXEL_READBACK | CAPS_SUBMATRIX;
            response[respLen++] = NUM_CONTROLS;
            // Device name (null-terminated, max 16 bytes)
            {
                const char* name = DEVICE_NAME;
                uint8_t i = 0;
                while (name[i] && i < 15) {
                    response[respLen++] = name[i++];
                }
                response[respLen++] = 0;
            }
            break;

        case INFO_VERSION:
            response[respLen++] = LTP_PROTOCOL_MAJOR;
            response[respLen++] = LTP_PROTOCOL_MINOR;
            response[respLen++] = (FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR;
            response[respLen++] = 0;
            break;

        case INFO_BUILD:
            // Firmware name (null-terminated)
            {
                const char* name = FIRMWARE_NAME;
                uint8_t i = 0;
                while (name[i] && i < 15) {
                    response[respLen++] = name[i++];
                }
                response[respLen++] = 0;
            }
            // Git commit hash (null-terminated)
            {
                const char* commit = GIT_COMMIT;
                uint8_t i = 0;
                while (commit[i] && i < 15) {
                    response[respLen++] = commit[i++];
                }
                response[respLen++] = 0;
            }
            // Build date YYYYMMDD (null-terminated)
            {
                const char* date = BUILD_DATE;
                uint8_t i = 0;
                while (date[i] && i < 15) {
                    response[respLen++] = date[i++];
                }
                response[respLen++] = 0;
            }
            break;

        case INFO_STRIPS:
            response[respLen++] = 1; // Strip count
            // Strip 0 definition
            response[respLen++] = 0; // Strip ID
            response[respLen++] = NUM_PIXELS & 0xFF;
            response[respLen++] = NUM_PIXELS >> 8;
            response[respLen++] = leds.getColorFormat();
            response[respLen++] = leds.getLedType();
            response[respLen++] = DATA_PIN;
            response[respLen++] = CLOCK_PIN;
            response[respLen++] = 0; // Flags
            break;

        case INFO_STATUS:
            response[respLen++] = 1; // Running
            response[respLen++] = config.brightness;
            response[respLen++] = 0xFF; // Temp N/A
            response[respLen++] = 0x7F;
            response[respLen++] = 0xFF; // Voltage N/A
            response[respLen++] = 0xFF;
            response[respLen++] = 0; // No error
            break;

        case INFO_STATS:
            // Frames received (4 bytes)
            response[respLen++] = stats.framesReceived & 0xFF;
            response[respLen++] = (stats.framesReceived >> 8) & 0xFF;
            response[respLen++] = (stats.framesReceived >> 16) & 0xFF;
            response[respLen++] = (stats.framesReceived >> 24) & 0xFF;
            // Frames displayed (4 bytes)
            response[respLen++] = stats.framesDisplayed & 0xFF;
            response[respLen++] = (stats.framesDisplayed >> 8) & 0xFF;
            response[respLen++] = (stats.framesDisplayed >> 16) & 0xFF;
            response[respLen++] = (stats.framesDisplayed >> 24) & 0xFF;
            // Bytes received (4 bytes)
            response[respLen++] = stats.bytesReceived & 0xFF;
            response[respLen++] = (stats.bytesReceived >> 8) & 0xFF;
            response[respLen++] = (stats.bytesReceived >> 16) & 0xFF;
            response[respLen++] = (stats.bytesReceived >> 24) & 0xFF;
            // Checksum errors (2 bytes)
            response[respLen++] = stats.checksumErrors & 0xFF;
            response[respLen++] = stats.checksumErrors >> 8;
            // Buffer overflows (2 bytes)
            response[respLen++] = stats.bufferOverflows & 0xFF;
            response[respLen++] = stats.bufferOverflows >> 8;
            // Uptime (4 bytes, seconds)
            {
                uint32_t uptime = (millis() - stats.startTime) / 1000;
                response[respLen++] = uptime & 0xFF;
                response[respLen++] = (uptime >> 8) & 0xFF;
                response[respLen++] = (uptime >> 16) & 0xFF;
                response[respLen++] = (uptime >> 24) & 0xFF;
            }
            break;

        case INFO_CONTROLS:
            // Return control metadata: count, then for each: id, type, min(2), max(2), name(null-term)
            response[respLen++] = NUM_CONTROLS;
            for (uint8_t i = 0; i < NUM_CONTROLS; i++) {
                const ControlDef& ctrl = controlDefs[i];
                response[respLen++] = ctrl.id;
                response[respLen++] = ctrl.type;
                response[respLen++] = ctrl.minVal & 0xFF;
                response[respLen++] = (ctrl.minVal >> 8) & 0xFF;
                response[respLen++] = ctrl.maxVal & 0xFF;
                response[respLen++] = (ctrl.maxVal >> 8) & 0xFF;
                // Copy name (null-terminated)
                const char* name = ctrl.name;
                while (*name && respLen < sizeof(response) - 1) {
                    response[respLen++] = *name++;
                }
                response[respLen++] = 0;
            }
            break;

        default:
            protocol.sendNak(CMD_GET_INFO, ERR_INVALID_PARAM);
            return;
    }

    protocol.sendPacket(CMD_INFO_RESPONSE, response, respLen);
}

void handleShow(const uint8_t* payload, uint16_t length) {
    resetActivityTimer();
    leds.show();
    stats.framesDisplayed++;

    // Frame acknowledgment if enabled
    if (config.frameAck && length >= 2) {
        uint8_t response[4];
        response[0] = payload[0]; // Frame number low
        response[1] = payload[1]; // Frame number high
        uint16_t timestamp = millis() & 0xFFFF;
        response[2] = timestamp & 0xFF;
        response[3] = timestamp >> 8;
        protocol.sendPacket(CMD_FRAME_ACK, response, 4);
    }
}

void handlePixelSetAll(const uint8_t* payload, uint16_t length) {
    if (length < 4) {
        protocol.sendNak(CMD_PIXEL_SET_ALL, ERR_INVALID_LENGTH);
        return;
    }

    uint8_t stripId = payload[0];
    if (stripId != 0 && stripId != STRIP_ALL) {
        protocol.sendNak(CMD_PIXEL_SET_ALL, ERR_INVALID_PARAM);
        return;
    }

    exitLocalMode();  // Exit local mode on display command

    uint8_t r = payload[1];
    uint8_t g = payload[2];
    uint8_t b = payload[3];

    leds.fill(r, g, b);
    stats.framesReceived++;

    if (config.autoShow) {
        leds.show();
        stats.framesDisplayed++;
    }
}

void handlePixelSetRange(const uint8_t* payload, uint16_t length) {
    if (length < 8) {
        protocol.sendNak(CMD_PIXEL_SET_RANGE, ERR_INVALID_LENGTH);
        return;
    }

    uint8_t stripId = payload[0];
    if (stripId != 0) {
        protocol.sendNak(CMD_PIXEL_SET_RANGE, ERR_INVALID_PARAM);
        return;
    }

    uint16_t start = payload[1] | ((uint16_t)payload[2] << 8);
    uint16_t end = payload[3] | ((uint16_t)payload[4] << 8);
    uint8_t r = payload[5];
    uint8_t g = payload[6];
    uint8_t b = payload[7];

    if (start >= NUM_PIXELS || end > NUM_PIXELS) {
        protocol.sendNak(CMD_PIXEL_SET_RANGE, ERR_PIXEL_OVERFLOW);
        return;
    }

    exitLocalMode();  // Exit local mode on display command

    leds.fillRange(start, end, r, g, b);
    stats.framesReceived++;

    if (config.autoShow) {
        leds.show();
        stats.framesDisplayed++;
    }
}

void handlePixelFrame(const uint8_t* payload, uint16_t length) {
    if (length < 5) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_INVALID_LENGTH);
        return;
    }

    uint8_t stripId = payload[0];
    if (stripId != 0) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_INVALID_PARAM);
        return;
    }

    uint16_t start = payload[1] | ((uint16_t)payload[2] << 8);
    uint16_t count = payload[3] | ((uint16_t)payload[4] << 8);

    uint16_t dataOffset = 5;
    uint16_t expectedBytes = count * leds.getBytesPerPixel();

    if (length < dataOffset + expectedBytes) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_INVALID_LENGTH);
        return;
    }

    if (start + count > NUM_PIXELS) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_PIXEL_OVERFLOW);
        return;
    }

    exitLocalMode();  // Exit local mode on display command

    // Copy pixel data
    const uint8_t* pixelData = payload + dataOffset;
    uint8_t bpp = leds.getBytesPerPixel();

    for (uint16_t i = 0; i < count; i++) {
        uint16_t offset = i * bpp;
        leds.setPixel(start + i, pixelData[offset], pixelData[offset + 1], pixelData[offset + 2]);
    }

    stats.framesReceived++;
    stats.bytesReceived += expectedBytes;
    resetActivityTimer();

    if (config.autoShow) {
        leds.show();
        stats.framesDisplayed++;
    }
}

void handlePixelSubmatrix(const uint8_t* payload, uint16_t length) {
    // Payload format:
    // [0] strip_id
    // [1-2] matrix_width (LE)
    // [3-4] x_offset (LE)
    // [5-6] y_offset (LE)
    // [7-8] sub_width (LE)
    // [9-10] sub_height (LE)
    // [11] flags
    // [12+] pixel data

    if (length < 12) {
        protocol.sendNak(CMD_PIXEL_SUBMATRIX, ERR_INVALID_LENGTH);
        return;
    }

    uint8_t stripId = payload[0];
    if (stripId != 0) {
        protocol.sendNak(CMD_PIXEL_SUBMATRIX, ERR_INVALID_PARAM);
        return;
    }

    uint16_t matrixWidth = payload[1] | ((uint16_t)payload[2] << 8);
    uint16_t xOffset = payload[3] | ((uint16_t)payload[4] << 8);
    uint16_t yOffset = payload[5] | ((uint16_t)payload[6] << 8);
    uint16_t subWidth = payload[7] | ((uint16_t)payload[8] << 8);
    uint16_t subHeight = payload[9] | ((uint16_t)payload[10] << 8);
    uint8_t flags = payload[11];

    uint16_t dataOffset = 12;
    uint8_t bpp = leds.getBytesPerPixel();
    uint32_t expectedBytes = (uint32_t)subWidth * subHeight * bpp;

    if (length < dataOffset + expectedBytes) {
        protocol.sendNak(CMD_PIXEL_SUBMATRIX, ERR_INVALID_LENGTH);
        return;
    }

    // Validate bounds
    if (matrixWidth == 0 || subWidth == 0 || subHeight == 0) {
        protocol.sendNak(CMD_PIXEL_SUBMATRIX, ERR_INVALID_PARAM);
        return;
    }

    exitLocalMode();  // Exit local mode on display command

    bool serpentine = flags & SUBMATRIX_SERPENTINE;
    const uint8_t* pixelData = payload + dataOffset;

    // Process each pixel in the submatrix
    for (uint16_t row = 0; row < subHeight; row++) {
        uint16_t y = yOffset + row;

        for (uint16_t col = 0; col < subWidth; col++) {
            uint16_t x = xOffset + col;
            uint16_t pixelIndex;

            if (serpentine && (y & 1)) {
                // Odd row - reversed
                pixelIndex = (y + 1) * matrixWidth - 1 - x;
            } else {
                // Even row or linear - normal
                pixelIndex = y * matrixWidth + x;
            }

            // Check bounds
            if (pixelIndex >= NUM_PIXELS) {
                protocol.sendNak(CMD_PIXEL_SUBMATRIX, ERR_PIXEL_OVERFLOW);
                return;
            }

            // Get pixel data from input (row-major order in submatrix)
            uint32_t srcOffset = ((uint32_t)row * subWidth + col) * bpp;
            leds.setPixel(pixelIndex,
                          pixelData[srcOffset],
                          pixelData[srcOffset + 1],
                          pixelData[srcOffset + 2]);
        }
    }

    stats.framesReceived++;
    stats.bytesReceived += expectedBytes;

    if (config.autoShow) {
        leds.show();
        stats.framesDisplayed++;
    }
}

void handleSetControl(const uint8_t* payload, uint16_t length) {
    if (length < 2) {
        protocol.sendNak(CMD_SET_CONTROL, ERR_INVALID_LENGTH);
        return;
    }

    uint8_t controlId = payload[0];

    switch (controlId) {
        case CTRL_ID_BRIGHTNESS:
            config.brightness = payload[1];
            leds.setBrightness(config.brightness);
            break;

        case CTRL_ID_GAMMA:
            if (payload[1] >= 10 && payload[1] <= 30) {
                config.gamma = payload[1];
            } else {
                protocol.sendNak(CMD_SET_CONTROL, ERR_INVALID_PARAM);
                return;
            }
            break;

        case CTRL_ID_IDLE_TIMEOUT:
            if (length >= 3) {
                config.idleTimeout = payload[1] | ((uint16_t)payload[2] << 8);
            }
            break;

        case CTRL_ID_AUTO_SHOW:
            config.autoShow = payload[1] != 0;
            break;

        case CTRL_ID_FRAME_ACK:
            config.frameAck = payload[1] != 0;
            break;

        case CTRL_ID_STATUS_INTERVAL:
            if (length >= 3) {
                config.statusInterval = payload[1] | ((uint16_t)payload[2] << 8);
            }
            break;

        case CTRL_ID_LOCAL_MODE:
            // Validate mode value: 0-5 are valid modes, 255 is cycle mode
            if (payload[1] < LOCAL_MODE_COUNT || payload[1] == LOCAL_MODE_CYCLE) {
                startLocalMode(payload[1]);
            } else {
                protocol.sendNak(CMD_SET_CONTROL, ERR_INVALID_PARAM);
                return;
            }
            break;

        case CTRL_ID_CYCLE_TIME:
            if (length >= 3) {
                config.cycleTime = payload[1] | ((uint16_t)payload[2] << 8);
            }
            break;

        default:
            protocol.sendNak(CMD_SET_CONTROL, ERR_INVALID_PARAM);
            return;
    }

    protocol.sendAck(CMD_SET_CONTROL);
}

void handleGetControl(const uint8_t* payload, uint16_t length) {
    if (length < 1) {
        protocol.sendNak(CMD_GET_CONTROL, ERR_INVALID_LENGTH);
        return;
    }

    uint8_t controlId = payload[0];
    uint8_t response[4];
    uint16_t respLen = 1;
    response[0] = controlId;

    switch (controlId) {
        case CTRL_ID_BRIGHTNESS:
            response[respLen++] = config.brightness;
            break;
        case CTRL_ID_GAMMA:
            response[respLen++] = config.gamma;
            break;
        case CTRL_ID_IDLE_TIMEOUT:
            response[respLen++] = config.idleTimeout & 0xFF;
            response[respLen++] = config.idleTimeout >> 8;
            break;
        case CTRL_ID_AUTO_SHOW:
            response[respLen++] = config.autoShow ? 1 : 0;
            break;
        case CTRL_ID_FRAME_ACK:
            response[respLen++] = config.frameAck ? 1 : 0;
            break;
        case CTRL_ID_STATUS_INTERVAL:
            response[respLen++] = config.statusInterval & 0xFF;
            response[respLen++] = config.statusInterval >> 8;
            break;
        case CTRL_ID_LOCAL_MODE:
            response[respLen++] = config.localMode;
            break;
        case CTRL_ID_CYCLE_TIME:
            response[respLen++] = config.cycleTime & 0xFF;
            response[respLen++] = config.cycleTime >> 8;
            break;
        default:
            protocol.sendNak(CMD_GET_CONTROL, ERR_INVALID_PARAM);
            return;
    }

    protocol.sendPacket(CMD_CONTROL_RESPONSE, response, respLen);
}

void handleGetPixels(const uint8_t* payload, uint16_t length) {
    if (length < 5) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_INVALID_LENGTH);
        return;
    }

    uint8_t stripId = payload[0];
    if (stripId != 0) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_INVALID_PARAM);
        return;
    }

    uint16_t start = payload[1] | ((uint16_t)payload[2] << 8);
    uint16_t count = payload[3] | ((uint16_t)payload[4] << 8);

    if (count == 0) count = NUM_PIXELS - start;
    if (start + count > NUM_PIXELS) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_PIXEL_OVERFLOW);
        return;
    }

    // Limit response size
    uint16_t maxPixels = (MAX_PAYLOAD_SIZE - 5) / leds.getBytesPerPixel();
    if (count > maxPixels) count = maxPixels;

    uint8_t* response = new uint8_t[5 + count * leds.getBytesPerPixel()];
    if (!response) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_BUFFER_OVERFLOW);
        return;
    }

    response[0] = stripId;
    response[1] = start & 0xFF;
    response[2] = start >> 8;
    response[3] = count & 0xFF;
    response[4] = count >> 8;

    uint8_t* pixelBuf = leds.getPixelBuffer();
    memcpy(response + 5, pixelBuf + start * leds.getBytesPerPixel(), count * leds.getBytesPerPixel());

    protocol.sendPacket(CMD_PIXEL_RESPONSE, response, 5 + count * leds.getBytesPerPixel());
    delete[] response;
}

void processPacket(const LtpPacket& pkt) {
    switch (pkt.cmd) {
        case CMD_NOP:
            if (pkt.flags & FLAG_ACK_REQ) {
                protocol.sendAck(CMD_NOP);
            }
            break;

        case CMD_RESET:
            // Send ACK before reset
            protocol.sendAck(CMD_RESET);
            delay(10);
            // Software reset - platform specific
#if defined(__arm__) && defined(CORE_TEENSY)
            // Teensy 3.x/4.x (ARM)
            SCB_AIRCR = 0x05FA0004;  // System reset request
#elif defined(__AVR__)
            // AVR (Arduino Uno, Nano, Mega, etc.)
            asm volatile ("jmp 0");
#else
            // Generic fallback - may not work on all platforms
            void (*resetFunc)(void) = 0;
            resetFunc();
#endif
            break;

        case CMD_HELLO:
            // Host is requesting hello
            sendHello();
            break;

        case CMD_SHOW:
            handleShow(pkt.payload, pkt.length);
            break;

        case CMD_GET_INFO:
            handleGetInfo(pkt.payload, pkt.length);
            break;

        case CMD_GET_PIXELS:
            handleGetPixels(pkt.payload, pkt.length);
            break;

        case CMD_GET_CONTROL:
            handleGetControl(pkt.payload, pkt.length);
            break;

        case CMD_GET_INPUT: {
            // No inputs on this device — return empty list
            uint8_t resp[2] = {0, 0};  // num_inputs=0, reserved=0
            protocol.sendPacket(CMD_INPUTS_LIST, resp, 2);
            break;
        }

        case CMD_PIXEL_SET_ALL:
            handlePixelSetAll(pkt.payload, pkt.length);
            break;

        case CMD_PIXEL_SET_RANGE:
            handlePixelSetRange(pkt.payload, pkt.length);
            break;

        case CMD_PIXEL_FRAME:
            handlePixelFrame(pkt.payload, pkt.length);
            break;

        case CMD_PIXEL_SUBMATRIX:
            handlePixelSubmatrix(pkt.payload, pkt.length);
            break;

        case CMD_SET_CONTROL:
            handleSetControl(pkt.payload, pkt.length);
            break;

        case CMD_SAVE_CONFIG:
            saveConfig();
            protocol.sendAck(CMD_SAVE_CONFIG);
            break;

        case CMD_LOAD_CONFIG:
            loadConfig();
            leds.setBrightness(config.brightness);
            protocol.sendAck(CMD_LOAD_CONFIG);
            break;

        case CMD_RESET_CONFIG:
            resetConfig();
            leds.setBrightness(config.brightness);
            protocol.sendAck(CMD_RESET_CONFIG);
            break;

        default:
            protocol.sendNak(pkt.cmd, ERR_INVALID_CMD);
            break;
    }
}

// ============================================================================
// ARDUINO SETUP AND LOOP
// ============================================================================

void setup() {
    // Initialize serial
    Serial.begin(SERIAL_BAUD);

    // Initialize heartbeat LED (if available)
    #if HEARTBEAT_ENABLED
    pinMode(HEARTBEAT_PIN, OUTPUT);
    digitalWrite(HEARTBEAT_PIN, LOW);
    #endif

    // Load saved configuration
    loadConfig();

    // Initialize LED driver
    leds.begin();
    leds.setBrightness(config.brightness);
    leds.clear();
    leds.show();

    // Initialize activity timer
    lastActivityTime = millis();
    stats.startTime = lastActivityTime;

    // Start local mode if configured
    if (config.localMode != LOCAL_MODE_BLANK) {
        startLocalMode(config.localMode);
    }

    // Send HELLO to announce ourselves
    delay(100); // Small delay for serial to stabilize
    sendHello();
}

void updateHeartbeat() {
    #if HEARTBEAT_ENABLED
    uint32_t now = millis();
    if (now - lastHeartbeat >= HEARTBEAT_INTERVAL) {
        lastHeartbeat = now;
        heartbeatState = !heartbeatState;
        digitalWrite(HEARTBEAT_PIN, heartbeatState ? HIGH : LOW);
    }
    #endif
}

void loop() {
    // Update heartbeat LED
    updateHeartbeat();

    // Process incoming serial data
    if (protocol.processInput()) {
        processPacket(protocol.getPacket());
    }

    // Check idle timeout
    checkIdleTimeout();

    // Update local mode animation
    updateLocalMode();
}
