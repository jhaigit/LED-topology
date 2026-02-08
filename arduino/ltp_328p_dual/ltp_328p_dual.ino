/**
 * LTP 328P Dual Strip - ATmega328P Implementation
 *
 * LED strip controller with two strips (only one active at a time) and
 * two buttons for interactive installations.
 *
 * Hardware:
 *   - ATmega328P (Arduino Uno/Nano)
 *   - WS2812 strip on pin D8
 *   - APA102 strip on pins D3 (data), D2 (clock)
 *   - 2 buttons on pins A4 and A5 (active low)
 *
 * Only one strip may be used at a time. A single pixel buffer is shared
 * between both strip drivers. The active strip and pixel count are
 * runtime controls, settable over the protocol and persisted to EEPROM.
 *
 * Features:
 *   - Full LTP protocol v2 support
 *   - Input event reporting for buttons
 *   - Local display modes (cylon, rainbow, fire, sparkle, chase)
 *   - EEPROM configuration persistence
 *   - Runtime-switchable strip output
 *   - Runtime-adjustable pixel count
 */

#include <EEPROM.h>
#if defined(__AVR__)
#include <avr/wdt.h>
#endif
#include "config.h"
#include <ltp_protocol.h>
#include "build_info.h"

// ============================================================================
// SHARED PIXEL BUFFER
// ============================================================================

// Single pixel buffer shared by both strip drivers.
// Stored in RGB order, 3 bytes per pixel.
static uint8_t pixelBuffer[MAX_PIXELS * 3];

// ============================================================================
// WS2812 DRIVER (bit-bang on arbitrary pin)
// ============================================================================

// Minimal WS2812 bit-bang driver for AVR.
// Uses inline assembly for timing-critical output on any pin.
static volatile uint8_t* ws2812Port;
static uint8_t ws2812PinMask;

void ws2812Init() {
    pinMode(WS2812_DATA_PIN, OUTPUT);
    digitalWrite(WS2812_DATA_PIN, LOW);
    ws2812Port = portOutputRegister(digitalPinToPort(WS2812_DATA_PIN));
    ws2812PinMask = digitalPinToBitMask(WS2812_DATA_PIN);
}

// Send a single byte out the WS2812 pin with precise timing.
// Must be called with interrupts already disabled.
// Timing for WS2812 at 16 MHz (62.5 ns per cycle):
//   T0H: 350ns (6 cycles), T0L: 800ns (13 cycles)
//   T1H: 700ns (11 cycles), T1L: 600ns (10 cycles)
static inline void ws2812SendByte(uint8_t b, uint8_t pinHigh, uint8_t pinLow,
                                   volatile uint8_t* port) {
    for (uint8_t bit = 0; bit < 8; bit++) {
        if (b & 0x80) {
            *port = pinHigh;
            __asm__ __volatile__(
                "nop\n\t" "nop\n\t" "nop\n\t" "nop\n\t"
                "nop\n\t" "nop\n\t" "nop\n\t" "nop\n\t"
                "nop\n\t"
            );
            *port = pinLow;
            __asm__ __volatile__(
                "nop\n\t" "nop\n\t" "nop\n\t" "nop\n\t"
                "nop\n\t"
            );
        } else {
            *port = pinHigh;
            __asm__ __volatile__(
                "nop\n\t" "nop\n\t" "nop\n\t"
            );
            *port = pinLow;
            __asm__ __volatile__(
                "nop\n\t" "nop\n\t" "nop\n\t" "nop\n\t"
                "nop\n\t" "nop\n\t" "nop\n\t" "nop\n\t"
            );
        }
        b <<= 1;
    }
}

// Send pixel data from an RGB buffer as WS2812 GRB with brightness scaling.
// Reads pixels as (R,G,B) triplets, sends as (G,R,B) with brightness applied.
// The source buffer is not modified.
void ws2812SendRgb(const uint8_t* rgbData, uint16_t count, uint8_t brightness) {
    volatile uint8_t* port = ws2812Port;
    uint8_t pinMask = ws2812PinMask;
    uint8_t pinLow = *port & ~pinMask;
    uint8_t pinHigh = *port | pinMask;

    cli();

    for (uint16_t i = 0; i < count; i++) {
        uint16_t off = i * 3;
        uint8_t r = scale8(rgbData[off + 0], brightness);
        uint8_t g = scale8(rgbData[off + 1], brightness);
        uint8_t b = scale8(rgbData[off + 2], brightness);
        // WS2812 order: G, R, B
        ws2812SendByte(g, pinHigh, pinLow, port);
        ws2812SendByte(r, pinHigh, pinLow, port);
        ws2812SendByte(b, pinHigh, pinLow, port);
    }

    sei();
    delayMicroseconds(80);
}

