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
#include "config.h"
#include <ltp_protocol.h>

// Build version info (set by Makefile, fallback for IDE builds)
#ifndef GIT_COMMIT
#define GIT_COMMIT "unknown"
#endif
#ifndef BUILD_DATE
#define BUILD_DATE "00000000"
#endif

// ============================================================================
// LED DRIVER SELECTION
// ============================================================================

#ifdef USE_FASTLED
    #include <FastLED.h>
    CRGB leds[NUM_LEDS];
#else
    #include "apa102_driver.h"
    APA102Driver ledDriver(NUM_LEDS, LED_DATA_PIN, LED_CLOCK_PIN);
#endif

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
    true,                       // inputEventsEnabled
    10                          // cycleTime (seconds)
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
#define HEARTBEAT_PIN       13
#define HEARTBEAT_INTERVAL  500  // ms
uint32_t lastHeartbeat = 0;

// Control metadata for INFO_CONTROLS response
struct ControlDef {
    uint8_t id;
    uint8_t type;
    uint8_t flags;
    int16_t minVal;
    int16_t maxVal;
    const char* name;
    const char* description;
};

static const ControlDef controlDefs[NUM_CONTROLS] = {
    { CTRL_ID_BRIGHTNESS,      CTRL_TYPE_UINT8,  CTRL_FLAG_HARDWARE, 0,     255,   "brightness", "Global LED brightness" },
    { CTRL_ID_GAMMA,           CTRL_TYPE_UINT8,  CTRL_FLAG_HARDWARE, 10,    30,    "gamma", "Gamma correction (1.0-3.0, stored as x10)" },
    { CTRL_ID_IDLE_TIMEOUT,    CTRL_TYPE_UINT16, CTRL_FLAG_HARDWARE, 0,     32767, "idle_timeout", "Seconds until local mode activates (0=never)" },
    { CTRL_ID_AUTO_SHOW,       CTRL_TYPE_BOOL,   CTRL_FLAG_HARDWARE | CTRL_FLAG_VOLATILE, 0, 1, "auto_show", "Auto-display after pixel commands" },
    { CTRL_ID_FRAME_ACK,       CTRL_TYPE_BOOL,   CTRL_FLAG_HARDWARE | CTRL_FLAG_VOLATILE, 0, 1, "frame_ack", "Send acknowledgment after frames" },
    { CTRL_ID_STATUS_INTERVAL, CTRL_TYPE_UINT16, CTRL_FLAG_HARDWARE | CTRL_FLAG_VOLATILE, 0, 32767, "status_interval", "Status broadcast interval in ms (0=off)" },
    { CTRL_ID_LOCAL_MODE,      CTRL_TYPE_UINT8,  CTRL_FLAG_HARDWARE, 0,     255,   "local_mode", "Local animation mode (0=off, 255=cycle)" },
    { CTRL_ID_CYCLE_TIME,      CTRL_TYPE_UINT16, CTRL_FLAG_HARDWARE, 1000,  32767, "cycle_time", "Mode cycle interval in ms" },
    // Action controls
    { CTRL_ID_SAVE_CONFIG,     CTRL_TYPE_ACTION, CTRL_FLAG_HARDWARE | CTRL_FLAG_ACTION, 0, 0, "save", "Save current config to EEPROM" },
    { CTRL_ID_REBOOT,          CTRL_TYPE_ACTION, CTRL_FLAG_HARDWARE | CTRL_FLAG_ACTION, 0, 0, "reboot", "Restart the device" },
};

// Get current value of a control (returns value, size in bytes via pointer)
uint16_t getControlValue(uint8_t controlId, uint8_t* valueSize) {
    *valueSize = 1;  // Default to 1 byte
    switch (controlId) {
        case CTRL_ID_BRIGHTNESS:
            return config.brightness;
        case CTRL_ID_GAMMA:
            return config.gamma;
        case CTRL_ID_IDLE_TIMEOUT:
            *valueSize = 2;
            return config.idleTimeout;
        case CTRL_ID_AUTO_SHOW:
            return config.autoShow ? 1 : 0;
        case CTRL_ID_FRAME_ACK:
            return config.frameAck ? 1 : 0;
        case CTRL_ID_STATUS_INTERVAL:
            *valueSize = 2;
            return config.statusInterval;
        case CTRL_ID_LOCAL_MODE:
            return config.localMode;
        case CTRL_ID_CYCLE_TIME:
            *valueSize = 2;
            return config.cycleTime;
        case CTRL_ID_SAVE_CONFIG:
        case CTRL_ID_REBOOT:
            // Action controls have no persistent value
            return 0;
        default:
            return 0;
    }
}

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

