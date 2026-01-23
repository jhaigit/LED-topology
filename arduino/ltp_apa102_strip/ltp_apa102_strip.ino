/**
 * LTP APA102 Strip - Teensy 3.2 Implementation
 *
 * LED strip controller with input sensors for interactive installations.
 *
 * Hardware:
 *   - Teensy 3.2
 *   - 144 APA102 LEDs (data pin 3, clock pin 2)
 *   - 2 buttons (pins 4 and 5, active low)
 *   - Motion detector (pin 16, active high)
 *
 * Features:
 *   - Full LTP protocol v2 support
 *   - Input event reporting for buttons and motion detector
 *   - Local display modes (cylon, rainbow, fire, sparkle, chase)
 *   - EEPROM configuration persistence
 */

#include <EEPROM.h>
#include <FastLED.h>
#include "config.h"
#include <ltp_protocol.h>

// ============================================================================
// LED ARRAY
// ============================================================================

CRGB leds[NUM_LEDS];
uint8_t globalBrightness = 255;

// ============================================================================
// PROTOCOL HANDLER
// ============================================================================

LtpProtocol protocol(Serial, MAX_PAYLOAD_SIZE);

// ============================================================================
// EEPROM CONFIGURATION
// ============================================================================

#define CONFIG_MAGIC        0x4C54  // "LT" - magic number for validation
#define CONFIG_VERSION      2
#define EEPROM_CONFIG_ADDR  0

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
    bool inputEventsEnabled;    // Send input events over serial
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
    true                        // inputEventsEnabled
};

// ============================================================================
// INPUT HANDLING
// ============================================================================

struct InputState {
    uint8_t pin;
    uint8_t type;           // INPUT_BUTTON or INPUT_SWITCH
    bool activeHigh;        // Polarity
    bool currentState;      // Debounced state
    bool lastRawState;      // For debouncing
    bool lastReportedState; // For change detection
    uint32_t lastChangeTime;// For debouncing
};

InputState inputs[NUM_INPUTS] = {
    { BUTTON_1_PIN, INPUT_BUTTON, !BUTTON_ACTIVE_LOW, false, false, false, 0 },
    { BUTTON_2_PIN, INPUT_BUTTON, !BUTTON_ACTIVE_LOW, false, false, false, 0 },
    { MOTION_PIN,   INPUT_SWITCH, MOTION_ACTIVE_HIGH, false, false, false, 0 }
};

// Input names for info responses
const char* inputNames[NUM_INPUTS] = {
    "Button1",
    "Button2",
    "Motion"
};

// ============================================================================
// STATE TRACKING
// ============================================================================

// Idle timeout tracking
uint32_t lastActivityTime = 0;
bool isIdle = false;

// Local mode state
bool localModeActive = false;
uint8_t currentDisplayMode = 0;
uint32_t lastModeUpdate = 0;
uint32_t modeStartTime = 0;
uint16_t modePosition = 0;
uint8_t modeHue = 0;

// Statistics
struct {
    uint32_t framesReceived = 0;
    uint32_t framesDisplayed = 0;
    uint32_t bytesReceived = 0;
    uint16_t checksumErrors = 0;
    uint16_t bufferOverflows = 0;
    uint32_t startTime = 0;
    uint32_t inputEventsCount = 0;
} stats;

// Heartbeat LED
#define HEARTBEAT_PIN       LED_BUILTIN  // Pin 13 on Teensy
#define HEARTBEAT_INTERVAL  500          // ms
uint32_t lastHeartbeat = 0;
bool heartbeatState = false;

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

    if (stored.magic == CONFIG_MAGIC && stored.version == CONFIG_VERSION) {
        config = stored;
    }
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
    config.inputEventsEnabled = true;
    saveConfig();
}

// ============================================================================
// LED DRIVER HELPERS
// ============================================================================

void showLeds() {
    FastLED.show();
}

void clearLeds() {
    fill_solid(leds, NUM_LEDS, CRGB::Black);
}

void setPixel(uint16_t idx, uint8_t r, uint8_t g, uint8_t b) {
    if (idx < NUM_LEDS) {
        leds[idx] = CRGB(r, g, b);
    }
}

uint32_t getPixelColor(uint16_t idx) {
    if (idx < NUM_LEDS) {
        return ((uint32_t)leds[idx].r << 16) | ((uint32_t)leds[idx].g << 8) | leds[idx].b;
    }
    return 0;
}

