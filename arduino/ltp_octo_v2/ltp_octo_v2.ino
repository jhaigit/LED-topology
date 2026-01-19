/**
 * LTP OctoWS2811 - Teensy 3.2 Implementation
 *
 * LED strip controller using OctoWS2811 for 8 parallel outputs.
 * Supports three modes (see config.h):
 *   - STRIPS: 8 independent strips
 *   - MATRIX_8: 8 strips as one matrix
 *   - MATRIX_16: 8 strips as 16-row matrix with serpentine folding
 *
 * Hardware:
 *   - Teensy 3.2
 *   - OctoWS2811 adapter board
 *   - Up to 8 WS2812B strips
 *
 * Pin Configuration (via OctoWS2811 adapter):
 *   Pin 2:  Strip 1    Pin 6:  Strip 5
 *   Pin 14: Strip 2    Pin 20: Strip 6
 *   Pin 7:  Strip 3    Pin 21: Strip 7
 *   Pin 8:  Strip 4    Pin 5:  Strip 8
 */

#include <EEPROM.h>
#include <OctoWS2811.h>
#include "config.h"
#include <ltp_protocol.h>
#include "led_driver_octo.h"

// ============================================================================
// GLOBALS
// ============================================================================

LedDriverOcto leds;

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
} config = {
    CONFIG_MAGIC,
    CONFIG_VERSION,
    255,                        // brightness
    22,                         // gamma (2.2)
    DEFAULT_IDLE_TIMEOUT,       // idle timeout
    false,                      // autoShow
    false,                      // frameAck
    0,                          // statusInterval
    LOCAL_MODE_BLANK            // localMode (default blank)
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

#define NUM_CONTROLS 7

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

// Get total pixel count for animations
uint16_t getTotalPixels() {
#if MATRIX_MODE
    return leds.getLogicalPixelCount();
#else
    return NUM_STRIPS * PIXELS_PER_STRIP;
#endif
}

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
    const uint8_t eyeSize = 5;  // Larger for OctoWS2811 matrices
    const uint8_t fadeAmount = 64;
    uint16_t numPixels = getTotalPixels();

    // Fade all pixels
    for (uint16_t i = 0; i < numPixels; i++) {
        uint32_t color = leds.getPixelColor(leds.mapPixel(i));
        uint8_t r = ((color >> 16) & 0xFF);
        uint8_t g = ((color >> 8) & 0xFF);
        uint8_t b = (color & 0xFF);
        r = r > fadeAmount ? r - fadeAmount : 0;
        g = g > fadeAmount ? g - fadeAmount : 0;
        b = b > fadeAmount ? b - fadeAmount : 0;
        leds.setPixel(i, r, g, b);
    }

    // Draw the eye
    for (uint8_t i = 0; i < eyeSize; i++) {
        int16_t pos = modePosition + i - eyeSize/2;
        if (pos >= 0 && pos < numPixels) {
            uint8_t brightness = 255 - abs(i - eyeSize/2) * 40;
            leds.setPixel(pos, brightness, 0, 0);
        }
    }

    // Move position
    if (direction) {
        modePosition++;
        if (modePosition >= numPixels - 1) direction = false;
    } else {
        modePosition--;
        if (modePosition == 0) direction = true;
    }

    leds.show();
}

// Rainbow cycle animation
void updateRainbow() {
    uint16_t numPixels = getTotalPixels();
    for (uint16_t i = 0; i < numPixels; i++) {
        uint8_t pixelHue = modeHue + (i * 256 / numPixels);
        uint8_t r, g, b;
        hsvToRgb(pixelHue, 255, 200, r, g, b);
        leds.setPixel(i, r, g, b);
    }
    modeHue++;
    leds.show();
}