#ifdef USE_FASTLED

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

void setBrightness(uint8_t brightness) {
    FastLED.setBrightness(brightness);
}

#else  // USE_CUSTOM_APA102_DRIVER

void showLeds() {
    ledDriver.show();
}

void clearLeds() {
    ledDriver.clear();
}

void setPixel(uint16_t idx, uint8_t r, uint8_t g, uint8_t b) {
    ledDriver.setPixel(idx, r, g, b);
}

uint32_t getPixelColor(uint16_t idx) {
    return ledDriver.getPixelColor(idx);
}

void fillAll(uint8_t r, uint8_t g, uint8_t b) {
    ledDriver.fill(r, g, b);
}

void fillRange(uint16_t start, uint16_t end, uint8_t r, uint8_t g, uint8_t b) {
    if (start >= NUM_LEDS) return;
    if (end > NUM_LEDS) end = NUM_LEDS;
    for (uint16_t i = start; i < end; i++) {
        ledDriver.setPixel(i, r, g, b);
    }
}

void setBrightness(uint8_t brightness) {
    ledDriver.setBrightness(brightness);
}

#endif  // USE_FASTLED

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

#ifndef USE_FASTLED
// These are only needed when not using FastLED (which provides them)

uint8_t random8() {
    return random(256);
}

uint8_t random8(uint8_t lim) {
    return random(lim);
}

uint8_t random8(uint8_t min, uint8_t lim) {
    return min + random(lim - min);
}

uint16_t random16() {
    return random(65536);
}

uint16_t random16(uint16_t lim) {
    return random(lim);
}

uint8_t qadd8(uint8_t a, uint8_t b) {
    uint16_t sum = (uint16_t)a + (uint16_t)b;
    return sum > 255 ? 255 : sum;
}

#endif  // !USE_FASTLED

// HSV to RGB conversion (used by both drivers for local modes)
void hsvToRgbRainbow(uint8_t h, uint8_t s, uint8_t v, uint8_t& r, uint8_t& g, uint8_t& b) {
#ifdef USE_FASTLED
    CHSV hsv(h, s, v);
    CRGB rgb;
    hsv2rgb_rainbow(hsv, rgb);
    r = rgb.r;
    g = rgb.g;
    b = rgb.b;
#else
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
#endif
}

// Heat color palette (for fire effect)
void heatColor(uint8_t temperature, uint8_t& r, uint8_t& g, uint8_t& b) {
#ifdef USE_FASTLED
    CRGB color = HeatColor(temperature);
    r = color.r;
    g = color.g;
    b = color.b;
#else
    // Scale 'heat' down from 0-255 to 0-191
    uint8_t t192 = (temperature > 0) ? ((uint16_t)temperature * 191 / 255) : 0;

    // Calculate ramp up from
    uint8_t heatramp = t192 & 0x3F; // 0..63
    heatramp <<= 2; // scale up to 0..252

    if (t192 > 0x80) {        // hottest (white)
        r = 255;
        g = 255;
        b = heatramp;
    } else if (t192 > 0x40) { // middle (yellow)
        r = 255;
        g = heatramp;
        b = 0;
    } else {                   // coolest (red)
        r = heatramp;
        g = 0;
        b = 0;
    }
#endif
}