void fillAll(uint8_t r, uint8_t g, uint8_t b) {
    fill_solid(leds, NUM_LEDS, CRGB(r, g, b));
}

void fillRange(uint16_t start, uint16_t end, uint8_t r, uint8_t g, uint8_t b) {
    if (start >= NUM_LEDS) return;
    if (end > NUM_LEDS) end = NUM_LEDS;
    for (uint16_t i = start; i < end; i++) {
        leds[i] = CRGB(r, g, b);
    }
}

// ============================================================================
// INPUT HANDLING
// ============================================================================

void initInputs() {
    for (uint8_t i = 0; i < NUM_INPUTS; i++) {
        pinMode(inputs[i].pin, INPUT_PULLUP);
        bool raw = digitalRead(inputs[i].pin);
        inputs[i].currentState = inputs[i].activeHigh ? raw : !raw;
        inputs[i].lastRawState = raw;
        inputs[i].lastReportedState = inputs[i].currentState;
    }
}

void updateInputs() {
    uint32_t now = millis();

    for (uint8_t i = 0; i < NUM_INPUTS; i++) {
        bool raw = digitalRead(inputs[i].pin);

        // Debounce
        if (raw != inputs[i].lastRawState) {
            inputs[i].lastRawState = raw;
            inputs[i].lastChangeTime = now;
        }

        if ((now - inputs[i].lastChangeTime) > DEBOUNCE_TIME) {
            bool state = inputs[i].activeHigh ? raw : !raw;

            if (state != inputs[i].currentState) {
                inputs[i].currentState = state;

                // Send input event if enabled and state changed
                if (config.inputEventsEnabled && state != inputs[i].lastReportedState) {
                    sendInputEvent(i, state);
                    inputs[i].lastReportedState = state;
                    stats.inputEventsCount++;
                }
            }
        }
    }
}

void sendInputEvent(uint8_t inputId, bool state) {
    uint8_t payload[6];
    payload[0] = inputId;                   // Input ID
    payload[1] = inputs[inputId].type;      // Input type
    // Timestamp (16-bit, little-endian)
    uint16_t timestamp = millis() & 0xFFFF;
    payload[2] = timestamp & 0xFF;
    payload[3] = timestamp >> 8;
    // Data: current value
    payload[4] = state ? 1 : 0;             // Current value
    payload[5] = 0;                         // Reserved

    protocol.sendPacket(CMD_INPUT_EVENT, payload, 6);
}

// ============================================================================
// IDLE TIMEOUT
// ============================================================================

void resetActivityTimer() {
    lastActivityTime = millis();
    if (isIdle) {
        isIdle = false;
        showLeds();
    }
}

void checkIdleTimeout() {
    if (config.idleTimeout == 0) return;

    uint32_t now = millis();
    uint32_t elapsed = (now - lastActivityTime) / 1000;

    if (!isIdle && elapsed >= config.idleTimeout) {
        isIdle = true;
        clearLeds();
        showLeds();
    }
}

// ============================================================================
// LOCAL DISPLAY MODES
// ============================================================================

void hsvToRgb(uint8_t h, uint8_t s, uint8_t v, uint8_t& r, uint8_t& g, uint8_t& b) {
    CHSV hsv(h, s, v);
    CRGB rgb;
    hsv2rgb_rainbow(hsv, rgb);
    r = rgb.r;
    g = rgb.g;
    b = rgb.b;
}

void exitLocalMode() {
    if (localModeActive) {
        localModeActive = false;
        clearLeds();
    }
}

void startLocalMode(uint8_t mode) {
    config.localMode = mode;
    modePosition = 0;
    modeHue = 0;
    lastModeUpdate = millis();
    modeStartTime = millis();

    if (mode == LOCAL_MODE_BLANK) {
        localModeActive = false;
        clearLeds();
        showLeds();
    } else {
        localModeActive = true;
        if (mode == LOCAL_MODE_CYCLE) {
            currentDisplayMode = LOCAL_MODE_CYLON;
        } else {
            currentDisplayMode = mode;
        }
    }
}

void updateCylon() {
    static bool direction = true;
    const uint8_t eyeSize = 5;
    const uint8_t fadeAmount = 64;

    // Fade all pixels
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        leds[i].fadeToBlackBy(fadeAmount);
    }

    // Draw the eye
    for (uint8_t i = 0; i < eyeSize; i++) {
        int16_t pos = modePosition + i - eyeSize/2;
        if (pos >= 0 && pos < NUM_LEDS) {
            uint8_t brightness = 255 - abs(i - eyeSize/2) * 40;
            leds[pos] = CRGB(brightness, 0, 0);
        }
    }

    // Move position
    if (direction) {
        modePosition++;
        if (modePosition >= NUM_LEDS - 1) direction = false;
    } else {
        modePosition--;
        if (modePosition == 0) direction = true;
    }

    showLeds();
}