// ============================================================================
// APA102 DRIVER (bit-bang SPI)
// ============================================================================

void apa102Init() {
    pinMode(APA102_DATA_PIN, OUTPUT);
    pinMode(APA102_CLOCK_PIN, OUTPUT);
    digitalWrite(APA102_DATA_PIN, LOW);
    digitalWrite(APA102_CLOCK_PIN, LOW);
}

static void apa102SendByte(uint8_t b) {
    for (uint8_t bit = 0; bit < 8; bit++) {
        digitalWrite(APA102_DATA_PIN, (b & 0x80) ? HIGH : LOW);
        digitalWrite(APA102_CLOCK_PIN, HIGH);
        b <<= 1;
        digitalWrite(APA102_CLOCK_PIN, LOW);
    }
}

// ============================================================================
// PROTOCOL HANDLER
// ============================================================================

LtpProtocol protocol(Serial, MAX_PAYLOAD_SIZE);

// ============================================================================
// EEPROM CONFIGURATION
// ============================================================================

#define CONFIG_MAGIC        0x4C44  // "LD" - magic for dual strip
#define CONFIG_VERSION      1
#define EEPROM_CONFIG_ADDR  0

// Local display modes
#define LOCAL_MODE_BLANK    0
#define LOCAL_MODE_CYLON    1
#define LOCAL_MODE_RAINBOW  2
#define LOCAL_MODE_FIRE     3
#define LOCAL_MODE_SPARKLE  4
#define LOCAL_MODE_CHASE    5
#define LOCAL_MODE_CYCLE    255
#define LOCAL_MODE_COUNT    6

// Custom control IDs (after standard ones)
#define CTRL_ID_NUM_PIXELS    8
#define CTRL_ID_ACTIVE_STRIP  9

struct Config {
    uint16_t magic;
    uint8_t version;
    uint8_t brightness;
    uint8_t gamma;
    uint16_t idleTimeout;
    bool autoShow;
    bool frameAck;
    uint16_t statusInterval;
    uint8_t localMode;
    uint16_t cycleTime;
    // Dual-strip specific
    uint16_t numPixels;         // Active pixel count (1..MAX_PIXELS)
    uint8_t activeStrip;        // STRIP_ID_WS2812 or STRIP_ID_APA102
    bool inputEventsEnabled;
} config = {
    CONFIG_MAGIC,
    CONFIG_VERSION,
    255,                        // brightness
    22,                         // gamma (2.2)
    DEFAULT_IDLE_TIMEOUT,
    false,                      // autoShow
    false,                      // frameAck
    0,                          // statusInterval
    LOCAL_MODE_BLANK,
    10,                         // cycleTime
    DEFAULT_NUM_PIXELS,
    DEFAULT_ACTIVE_STRIP,
    true                        // inputEventsEnabled
};

// Runtime pixel count (mirrors config.numPixels, used everywhere)
uint16_t numPixels;

// ============================================================================
// INPUT HANDLING
// ============================================================================

struct InputState {
    uint8_t pin;
    uint8_t type;
    bool activeHigh;
    bool currentState;
    bool lastRawState;
    bool lastReportedState;
    uint32_t lastChangeTime;
};

InputState inputs[NUM_INPUTS] = {
    { BUTTON_1_PIN, INPUT_BUTTON, !BUTTON_ACTIVE_LOW, false, false, false, 0 },
    { BUTTON_2_PIN, INPUT_BUTTON, !BUTTON_ACTIVE_LOW, false, false, false, 0 }
};

const char* inputNames[NUM_INPUTS] = {
    "Button1",
    "Button2"
};

// ============================================================================
// STATE TRACKING
// ============================================================================

uint32_t lastActivityTime = 0;
bool isIdle = false;

bool localModeActive = false;
uint8_t currentDisplayMode = 0;
uint32_t lastModeUpdate = 0;
uint32_t modeStartTime = 0;
uint16_t modePosition = 0;
uint8_t modeHue = 0;