// Fade pixel toward black
void fadePixelToBlack(uint16_t idx, uint8_t fadeAmount) {
#ifdef USE_FASTLED
    if (idx < NUM_LEDS) {
        leds[idx].fadeToBlackBy(fadeAmount);
    }
#else
    uint8_t r, g, b;
    ledDriver.getPixel(idx, r, g, b);
    r = (r > fadeAmount) ? r - fadeAmount : 0;
    g = (g > fadeAmount) ? g - fadeAmount : 0;
    b = (b > fadeAmount) ? b - fadeAmount : 0;
    ledDriver.setPixel(idx, r, g, b);
#endif
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
    const char* name = inputNames[inputId];
    uint8_t nameLen = strlen(name);
    if (nameLen > 15) nameLen = 15;

    uint8_t payload[6 + 1 + nameLen];
    payload[0] = inputId;                   // Input ID
    payload[1] = inputs[inputId].type;      // Input type
    // Timestamp (16-bit, little-endian)
    uint16_t timestamp = millis() & 0xFFFF;
    payload[2] = timestamp & 0xFF;
    payload[3] = timestamp >> 8;
    // Data: current value
    payload[4] = state ? 1 : 0;             // Current value
    payload[5] = nameLen;                   // Name length
    memcpy(payload + 6, name, nameLen);     // Name

    protocol.sendPacket(CMD_INPUT_EVENT, payload, 6 + 1 + nameLen);
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
    hsvToRgbRainbow(h, s, v, r, g, b);
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
    static uint16_t lastPosition = 0xFFFF;
    const uint8_t eyeSize = 5;
    const uint8_t fadeAmount = 64;

    // Detect mode restart (modePosition reset to 0 by startLocalMode)
    // or underflow protection
    if (modePosition == 0 && lastPosition > 1) {
        direction = true;  // Reset direction on mode start
    }
    if (modePosition > NUM_LEDS) {
        modePosition = 0;
        direction = true;
    }
    lastPosition = modePosition;

    // Fade all pixels
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        fadePixelToBlack(i, fadeAmount);
    }

    // Draw the eye
    for (uint8_t i = 0; i < eyeSize; i++) {
        int16_t pos = modePosition + i - eyeSize/2;
        if (pos >= 0 && pos < NUM_LEDS) {
            uint8_t bright = 255 - abs(i - eyeSize/2) * 40;
            setPixel(pos, bright, 0, 0);
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
    uint8_t r, g, b;
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        hsvToRgbRainbow(modeHue + (i * 256 / NUM_LEDS), 255, 200, r, g, b);
        setPixel(i, r, g, b);
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
    uint8_t r, g, b;
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        heatColor(heat[i], r, g, b);
        setPixel(i, r, g, b);
    }

    showLeds();
}

void updateSparkle() {
    const uint8_t fadeAmount = 20;

    // Fade all pixels
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        fadePixelToBlack(i, fadeAmount);
    }

    // Add random sparkles
    uint8_t sparkleCount = NUM_LEDS > 200 ? 8 : 3;
    uint8_t r, g, b;
    for (uint8_t i = 0; i < sparkleCount; i++) {
        uint16_t pos = random16(NUM_LEDS);
        hsvToRgbRainbow(random8(), 200, 255, r, g, b);
        setPixel(pos, r, g, b);
    }

    showLeds();
}