void updateRainbow() {
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        leds[i] = CHSV(modeHue + (i * 256 / NUM_LEDS), 255, 200);
    }
    modeHue++;
    showLeds();
}

void updateFire() {
    static uint8_t heat[NUM_LEDS];
    const uint8_t cooling = 55;
    const uint8_t sparking = 120;

    // Cool down every cell
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        uint8_t cooldown = random8(0, ((cooling * 10) / NUM_LEDS) + 2);
        heat[i] = heat[i] > cooldown ? heat[i] - cooldown : 0;
    }

    // Heat rises
    for (uint16_t i = NUM_LEDS - 1; i >= 2; i--) {
        heat[i] = (heat[i - 1] + heat[i - 2] + heat[i - 2]) / 3;
    }

    // Randomly ignite sparks near bottom
    if (random8() < sparking) {
        uint8_t y = random8(7);
        heat[y] = qadd8(heat[y], random8(160, 255));
    }

    // Map heat to colors
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        leds[i] = HeatColor(heat[i]);
    }

    showLeds();
}

void updateSparkle() {
    const uint8_t fadeAmount = 20;

    // Fade all pixels
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        leds[i].fadeToBlackBy(fadeAmount);
    }

    // Add random sparkles
    uint8_t sparkleCount = NUM_LEDS > 200 ? 8 : 3;
    for (uint8_t i = 0; i < sparkleCount; i++) {
        uint16_t pos = random16(NUM_LEDS);
        leds[pos] = CHSV(random8(), 200, 255);
    }

    showLeds();
}

void updateChase() {
    clearLeds();

    const uint8_t chaseLength = 8;

    for (uint8_t i = 0; i < chaseLength; i++) {
        uint16_t pos = (modePosition + i) % NUM_LEDS;
        leds[pos] = CHSV(modeHue, 255, 255 - i * 25);
    }

    modePosition = (modePosition + 1) % NUM_LEDS;
    modeHue += 2;
    showLeds();
}

void updateLocalMode() {
    if (!localModeActive) return;

    uint32_t now = millis();
    uint32_t interval;

    switch (currentDisplayMode) {
        case LOCAL_MODE_CYLON:   interval = 15; break;
        case LOCAL_MODE_RAINBOW: interval = 15; break;
        case LOCAL_MODE_FIRE:    interval = 25; break;
        case LOCAL_MODE_SPARKLE: interval = 25; break;
        case LOCAL_MODE_CHASE:   interval = 30; break;
        default: interval = 50; break;
    }

    if (now - lastModeUpdate < interval) return;
    lastModeUpdate = now;

    // Handle cycle mode
    if (config.localMode == LOCAL_MODE_CYCLE) {
        if (now - modeStartTime > 10000) {
            modeStartTime = now;
            currentDisplayMode++;
            if (currentDisplayMode >= LOCAL_MODE_COUNT) {
                currentDisplayMode = LOCAL_MODE_CYLON;
            }
            modePosition = 0;
            clearLeds();
        }
    }

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
    payload[4] = 1; // Number of strips
    payload[5] = NUM_LEDS & 0xFF;
    payload[6] = NUM_LEDS >> 8;
    payload[7] = COLOR_RGB;  // Color format

    uint8_t caps1 = CAPS_BRIGHTNESS | CAPS_EXTENDED;
    payload[8] = caps1;
    payload[9] = CAPS_PIXEL_READBACK | CAPS_EEPROM | CAPS_INPUTS;
    payload[10] = NUM_CONTROLS;
    payload[11] = NUM_INPUTS;

    protocol.sendPacket(CMD_HELLO, payload, 12);
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
            response[respLen++] = 1; // Number of strips
            response[respLen++] = NUM_LEDS & 0xFF;
            response[respLen++] = NUM_LEDS >> 8;
            response[respLen++] = COLOR_RGB;
            response[respLen++] = CAPS_BRIGHTNESS | CAPS_EXTENDED;
            response[respLen++] = CAPS_PIXEL_READBACK | CAPS_EEPROM | CAPS_INPUTS;
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
            break;

        case INFO_VERSION:
            response[respLen++] = LTP_PROTOCOL_MAJOR;
            response[respLen++] = LTP_PROTOCOL_MINOR;
            response[respLen++] = (FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR;
            response[respLen++] = 0;
            break;

        case INFO_STRIPS:
            response[respLen++] = 1; // Number of strips
            // Strip info
            response[respLen++] = 0; // Strip ID
            response[respLen++] = NUM_LEDS & 0xFF;
            response[respLen++] = NUM_LEDS >> 8;
            response[respLen++] = COLOR_RGB;
            response[respLen++] = LED_TYPE_APA102;
            response[respLen++] = LED_DATA_PIN;
            response[respLen++] = LED_CLOCK_PIN;
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

        case INFO_INPUTS:
            response[respLen++] = NUM_INPUTS;
            for (uint8_t i = 0; i < NUM_INPUTS; i++) {
                response[respLen++] = i;                    // Input ID
                response[respLen++] = inputs[i].type;       // Type
                response[respLen++] = inputs[i].currentState ? 1 : 0;  // Current value
                response[respLen++] = 0;                    // Reserved
            }
            break;

        default:
            protocol.sendNak(CMD_GET_INFO, ERR_INVALID_PARAM);
            return;
    }

    protocol.sendPacket(CMD_INFO_RESPONSE, response, respLen);
}