// Fire effect animation
void updateFire() {
    static uint8_t heat[512];  // Max 512 pixels for fire
    const uint8_t cooling = 55;
    const uint8_t sparking = 120;
    uint16_t numPixels = min((uint16_t)512, getTotalPixels());

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
    uint16_t numPixels = getTotalPixels();

    // Fade all pixels
    for (uint16_t i = 0; i < numPixels; i++) {
        uint32_t color = leds.getPixelColor(leds.mapPixel(i));
        uint8_t r = ((color >> 16) & 0xFF);
        uint8_t g = ((color >> 8) & 0xFF);
        uint8_t b = (color & 0xFF);
        r = r > 20 ? r - 20 : 0;
        g = g > 20 ? g - 20 : 0;
        b = b > 20 ? b - 20 : 0;
        leds.setPixel(i, r, g, b);
    }

    // Add random sparkles (more for larger displays)
    uint8_t sparkleCount = numPixels > 200 ? 8 : 3;
    for (uint8_t i = 0; i < sparkleCount; i++) {
        uint16_t pos = random(numPixels);
        uint8_t r, g, b;
        hsvToRgb(random(256), 200, 255, r, g, b);
        leds.setPixel(pos, r, g, b);
    }

    leds.show();
}

// Color chase animation
void updateChase() {
    const uint8_t chaseLength = 8;  // Larger for OctoWS2811
    uint16_t numPixels = getTotalPixels();

    leds.clear();

    for (uint8_t i = 0; i < chaseLength; i++) {
        uint16_t pos = (modePosition + i) % numPixels;
        uint8_t r, g, b;
        hsvToRgb(modeHue, 255, 255 - i * 25, r, g, b);
        leds.setPixel(pos, r, g, b);
    }

    modePosition = (modePosition + 1) % numPixels;
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
        case LOCAL_MODE_CYLON:   interval = 15; break;  // Faster for OctoWS2811
        case LOCAL_MODE_RAINBOW: interval = 15; break;
        case LOCAL_MODE_FIRE:    interval = 25; break;
        case LOCAL_MODE_SPARKLE: interval = 25; break;
        case LOCAL_MODE_CHASE:   interval = 30; break;
        default: interval = 50; break;
    }

    if (now - lastModeUpdate < interval) return;
    lastModeUpdate = now;

    // Handle cycle mode - switch every 10 seconds
    if (config.localMode == LOCAL_MODE_CYCLE) {
        if (now - modeStartTime > 10000) {
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
    uint8_t payload[14];
    payload[0] = LTP_PROTOCOL_MAJOR;
    payload[1] = LTP_PROTOCOL_MINOR;
    payload[2] = (FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR;
    payload[3] = 0; // BCD low byte
    payload[4] = leds.getStripCount();
    payload[5] = leds.getPixelsPerStrip() & 0xFF;
    payload[6] = leds.getPixelsPerStrip() >> 8;
    payload[7] = leds.getColorFormat();

    uint8_t caps1 = CAPS_BRIGHTNESS | CAPS_EXTENDED;
#if MATRIX_MODE
    caps1 |= CAPS_SEGMENTS;  // Indicate matrix/segment support
#else
    caps1 |= CAPS_MULTI_STRIP;
#endif
    payload[8] = caps1;
#if MATRIX_MODE
    payload[9] = CAPS_PIXEL_READBACK | CAPS_SUBMATRIX | CAPS_EEPROM;
#else
    payload[9] = CAPS_PIXEL_READBACK | CAPS_EEPROM;
#endif
    payload[10] = NUM_CONTROLS;
    payload[11] = 0; // Input count

#if MATRIX_MODE
    // Add matrix dimensions
    payload[12] = MATRIX_WIDTH & 0xFF;
    payload[13] = MATRIX_HEIGHT;
    protocol.sendPacket(CMD_HELLO, payload, 14);
#else
    protocol.sendPacket(CMD_HELLO, payload, 12);
#endif
}

void handleGetInfo(const uint8_t* payload, uint16_t length) {
    if (length < 1) {
        protocol.sendNak(CMD_GET_INFO, ERR_INVALID_LENGTH);
        return;
    }

    uint8_t infoType = payload[0];
    uint8_t response[64];
    uint16_t respLen = 0;

    switch (infoType) {
        case INFO_ALL:
            response[respLen++] = LTP_PROTOCOL_MAJOR;
            response[respLen++] = LTP_PROTOCOL_MINOR;
            response[respLen++] = (FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR;
            response[respLen++] = 0;
            response[respLen++] = leds.getStripCount();
            response[respLen++] = leds.getPixelsPerStrip() & 0xFF;
            response[respLen++] = leds.getPixelsPerStrip() >> 8;
            response[respLen++] = leds.getColorFormat();
            response[respLen++] = CAPS_BRIGHTNESS | CAPS_EXTENDED;
#if MATRIX_MODE
            response[respLen++] = CAPS_PIXEL_READBACK | CAPS_SUBMATRIX;
#else
            response[respLen++] = CAPS_PIXEL_READBACK;
#endif
            response[respLen++] = NUM_CONTROLS;
            // Device name
            {
                const char* name = DEVICE_NAME;
                uint8_t i = 0;
                while (name[i] && i < 15) {
                    response[respLen++] = name[i++];
                }
                response[respLen++] = 0;
            }
#if MATRIX_MODE
            // Add matrix dimensions (width, height as 16-bit values)
            response[respLen++] = MATRIX_WIDTH & 0xFF;
            response[respLen++] = MATRIX_WIDTH >> 8;
            response[respLen++] = MATRIX_HEIGHT & 0xFF;
            response[respLen++] = MATRIX_HEIGHT >> 8;
#endif
            break;

        case INFO_VERSION:
            response[respLen++] = LTP_PROTOCOL_MAJOR;
            response[respLen++] = LTP_PROTOCOL_MINOR;
            response[respLen++] = (FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR;
            response[respLen++] = 0;
            break;

        case INFO_STRIPS:
            response[respLen++] = leds.getStripCount();
            // Report strip(s)
            for (uint8_t s = 0; s < leds.getStripCount(); s++) {
                response[respLen++] = s; // Strip ID
                response[respLen++] = leds.getPixelsPerStrip() & 0xFF;
                response[respLen++] = leds.getPixelsPerStrip() >> 8;
                response[respLen++] = leds.getColorFormat();
                response[respLen++] = leds.getLedType();
                response[respLen++] = 0; // Pin (N/A for OctoWS2811)
                response[respLen++] = 0; // Clock pin
#if MATRIX_MODE
                response[respLen++] = 0x01; // Flags: matrix mode
#else
                response[respLen++] = 0; // Flags
#endif
            }
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
            // Frames displayed
            response[respLen++] = stats.framesDisplayed & 0xFF;
            response[respLen++] = (stats.framesDisplayed >> 8) & 0xFF;
            response[respLen++] = (stats.framesDisplayed >> 16) & 0xFF;
            response[respLen++] = (stats.framesDisplayed >> 24) & 0xFF;
            // Bytes received
            response[respLen++] = stats.bytesReceived & 0xFF;
            response[respLen++] = (stats.bytesReceived >> 8) & 0xFF;
            response[respLen++] = (stats.bytesReceived >> 16) & 0xFF;
            response[respLen++] = (stats.bytesReceived >> 24) & 0xFF;
            // Checksum errors
            response[respLen++] = stats.checksumErrors & 0xFF;
            response[respLen++] = stats.checksumErrors >> 8;
            // Buffer overflows
            response[respLen++] = stats.bufferOverflows & 0xFF;
            response[respLen++] = stats.bufferOverflows >> 8;
            // Uptime
            {
                uint32_t uptime = (millis() - stats.startTime) / 1000;
                response[respLen++] = uptime & 0xFF;
                response[respLen++] = (uptime >> 8) & 0xFF;
                response[respLen++] = (uptime >> 16) & 0xFF;
                response[respLen++] = (uptime >> 24) & 0xFF;
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

    if (config.frameAck && length >= 2) {
        uint8_t response[4];
        response[0] = payload[0];
        response[1] = payload[1];
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

    exitLocalMode();  // Exit local mode on display command

#if MATRIX_MODE
    // In matrix mode, only strip 0 (the whole matrix) is valid
    if (stripId != 0 && stripId != STRIP_ALL) {
        protocol.sendNak(CMD_PIXEL_SET_ALL, ERR_INVALID_PARAM);
        return;
    }
    leds.fill(payload[1], payload[2], payload[3]);
#else
    // In strips mode, can fill individual strip or all
    if (stripId == STRIP_ALL) {
        leds.fill(payload[1], payload[2], payload[3]);
    } else if (stripId < NUM_STRIPS) {
        leds.fillStrip(stripId, payload[1], payload[2], payload[3]);
    } else {
        protocol.sendNak(CMD_PIXEL_SET_ALL, ERR_INVALID_PARAM);
        return;
    }
#endif

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
    uint16_t start = payload[1] | ((uint16_t)payload[2] << 8);
    uint16_t end = payload[3] | ((uint16_t)payload[4] << 8);
    uint8_t r = payload[5];
    uint8_t g = payload[6];
    uint8_t b = payload[7];

    exitLocalMode();  // Exit local mode on display command

#if MATRIX_MODE
    if (stripId != 0) {
        protocol.sendNak(CMD_PIXEL_SET_RANGE, ERR_INVALID_PARAM);
        return;
    }
    if (end > leds.getLogicalPixelCount()) {
        protocol.sendNak(CMD_PIXEL_SET_RANGE, ERR_PIXEL_OVERFLOW);
        return;
    }
    leds.fillRange(0, start, end, r, g, b);
#else
    if (stripId >= NUM_STRIPS) {
        protocol.sendNak(CMD_PIXEL_SET_RANGE, ERR_INVALID_PARAM);
        return;
    }
    if (end > PIXELS_PER_STRIP) {
        protocol.sendNak(CMD_PIXEL_SET_RANGE, ERR_PIXEL_OVERFLOW);
        return;
    }
    leds.fillRange(stripId, start, end, r, g, b);
#endif

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
    uint16_t start = payload[1] | ((uint16_t)payload[2] << 8);
    uint16_t count = payload[3] | ((uint16_t)payload[4] << 8);

    uint16_t dataOffset = 5;
    uint16_t expectedBytes = count * leds.getBytesPerPixel();

    if (length < dataOffset + expectedBytes) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_INVALID_LENGTH);
        return;
    }

    exitLocalMode();  // Exit local mode on display command

#if MATRIX_MODE
    if (stripId != 0) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_INVALID_PARAM);
        return;
    }
    if (start + count > leds.getLogicalPixelCount()) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_PIXEL_OVERFLOW);
        return;
    }

    // Copy pixel data with logical-to-physical mapping
    const uint8_t* pixelData = payload + dataOffset;
    uint8_t bpp = leds.getBytesPerPixel();

    for (uint16_t i = 0; i < count; i++) {
        uint16_t offset = i * bpp;
        leds.setPixel(start + i, pixelData[offset], pixelData[offset + 1], pixelData[offset + 2]);
    }
#else
    if (stripId >= NUM_STRIPS) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_INVALID_PARAM);
        return;
    }
    if (start + count > PIXELS_PER_STRIP) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_PIXEL_OVERFLOW);
        return;
    }

    // Copy pixel data directly to strip
    const uint8_t* pixelData = payload + dataOffset;
    uint8_t bpp = leds.getBytesPerPixel();

    for (uint16_t i = 0; i < count; i++) {
        uint16_t offset = i * bpp;
        leds.setStripPixel(stripId, start + i, pixelData[offset], pixelData[offset + 1], pixelData[offset + 2]);
    }