struct {
    uint32_t framesReceived = 0;
    uint32_t framesDisplayed = 0;
    uint32_t bytesReceived = 0;
    uint16_t checksumErrors = 0;
    uint16_t bufferOverflows = 0;
    uint32_t startTime = 0;
    uint32_t inputEventsCount = 0;
} stats;

// Heartbeat - use LED_BUILTIN only if it doesn't conflict with our pins
#if defined(LED_BUILTIN) && (LED_BUILTIN != WS2812_DATA_PIN) && (LED_BUILTIN != APA102_DATA_PIN) && (LED_BUILTIN != APA102_CLOCK_PIN)
    #define HEARTBEAT_ENABLED   true
    #define HEARTBEAT_PIN       LED_BUILTIN
#else
    #define HEARTBEAT_ENABLED   false
    #define HEARTBEAT_PIN       -1
#endif
#define HEARTBEAT_INTERVAL  500
uint32_t lastHeartbeat = 0;
bool heartbeatState = false;

// ============================================================================
// CONTROL DEFINITIONS
// ============================================================================

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
    { CTRL_ID_BRIGHTNESS,      CTRL_TYPE_UINT8,  CTRL_FLAG_HARDWARE, 0,     255,        "brightness",    "0-255" },
    { CTRL_ID_GAMMA,           CTRL_TYPE_UINT8,  CTRL_FLAG_HARDWARE, 10,    30,         "gamma",         "x10, 10=1.0" },
    { CTRL_ID_IDLE_TIMEOUT,    CTRL_TYPE_UINT16, CTRL_FLAG_HARDWARE, 0,     32767,      "idle_timeout",  "secs, 0=off" },
    { CTRL_ID_AUTO_SHOW,       CTRL_TYPE_BOOL,   CTRL_FLAG_HARDWARE | CTRL_FLAG_VOLATILE, 0, 1, "auto_show", "show after cmds" },
    { CTRL_ID_FRAME_ACK,       CTRL_TYPE_BOOL,   CTRL_FLAG_HARDWARE | CTRL_FLAG_VOLATILE, 0, 1, "frame_ack", "ack frames" },
    { CTRL_ID_STATUS_INTERVAL, CTRL_TYPE_UINT16, CTRL_FLAG_HARDWARE | CTRL_FLAG_VOLATILE, 0, 32767, "status_interval", "ms, 0=off" },
    { CTRL_ID_LOCAL_MODE,      CTRL_TYPE_UINT8,  CTRL_FLAG_HARDWARE, 0,     255,        "local_mode",    "0=off, 255=cycle" },
    { CTRL_ID_CYCLE_TIME,      CTRL_TYPE_UINT16, CTRL_FLAG_HARDWARE, 1,     3600,       "cycle_time",    "mode cycle, secs" },
    { CTRL_ID_NUM_PIXELS,      CTRL_TYPE_UINT16, CTRL_FLAG_HARDWARE, 1,     MAX_PIXELS, "num_pixels",    "active pixel count" },
    { CTRL_ID_ACTIVE_STRIP,    CTRL_TYPE_UINT8,  CTRL_FLAG_HARDWARE, 0,     1,          "active_strip",  "0=WS2812, 1=APA102" },
    // Action controls
    { CTRL_ID_SAVE_CONFIG,     CTRL_TYPE_ACTION, CTRL_FLAG_HARDWARE | CTRL_FLAG_ACTION, 0, 0, "save",   "save to EEPROM" },
    { CTRL_ID_REBOOT,          CTRL_TYPE_ACTION, CTRL_FLAG_HARDWARE | CTRL_FLAG_ACTION, 0, 0, "reboot", "restart" },
};

uint16_t getControlValue(uint8_t controlId, uint8_t* valueSize) {
    *valueSize = 1;
    switch (controlId) {
        case CTRL_ID_BRIGHTNESS:      return config.brightness;
        case CTRL_ID_GAMMA:           return config.gamma;
        case CTRL_ID_IDLE_TIMEOUT:    *valueSize = 2; return config.idleTimeout;
        case CTRL_ID_AUTO_SHOW:       return config.autoShow ? 1 : 0;
        case CTRL_ID_FRAME_ACK:       return config.frameAck ? 1 : 0;
        case CTRL_ID_STATUS_INTERVAL: *valueSize = 2; return config.statusInterval;
        case CTRL_ID_LOCAL_MODE:      return config.localMode;
        case CTRL_ID_CYCLE_TIME:      *valueSize = 2; return config.cycleTime;
        case CTRL_ID_NUM_PIXELS:      *valueSize = 2; return config.numPixels;
        case CTRL_ID_ACTIVE_STRIP:    return config.activeStrip;
        case CTRL_ID_SAVE_CONFIG:
        case CTRL_ID_REBOOT:          return 0;
        default:                      return 0;
    }
}