void handleGetInput(const uint8_t* payload, uint16_t length) {
    if (length < 1) {
        protocol.sendNak(CMD_GET_INPUT, ERR_INVALID_LENGTH);
        return;
    }

    uint8_t inputId = payload[0];

    if (inputId == 0xFF) {
        // Return all inputs
        uint8_t response[2 + NUM_INPUTS * 4];
        response[0] = NUM_INPUTS;
        response[1] = 0; // Reserved

        for (uint8_t i = 0; i < NUM_INPUTS; i++) {
            response[2 + i * 4 + 0] = i;                    // Input ID
            response[2 + i * 4 + 1] = inputs[i].type;       // Type
            response[2 + i * 4 + 2] = inputs[i].currentState ? 1 : 0;
            response[2 + i * 4 + 3] = 0;                    // Reserved
        }

        protocol.sendPacket(CMD_INPUTS_LIST, response, 2 + NUM_INPUTS * 4);
    } else if (inputId < NUM_INPUTS) {
        // Return single input
        uint8_t response[8];
        response[0] = inputId;
        response[1] = inputs[inputId].type;
        response[2] = inputs[inputId].currentState ? 1 : 0;
        response[3] = 0;
        // Add input name
        const char* name = inputNames[inputId];
        uint8_t nameLen = strlen(name);
        if (nameLen > 4) nameLen = 4;
        memcpy(response + 4, name, nameLen);

        protocol.sendPacket(CMD_INPUT_RESPONSE, response, 4 + nameLen);
    } else {
        protocol.sendNak(CMD_GET_INPUT, ERR_INVALID_PARAM);
    }
}

void handleShow(const uint8_t* payload, uint16_t length) {
    resetActivityTimer();
    showLeds();
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

    exitLocalMode();
    fillAll(payload[1], payload[2], payload[3]);
    stats.framesReceived++;

    if (config.autoShow) {
        showLeds();
        stats.framesDisplayed++;
    }
}

void handlePixelSetRange(const uint8_t* payload, uint16_t length) {
    if (length < 8) {
        protocol.sendNak(CMD_PIXEL_SET_RANGE, ERR_INVALID_LENGTH);
        return;
    }

    uint16_t start = payload[1] | ((uint16_t)payload[2] << 8);
    uint16_t end = payload[3] | ((uint16_t)payload[4] << 8);

    if (end > NUM_LEDS) {
        protocol.sendNak(CMD_PIXEL_SET_RANGE, ERR_PIXEL_OVERFLOW);
        return;
    }

    exitLocalMode();
    fillRange(start, end, payload[5], payload[6], payload[7]);
    stats.framesReceived++;

    if (config.autoShow) {
        showLeds();
        stats.framesDisplayed++;
    }
}