#endif

    stats.framesReceived++;
    stats.bytesReceived += expectedBytes;
    resetActivityTimer();

    if (config.autoShow) {
        leds.show();
        stats.framesDisplayed++;
    }
}

#if MATRIX_MODE
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
            uint16_t logicalIndex;

            if (serpentine && (y & 1)) {
                // Odd row - reversed
                logicalIndex = (y + 1) * matrixWidth - 1 - x;
            } else {
                // Even row or linear - normal
                logicalIndex = y * matrixWidth + x;
            }

            // Check bounds
            if (logicalIndex >= leds.getLogicalPixelCount()) {
                protocol.sendNak(CMD_PIXEL_SUBMATRIX, ERR_PIXEL_OVERFLOW);
                return;
            }

            // Get pixel data from input (row-major order in submatrix)
            uint32_t srcOffset = ((uint32_t)row * subWidth + col) * bpp;
            leds.setPixel(logicalIndex,
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
#endif

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
            startLocalMode(payload[1]);
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
    uint16_t start = payload[1] | ((uint16_t)payload[2] << 8);
    uint16_t count = payload[3] | ((uint16_t)payload[4] << 8);

    uint16_t maxPixels;
#if MATRIX_MODE
    if (stripId != 0) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_INVALID_PARAM);
        return;
    }
    maxPixels = leds.getLogicalPixelCount();