// ============================================================================
// LED ABSTRACTION (shared buffer, switchable output)
// ============================================================================

// Apply 8-bit brightness scaling
static uint8_t scale8(uint8_t value, uint8_t bright) {
    return ((uint16_t)value * (uint16_t)(bright + 1)) >> 8;
}

void setPixel(uint16_t idx, uint8_t r, uint8_t g, uint8_t b) {
    if (idx >= numPixels) return;
    uint16_t offset = idx * 3;
    pixelBuffer[offset + 0] = r;
    pixelBuffer[offset + 1] = g;
    pixelBuffer[offset + 2] = b;
}

void getPixel(uint16_t idx, uint8_t& r, uint8_t& g, uint8_t& b) {
    if (idx >= numPixels) { r = g = b = 0; return; }
    uint16_t offset = idx * 3;
    r = pixelBuffer[offset + 0];
    g = pixelBuffer[offset + 1];
    b = pixelBuffer[offset + 2];
}

void clearLeds() {
    memset(pixelBuffer, 0, numPixels * 3);
}

void fillAll(uint8_t r, uint8_t g, uint8_t b) {
    for (uint16_t i = 0; i < numPixels; i++) {
        setPixel(i, r, g, b);
    }
}

void fillRange(uint16_t start, uint16_t end, uint8_t r, uint8_t g, uint8_t b) {
    if (start >= numPixels) return;
    if (end > numPixels) end = numPixels;
    for (uint16_t i = start; i < end; i++) {
        setPixel(i, r, g, b);
    }
}

void showLeds() {
    if (config.activeStrip == STRIP_ID_WS2812) {
        // Send RGB buffer as GRB with brightness — no extra memory needed
        ws2812SendRgb(pixelBuffer, numPixels, config.brightness);
    } else {
        // APA102: bit-bang SPI, no timing constraints
        apa102SendByte(0x00); apa102SendByte(0x00);
        apa102SendByte(0x00); apa102SendByte(0x00);

        for (uint16_t i = 0; i < numPixels; i++) {
            uint16_t off = i * 3;
            apa102SendByte(0xFF); // 0xE0 | 31 (max per-LED brightness)
            apa102SendByte(scale8(pixelBuffer[off + 2], config.brightness)); // B
            apa102SendByte(scale8(pixelBuffer[off + 1], config.brightness)); // G
            apa102SendByte(scale8(pixelBuffer[off + 0], config.brightness)); // R
        }

        uint8_t endBytes = (numPixels + 15) / 16 + 1;
        for (uint8_t i = 0; i < endBytes; i++) {
            apa102SendByte(0xFF);
        }
    }
}