void handlePixelFrame(const uint8_t* payload, uint16_t length) {
    if (length < 5) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_INVALID_LENGTH);
        return;
    }

    uint16_t start = payload[1] | ((uint16_t)payload[2] << 8);
    uint16_t count = payload[3] | ((uint16_t)payload[4] << 8);
    uint16_t dataOffset = 5;
    uint16_t expectedBytes = count * 3;  // RGB

    if (length < dataOffset + expectedBytes) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_INVALID_LENGTH);
        return;
    }

    if (start + count > NUM_LEDS) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_PIXEL_OVERFLOW);
        return;
    }

    exitLocalMode();

    const uint8_t* pixelData = payload + dataOffset;
    for (uint16_t i = 0; i < count; i++) {
        uint16_t offset = i * 3;
        setPixel(start + i, pixelData[offset], pixelData[offset + 1], pixelData[offset + 2]);
    }

    stats.framesReceived++;
    stats.bytesReceived += expectedBytes;
    resetActivityTimer();

    if (config.autoShow) {
        showLeds();
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
            FastLED.setBrightness(config.brightness);
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

    uint16_t start = payload[1] | ((uint16_t)payload[2] << 8);
    uint16_t count = payload[3] | ((uint16_t)payload[4] << 8);

    if (count == 0) count = NUM_LEDS - start;
    if (start + count > NUM_LEDS) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_PIXEL_OVERFLOW);
        return;
    }

    // Limit response size
    uint16_t maxRespPixels = (MAX_PAYLOAD_SIZE - 5) / 3;
    if (count > maxRespPixels) count = maxRespPixels;

    uint8_t* response = new uint8_t[5 + count * 3];
    if (!response) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_BUFFER_OVERFLOW);
        return;
    }

    response[0] = 0; // Strip ID
    response[1] = start & 0xFF;
    response[2] = start >> 8;
    response[3] = count & 0xFF;
    response[4] = count >> 8;

    for (uint16_t i = 0; i < count; i++) {
        uint32_t color = getPixelColor(start + i);
        response[5 + i * 3 + 0] = (color >> 16) & 0xFF; // R
        response[5 + i * 3 + 1] = (color >> 8) & 0xFF;  // G
        response[5 + i * 3 + 2] = color & 0xFF;         // B
    }

    protocol.sendPacket(CMD_PIXEL_RESPONSE, response, 5 + count * 3);
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

        case CMD_GET_INPUT:
            handleGetInput(pkt.payload, pkt.length);
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

        case CMD_SET_CONTROL:
            handleSetControl(pkt.payload, pkt.length);
            break;

        case CMD_SAVE_CONFIG:
            saveConfig();
            protocol.sendAck(CMD_SAVE_CONFIG);
            break;

        case CMD_LOAD_CONFIG:
            loadConfig();
            FastLED.setBrightness(config.brightness);
            protocol.sendAck(CMD_LOAD_CONFIG);
            break;

        case CMD_RESET_CONFIG:
            resetConfig();
            FastLED.setBrightness(config.brightness);
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

    // Initialize heartbeat LED
    pinMode(HEARTBEAT_PIN, OUTPUT);
    digitalWrite(HEARTBEAT_PIN, LOW);

    // Load saved configuration
    loadConfig();

    // Initialize FastLED
    FastLED.addLeds<LED_CHIPSET, LED_DATA_PIN, LED_CLOCK_PIN, LED_COLOR_ORDER>(leds, NUM_LEDS);
    FastLED.setBrightness(config.brightness);
    clearLeds();
    showLeds();

    // Initialize inputs
    initInputs();

    // Initialize activity timer
    lastActivityTime = millis();
    stats.startTime = lastActivityTime;

    // Brief startup indicator
    for (int i = 0; i < 3; i++) {
        fill_solid(leds, min(10, NUM_LEDS), CRGB(0, 32, 0));  // Dim green
        showLeds();
        delay(100);
        clearLeds();
        showLeds();
        delay(100);
    }

    // Start local mode if configured
    if (config.localMode != LOCAL_MODE_BLANK) {
        startLocalMode(config.localMode);
    }

    delay(100);
    sendHello();
}

void updateHeartbeat() {
    uint32_t now = millis();
    if (now - lastHeartbeat >= HEARTBEAT_INTERVAL) {
        lastHeartbeat = now;
        heartbeatState = !heartbeatState;
        digitalWrite(HEARTBEAT_PIN, heartbeatState ? HIGH : LOW);
    }
}

void loop() {
    // Update heartbeat LED
    updateHeartbeat();

    // Process serial protocol
    if (protocol.processInput()) {
        processPacket(protocol.getPacket());
    }

    // Update inputs and send events
    updateInputs();

    // Check idle timeout
    checkIdleTimeout();

    // Update local mode animation
    updateLocalMode();
}