#else
    if (stripId >= NUM_STRIPS) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_INVALID_PARAM);
        return;
    }
    maxPixels = PIXELS_PER_STRIP;
#endif

    if (count == 0) count = maxPixels - start;
    if (start + count > maxPixels) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_PIXEL_OVERFLOW);
        return;
    }

    // Limit response size
    uint16_t maxRespPixels = (MAX_PAYLOAD_SIZE - 5) / leds.getBytesPerPixel();
    if (count > maxRespPixels) count = maxRespPixels;

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

    // Read back pixel data
    uint8_t bpp = leds.getBytesPerPixel();
    for (uint16_t i = 0; i < count; i++) {
        uint16_t logicalIdx = start + i;
        uint16_t physIdx = leds.mapPixel(logicalIdx);
#if !MATRIX_MODE
        physIdx = stripId * PIXELS_PER_STRIP + logicalIdx;
#endif
        uint32_t color = leds.getPixelColor(physIdx);
        response[5 + i * bpp + 0] = (color >> 16) & 0xFF; // R
        response[5 + i * bpp + 1] = (color >> 8) & 0xFF;  // G
        response[5 + i * bpp + 2] = color & 0xFF;         // B
    }

    protocol.sendPacket(CMD_PIXEL_RESPONSE, response, 5 + count * bpp);
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
            protocol.sendAck(CMD_RESET);
            delay(10);
            // Teensy 3.x reset
            SCB_AIRCR = 0x05FA0004;
            break;

        case CMD_HELLO:
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

        case CMD_PIXEL_SET_ALL:
            handlePixelSetAll(pkt.payload, pkt.length);
            break;

        case CMD_PIXEL_SET_RANGE:
            handlePixelSetRange(pkt.payload, pkt.length);
            break;

        case CMD_PIXEL_FRAME:
            handlePixelFrame(pkt.payload, pkt.length);
            break;

#if MATRIX_MODE
        case CMD_PIXEL_SUBMATRIX:
            handlePixelSubmatrix(pkt.payload, pkt.length);
            break;
#endif

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
// SETUP AND LOOP
// ============================================================================

void setup() {
    Serial.begin(SERIAL_BAUD);

    // Load saved configuration
    loadConfig();

    leds.begin();
    leds.setBrightness(config.brightness);
    leds.clear();
    leds.show();

    // Initialize activity timer
    lastActivityTime = millis();
    stats.startTime = lastActivityTime;

    // Brief startup indicator
    for (int i = 0; i < 3; i++) {
        leds.fillStrip(0, 0, 32, 0);  // Dim green on first strip
        leds.show();
        delay(100);
        leds.clear();
        leds.show();
        delay(100);
    }

    // Start local mode if configured
    if (config.localMode != LOCAL_MODE_BLANK) {
        startLocalMode(config.localMode);
    }

    delay(100);
    sendHello();
}

void loop() {
    if (protocol.processInput()) {
        processPacket(protocol.getPacket());
    }

    // Check idle timeout
    checkIdleTimeout();

    // Update local mode animation
    updateLocalMode();
}