// Turn off whichever strip is NOT active (blank it out)
void blankInactiveStrip() {
    if (config.activeStrip == STRIP_ID_WS2812) {
        // Blank APA102
        apa102SendByte(0x00); apa102SendByte(0x00);
        apa102SendByte(0x00); apa102SendByte(0x00);
        for (uint16_t i = 0; i < numPixels; i++) {
            apa102SendByte(0xE0); // brightness 0
            apa102SendByte(0x00);
            apa102SendByte(0x00);
            apa102SendByte(0x00);
        }
        uint8_t endBytes = (numPixels + 15) / 16 + 1;
        for (uint8_t i = 0; i < endBytes; i++) {
            apa102SendByte(0xFF);
        }
    } else {
        // Blank WS2812 - send all-zero bytes directly
        volatile uint8_t* port = ws2812Port;
        uint8_t pinMask = ws2812PinMask;
        uint8_t pinLow = *port & ~pinMask;
        uint8_t pinHigh = *port | pinMask;

        cli();
        for (uint16_t sent = 0; sent < numPixels * 3; sent++) {
            ws2812SendByte(0, pinHigh, pinLow, port);
        }
        sei();
        delayMicroseconds(80);
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
    // Clamp numPixels to valid range
    if (config.numPixels == 0 || config.numPixels > MAX_PIXELS) {
        config.numPixels = DEFAULT_NUM_PIXELS;
    }
    if (config.activeStrip > STRIP_ID_APA102) {
        config.activeStrip = DEFAULT_ACTIVE_STRIP;
    }
    numPixels = config.numPixels;
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
    config.cycleTime = 10;
    config.numPixels = DEFAULT_NUM_PIXELS;
    config.activeStrip = DEFAULT_ACTIVE_STRIP;
    config.inputEventsEnabled = true;
    numPixels = config.numPixels;
    saveConfig();
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

void sendInputEvent(uint8_t inputId, bool state);

void updateInputs() {
    uint32_t now = millis();

    for (uint8_t i = 0; i < NUM_INPUTS; i++) {
        bool raw = digitalRead(inputs[i].pin);

        if (raw != inputs[i].lastRawState) {
            inputs[i].lastRawState = raw;
            inputs[i].lastChangeTime = now;
        }

        if ((now - inputs[i].lastChangeTime) > DEBOUNCE_TIME) {
            bool state = inputs[i].activeHigh ? raw : !raw;

            if (state != inputs[i].currentState) {
                inputs[i].currentState = state;

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
    payload[0] = inputId;
    payload[1] = inputs[inputId].type;
    uint16_t timestamp = millis() & 0xFFFF;
    payload[2] = timestamp & 0xFF;
    payload[3] = timestamp >> 8;
    payload[4] = state ? 1 : 0;
    payload[5] = nameLen;
    memcpy(payload + 6, name, nameLen);

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
    if (s == 0) { r = g = b = v; return; }

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
    const uint8_t eyeSize = 3;
    const uint8_t fadeAmount = 64;

    if (modePosition == 0 && lastPosition > 1) {
        direction = true;
    }
    if (modePosition > numPixels) {
        modePosition = 0;
        direction = true;
    }
    lastPosition = modePosition;

    for (uint16_t i = 0; i < numPixels; i++) {
        uint8_t r, g, b;
        getPixel(i, r, g, b);
        r = r > fadeAmount ? r - fadeAmount : 0;
        g = g > fadeAmount ? g - fadeAmount : 0;
        b = b > fadeAmount ? b - fadeAmount : 0;
        setPixel(i, r, g, b);
    }

    for (uint8_t i = 0; i < eyeSize; i++) {
        int16_t pos = modePosition + i - eyeSize / 2;
        if (pos >= 0 && pos < (int16_t)numPixels) {
            uint8_t brightness = 255 - abs(i - eyeSize / 2) * 60;
            setPixel(pos, brightness, 0, 0);
        }
    }

    if (direction) {
        modePosition++;
        if (modePosition >= numPixels - 1) direction = false;
    } else {
        modePosition--;
        if (modePosition == 0) direction = true;
    }

    showLeds();
}

void updateRainbow() {
    for (uint16_t i = 0; i < numPixels; i++) {
        uint8_t pixelHue = modeHue + (i * 256 / numPixels);
        uint8_t r, g, b;
        hsvToRgb(pixelHue, 255, 200, r, g, b);
        setPixel(i, r, g, b);
    }
    modeHue++;
    showLeds();
}

void updateFire() {
    uint16_t firePixels = numPixels > MAX_PIXELS ? MAX_PIXELS : numPixels;
    static uint8_t heat[MAX_PIXELS];
    const uint8_t cooling = 55;
    const uint8_t sparking = 120;

    for (uint16_t i = 0; i < firePixels; i++) {
        uint8_t cooldown = random(0, ((cooling * 10) / firePixels) + 2);
        heat[i] = heat[i] > cooldown ? heat[i] - cooldown : 0;
    }

    for (uint16_t i = firePixels - 1; i >= 2; i--) {
        heat[i] = (heat[i - 1] + heat[i - 2] + heat[i - 2]) / 3;
    }

    if (random(255) < sparking) {
        uint8_t y = random(7);
        heat[y] = heat[y] + random(160, 255);
        if (heat[y] > 255) heat[y] = 255;
    }

    for (uint16_t i = 0; i < firePixels; i++) {
        uint8_t t192 = (heat[i] * 192) / 255;
        uint8_t r, g, b;
        if (t192 < 64) {
            r = t192 * 4; g = 0; b = 0;
        } else if (t192 < 128) {
            r = 255; g = (t192 - 64) * 4; b = 0;
        } else {
            r = 255; g = 255; b = (t192 - 128) * 4;
        }
        setPixel(i, r, g, b);
    }
    showLeds();
}

void updateSparkle() {
    const uint8_t fadeAmount = 20;

    for (uint16_t i = 0; i < numPixels; i++) {
        uint8_t r, g, b;
        getPixel(i, r, g, b);
        r = r > fadeAmount ? r - fadeAmount : 0;
        g = g > fadeAmount ? g - fadeAmount : 0;
        b = b > fadeAmount ? b - fadeAmount : 0;
        setPixel(i, r, g, b);
    }

    for (uint8_t i = 0; i < 3; i++) {
        uint16_t pos = random(numPixels);
        uint8_t r, g, b;
        hsvToRgb(random(256), 200, 255, r, g, b);
        setPixel(pos, r, g, b);
    }

    showLeds();
}

void updateChase() {
    const uint8_t chaseLength = 5;
    clearLeds();

    for (uint8_t i = 0; i < chaseLength; i++) {
        uint16_t pos = (modePosition + i) % numPixels;
        uint8_t r, g, b;
        hsvToRgb(modeHue, 255, 255 - i * 40, r, g, b);
        setPixel(pos, r, g, b);
    }

    modePosition = (modePosition + 1) % numPixels;
    modeHue += 2;
    showLeds();
}

void updateLocalMode() {
    if (!localModeActive) return;

    uint32_t now = millis();
    uint32_t interval;

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
    uint8_t payload[12];
    payload[0] = LTP_PROTOCOL_MAJOR;
    payload[1] = LTP_PROTOCOL_MINOR;
    payload[2] = (FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR;
    payload[3] = 0;
    payload[4] = 1; // 1 active strip (logically)
    payload[5] = numPixels & 0xFF;
    payload[6] = numPixels >> 8;
    payload[7] = COLOR_RGB;
    payload[8] = CAPS_BRIGHTNESS | CAPS_EXTENDED;
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
    uint8_t response[350];
    uint16_t respLen = 0;

    switch (infoType) {
        case INFO_ALL:
            response[respLen++] = LTP_PROTOCOL_MAJOR;
            response[respLen++] = LTP_PROTOCOL_MINOR;
            response[respLen++] = (FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR;
            response[respLen++] = 0;
            response[respLen++] = 1; // 1 active strip
            response[respLen++] = numPixels & 0xFF;
            response[respLen++] = numPixels >> 8;
            response[respLen++] = COLOR_RGB;
            response[respLen++] = CAPS_BRIGHTNESS | CAPS_EXTENDED;
            response[respLen++] = CAPS_PIXEL_READBACK | CAPS_EEPROM | CAPS_INPUTS;
            response[respLen++] = NUM_CONTROLS;
            response[respLen++] = NUM_INPUTS;
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
            {
                const char* name = FIRMWARE_NAME;
                uint8_t i = 0;
                while (name[i] && i < 15) {
                    response[respLen++] = name[i++];
                }
                response[respLen++] = 0;
            }
            {
                const char* commit = GIT_COMMIT;
                uint8_t i = 0;
                while (commit[i] && i < 15) {
                    response[respLen++] = commit[i++];
                }
                response[respLen++] = 0;
            }
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
            // Report both physical strips but indicate which is active
            response[respLen++] = 2; // 2 physical strips
            // Strip 0: WS2812
            response[respLen++] = STRIP_ID_WS2812;
            response[respLen++] = numPixels & 0xFF;
            response[respLen++] = numPixels >> 8;
            response[respLen++] = COLOR_RGB;
            response[respLen++] = LED_TYPE_WS2812;
            response[respLen++] = WS2812_DATA_PIN;
            response[respLen++] = 0; // No clock
            response[respLen++] = (config.activeStrip == STRIP_ID_WS2812) ? 0x01 : 0x00;
            // Strip 1: APA102
            response[respLen++] = STRIP_ID_APA102;
            response[respLen++] = numPixels & 0xFF;
            response[respLen++] = numPixels >> 8;
            response[respLen++] = COLOR_RGB;
            response[respLen++] = LED_TYPE_APA102;
            response[respLen++] = APA102_DATA_PIN;
            response[respLen++] = APA102_CLOCK_PIN;
            response[respLen++] = (config.activeStrip == STRIP_ID_APA102) ? 0x01 : 0x00;
            break;

        case INFO_STATUS:
            response[respLen++] = 1; // Running
            response[respLen++] = config.brightness;
            response[respLen++] = 0xFF; // Temp N/A
            response[respLen++] = 0x7F;
            response[respLen++] = 0xFF; // Voltage N/A
            response[respLen++] = 0xFF;
            response[respLen++] = 0;
            break;

        case INFO_STATS:
            response[respLen++] = stats.framesReceived & 0xFF;
            response[respLen++] = (stats.framesReceived >> 8) & 0xFF;
            response[respLen++] = (stats.framesReceived >> 16) & 0xFF;
            response[respLen++] = (stats.framesReceived >> 24) & 0xFF;
            response[respLen++] = stats.framesDisplayed & 0xFF;
            response[respLen++] = (stats.framesDisplayed >> 8) & 0xFF;
            response[respLen++] = (stats.framesDisplayed >> 16) & 0xFF;
            response[respLen++] = (stats.framesDisplayed >> 24) & 0xFF;
            response[respLen++] = stats.bytesReceived & 0xFF;
            response[respLen++] = (stats.bytesReceived >> 8) & 0xFF;
            response[respLen++] = (stats.bytesReceived >> 16) & 0xFF;
            response[respLen++] = (stats.bytesReceived >> 24) & 0xFF;
            response[respLen++] = stats.checksumErrors & 0xFF;
            response[respLen++] = stats.checksumErrors >> 8;
            response[respLen++] = stats.bufferOverflows & 0xFF;
            response[respLen++] = stats.bufferOverflows >> 8;
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
                response[respLen++] = i;
                response[respLen++] = inputs[i].type;
                response[respLen++] = inputs[i].currentState ? 1 : 0;
                response[respLen++] = 0;
            }
            break;

        case INFO_CONTROLS:
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
                uint8_t valueSize;
                uint16_t value = getControlValue(ctrl.id, &valueSize);
                response[respLen++] = value & 0xFF;
                if (valueSize > 1) {
                    response[respLen++] = (value >> 8) & 0xFF;
                }
                const char* name = ctrl.name;
                while (*name && respLen < sizeof(response) - 2) {
                    response[respLen++] = *name++;
                }
                response[respLen++] = 0;
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
        uint8_t response[64];
        uint8_t offset = 0;
        response[offset++] = NUM_INPUTS;
        response[offset++] = 0;

        for (uint8_t i = 0; i < NUM_INPUTS; i++) {
            const char* name = inputNames[i];
            uint8_t nameLen = strlen(name);
            if (nameLen > 15) nameLen = 15;
            response[offset++] = i;
            response[offset++] = inputs[i].type;
            response[offset++] = inputs[i].currentState ? 1 : 0;
            response[offset++] = nameLen;
            memcpy(response + offset, name, nameLen);
            offset += nameLen;
        }

        protocol.sendPacket(CMD_INPUTS_LIST, response, offset);
    } else if (inputId < NUM_INPUTS) {
        uint8_t response[8];
        response[0] = inputId;
        response[1] = inputs[inputId].type;
        response[2] = inputs[inputId].currentState ? 1 : 0;
        response[3] = 0;
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

    if (end > numPixels) {
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
    uint16_t expectedBytes = count * 3;

    if (length < dataOffset + expectedBytes) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_INVALID_LENGTH);
        return;
    }

    if (start + count > numPixels) {
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

        case CTRL_ID_NUM_PIXELS:
            if (length >= 3) {
                uint16_t newCount = payload[1] | ((uint16_t)payload[2] << 8);
                if (newCount >= 1 && newCount <= MAX_PIXELS) {
                    // Clear old pixels, update count
                    clearLeds();
                    showLeds();
                    config.numPixels = newCount;
                    numPixels = newCount;
                    clearLeds();
                } else {
                    protocol.sendNak(CMD_SET_CONTROL, ERR_INVALID_PARAM);
                    return;
                }
            }
            break;

        case CTRL_ID_ACTIVE_STRIP:
            if (payload[1] <= STRIP_ID_APA102) {
                if (payload[1] != config.activeStrip) {
                    // Blank the current strip before switching
                    clearLeds();
                    showLeds();
                    config.activeStrip = payload[1];
                    // Blank the old strip (now inactive)
                    blankInactiveStrip();
                    // Show current buffer on new strip
                    showLeds();
                }
            } else {
                protocol.sendNak(CMD_SET_CONTROL, ERR_INVALID_PARAM);
                return;
            }
            break;

        case CTRL_ID_SAVE_CONFIG:
            saveConfig();
            break;

        case CTRL_ID_REBOOT:
            protocol.sendAck(CMD_SET_CONTROL);
            delay(100);
            #if defined(__AVR__)
            wdt_enable(WDTO_15MS);
            while (1) {}
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
        case CTRL_ID_BRIGHTNESS:      response[respLen++] = config.brightness; break;
        case CTRL_ID_GAMMA:           response[respLen++] = config.gamma; break;
        case CTRL_ID_IDLE_TIMEOUT:
            response[respLen++] = config.idleTimeout & 0xFF;
            response[respLen++] = config.idleTimeout >> 8;
            break;
        case CTRL_ID_AUTO_SHOW:       response[respLen++] = config.autoShow ? 1 : 0; break;
        case CTRL_ID_FRAME_ACK:       response[respLen++] = config.frameAck ? 1 : 0; break;
        case CTRL_ID_STATUS_INTERVAL:
            response[respLen++] = config.statusInterval & 0xFF;
            response[respLen++] = config.statusInterval >> 8;
            break;
        case CTRL_ID_LOCAL_MODE:      response[respLen++] = config.localMode; break;
        case CTRL_ID_CYCLE_TIME:
            response[respLen++] = config.cycleTime & 0xFF;
            response[respLen++] = config.cycleTime >> 8;
            break;
        case CTRL_ID_NUM_PIXELS:
            response[respLen++] = config.numPixels & 0xFF;
            response[respLen++] = config.numPixels >> 8;
            break;
        case CTRL_ID_ACTIVE_STRIP:    response[respLen++] = config.activeStrip; break;
        case CTRL_ID_SAVE_CONFIG:
        case CTRL_ID_REBOOT:          response[respLen++] = 0; break;
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

    if (count == 0) count = numPixels - start;
    if (start + count > numPixels) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_PIXEL_OVERFLOW);
        return;
    }

    uint16_t maxRespPixels = (MAX_PAYLOAD_SIZE - 5) / 3;
    if (count > maxRespPixels) count = maxRespPixels;

    // Use the protocol's payload buffer area for the response to avoid
    // dynamic allocation on tiny AVR
    uint8_t response[5];
    response[0] = 0; // Strip ID (active)
    response[1] = start & 0xFF;
    response[2] = start >> 8;
    response[3] = count & 0xFF;
    response[4] = count >> 8;

    // Build response: header + pixel data from shared buffer
    // We can send in one shot since pixel data is contiguous in pixelBuffer
    uint16_t totalLen = 5 + count * 3;

    // Assemble into a temp buffer (limited by MAX_PAYLOAD_SIZE)
    uint8_t* resp = new uint8_t[totalLen];
    if (!resp) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_BUFFER_OVERFLOW);
        return;
    }
    memcpy(resp, response, 5);
    memcpy(resp + 5, pixelBuffer + start * 3, count * 3);

    protocol.sendPacket(CMD_PIXEL_RESPONSE, resp, totalLen);
    delete[] resp;
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
            #if defined(__AVR__)
            asm volatile ("jmp 0");
            #endif
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
            protocol.sendAck(CMD_LOAD_CONFIG);
            break;

        case CMD_RESET_CONFIG:
            resetConfig();
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

    #if HEARTBEAT_ENABLED
    pinMode(HEARTBEAT_PIN, OUTPUT);
    digitalWrite(HEARTBEAT_PIN, LOW);
    #endif

    // Load saved configuration
    loadConfig();

    // Initialize both strip drivers (both always initialized,
    // only the active one gets data in showLeds())
    ws2812Init();
    apa102Init();

    // Clear and show
    clearLeds();
    showLeds();

    // Initialize inputs
    initInputs();

    // Initialize timers
    lastActivityTime = millis();
    stats.startTime = lastActivityTime;

    // Brief startup indicator - green flash on first few LEDs
    uint16_t flashCount = numPixels < 5 ? numPixels : 5;
    for (uint16_t i = 0; i < flashCount; i++) {
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
    #if HEARTBEAT_ENABLED
    uint32_t now = millis();
    if (now - lastHeartbeat >= HEARTBEAT_INTERVAL) {
        lastHeartbeat = now;
        heartbeatState = !heartbeatState;
        digitalWrite(HEARTBEAT_PIN, heartbeatState ? HIGH : LOW);
    }
    #endif

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