void updateChase() {
    clearLeds();

    const uint8_t chaseLength = 8;
    uint8_t r, g, b;

    for (uint8_t i = 0; i < chaseLength; i++) {
        uint16_t pos = (modePosition + i) % NUM_LEDS;
        hsvToRgbRainbow(modeHue, 255, 255 - i * 25, r, g, b);
        setPixel(pos, r, g, b);
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

    // Handle cycle mode - switch based on cycleTime
    if (config.localMode == LOCAL_MODE_CYCLE) {
        if (now - modeStartTime > (uint32_t)config.cycleTime * 1000) {
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
    uint8_t response[600];  // Large enough for INFO_CONTROLS with descriptions
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

        case INFO_CONTROLS:
            // Return control metadata: count, then for each:
            // id(1), type(1), flags(1), min(2), max(2), value(1-2), name(null-term), description(null-term)
            response[respLen++] = NUM_CONTROLS;
            for (uint8_t i = 0; i < NUM_CONTROLS; i++) {
                const ControlDef& ctrl = controlDefs[i];
                response[respLen++] = ctrl.id;
                response[respLen++] = ctrl.type;
                response[respLen++] = ctrl.flags;
                response[respLen++] = ctrl.minVal & 0xFF;
                response[respLen++] = (ctrl.minVal >> 8) & 0xFF;
                response[respLen++] = ctrl.maxVal & 0xFF;
                response[respLen++] = (ctrl.maxVal >> 8) & 0xFF;
                // Current value (1 or 2 bytes depending on type)
                uint8_t valueSize;
                uint16_t value = getControlValue(ctrl.id, &valueSize);
                response[respLen++] = value & 0xFF;
                if (valueSize > 1) {
                    response[respLen++] = (value >> 8) & 0xFF;
                }
                // Copy name (null-terminated)
                const char* name = ctrl.name;
                while (*name && respLen < sizeof(response) - 2) {
                    response[respLen++] = *name++;
                }
                response[respLen++] = 0;
                // Copy description (null-terminated)
                const char* desc = ctrl.description;
                while (*desc && respLen < sizeof(response) - 1) {
                    response[respLen++] = *desc++;
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

void handleGetInput(const uint8_t* payload, uint16_t length) {
    if (length < 1) {
        protocol.sendNak(CMD_GET_INPUT, ERR_INVALID_LENGTH);
        return;
    }

    uint8_t inputId = payload[0];

    if (inputId == 0xFF) {
        // Return all inputs with names
        // Format: [num_inputs, reserved, (id, type, value, nameLen, name[])...]
        uint8_t response[128];  // Should be enough for a few inputs with names
        uint8_t offset = 0;

        response[offset++] = NUM_INPUTS;
        response[offset++] = 0; // Reserved

        for (uint8_t i = 0; i < NUM_INPUTS; i++) {
            const char* name = inputNames[i];
            uint8_t nameLen = strlen(name);
            if (nameLen > 15) nameLen = 15;

            response[offset++] = i;                         // Input ID
            response[offset++] = inputs[i].type;            // Type
            response[offset++] = inputs[i].currentState ? 1 : 0;  // Value
            response[offset++] = nameLen;                   // Name length
            memcpy(response + offset, name, nameLen);       // Name
            offset += nameLen;
        }

        protocol.sendPacket(CMD_INPUTS_LIST, response, offset);
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
            setBrightness(config.brightness);
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

        case CTRL_ID_SAVE_CONFIG:
            // Action: save config to EEPROM
            saveConfig();
            break;

        case CTRL_ID_REBOOT:
            // Action: reboot device (Teensy)
            protocol.sendAck(CMD_SET_CONTROL);
            delay(100);
            #if defined(__MK20DX256__) || defined(__MK64FX512__) || defined(__MK66FX1M0__)
            // Teensy 3.x software reset
            SCB_AIRCR = 0x05FA0004;
            #endif
            return;

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
        case CTRL_ID_SAVE_CONFIG:
        case CTRL_ID_REBOOT:
            // Action controls have no value
            response[respLen++] = 0;
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
            setBrightness(config.brightness);
            protocol.sendAck(CMD_LOAD_CONFIG);
            break;

        case CMD_RESET_CONFIG:
            resetConfig();
            setBrightness(config.brightness);
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
    // Initialize heartbeat LED
    pinMode(HEARTBEAT_PIN, OUTPUT);

    Serial.begin(SERIAL_BAUD);
    // Wait for USB serial to be ready (up to 3 seconds)
    uint32_t serialStart = millis();
    while (!Serial && millis() - serialStart < 3000) {
        delay(10);
    }

    // Load saved configuration
    loadConfig();

    // Initialize LED driver
#ifdef USE_FASTLED
    FastLED.addLeds<APA102, LED_DATA_PIN, LED_CLOCK_PIN, BGR>(leds, NUM_LEDS);
#else
    ledDriver.begin();
#endif

    setBrightness(config.brightness);
    clearLeds();
    showLeds();

    // Initialize inputs
    initInputs();

    // Initialize activity timer
    lastActivityTime = millis();
    stats.startTime = lastActivityTime;

    // Brief startup indicator - green flash on first 10 LEDs
    for (uint16_t i = 0; i < min((uint16_t)10, (uint16_t)NUM_LEDS); i++) {
        setPixel(i, 0, 32, 0);
    }
    showLeds();
    delay(200);
    clearLeds();
    showLeds();

    // Start local mode if configured
    if (config.localMode != LOCAL_MODE_BLANK) {
        startLocalMode(config.localMode);
    }

    delay(100);
    sendHello();
}

void loop() {
    // Heartbeat LED
    uint32_t now = millis();
    if (now - lastHeartbeat >= HEARTBEAT_INTERVAL) {
        lastHeartbeat = now;
        digitalWrite(HEARTBEAT_PIN, !digitalRead(HEARTBEAT_PIN));
    }

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
