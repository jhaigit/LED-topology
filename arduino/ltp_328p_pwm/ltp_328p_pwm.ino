/**
 * LTP 328P PWM - ATmega328P PWM Channel Controller
 *
 * Drives up to 6 monochrome LED channels via D4184 MOSFET solid-state
 * relays using hardware PWM. Each channel maps to one LTP "pixel."
 *
 * Hardware:
 *   - ATmega328P (Arduino Nano, old bootloader)
 *   - D4184 MOSFETs on PWM pins D3, D5, D6, D9, D10, D11
 *
 * The protocol presents channels as pixels with RGB color format for
 * wire compatibility. RGB values are converted to monochrome brightness
 * using max(R, G, B).
 *
 * PWM frequency is runtime-configurable across all 6 channels by
 * changing all three hardware timer prescalers and PWM mode. Each
 * prescaler has both a Fast PWM and Phase Correct option, giving
 * roughly 2x frequency steps within the D4184's operating range.
 *
 * Available frequencies (16 MHz, 8-bit resolution):
 *   122 Hz   (Phase Correct, /256)
 *   244 Hz   (Fast PWM, /256)
 *   490 Hz   (Phase Correct, /64)
 *   976 Hz   (Fast PWM, /64, default)
 *   3.9 kHz  (Phase Correct, /8)
 *   7.8 kHz  (Fast PWM, /8)
 *
 * Timer0 also drives millis()/delay(). A correctedMillis() wrapper
 * compensates for prescaler and mode changes via bit-shift.
 *
 * Features:
 *   - Full LTP protocol v2 support
 *   - 6 independently dimmable PWM channels
 *   - Runtime-configurable PWM frequency (6 presets)
 *   - Local display modes (breathe, chase, random, stagger)
 *   - EEPROM configuration persistence
 *   - Runtime-adjustable channel count
 */

#include <EEPROM.h>
#if defined(__AVR__)
#include <avr/wdt.h>
#include <avr/pgmspace.h>
#endif
#include "config.h"
#include <ltp_protocol.h>
#include "build_info.h"

// ============================================================================
// CHANNEL STATE
// ============================================================================

// Per-channel brightness (0-255), one byte each
static uint8_t channelValues[MAX_CHANNELS];

// ============================================================================
// PROTOCOL HANDLER
// ============================================================================

static uint8_t protocolBuffer[MAX_PAYLOAD_SIZE];
LtpProtocol protocol(Serial, protocolBuffer, MAX_PAYLOAD_SIZE);

// ============================================================================
// EEPROM CONFIGURATION
// ============================================================================

#define CONFIG_MAGIC        0x4C50  // "LP" - magic for PWM controller
#define CONFIG_VERSION      2       // v2: appended EEPROM-backed device name
#define EEPROM_CONFIG_ADDR  0

// Local display modes
#define LOCAL_MODE_BLANK    0
#define LOCAL_MODE_BREATHE  1
#define LOCAL_MODE_CHASE    2
#define LOCAL_MODE_RANDOM   3
#define LOCAL_MODE_STAGGER  4
#define LOCAL_MODE_CYCLE    255
#define LOCAL_MODE_COUNT    5

// Custom control IDs
#define CTRL_ID_NUM_CHANNELS  8
#define CTRL_ID_PWM_FREQ      9

// PWM frequency presets - each prescaler has a Phase Correct and Fast
// option, giving roughly 2x steps across the D4184's usable range.
#define PWM_FREQ_122HZ     0   // Phase Correct, /256
#define PWM_FREQ_244HZ     1   // Fast PWM, /256
#define PWM_FREQ_490HZ     2   // Phase Correct, /64
#define PWM_FREQ_976HZ     3   // Fast PWM, /64 (default)
#define PWM_FREQ_3922HZ    4   // Phase Correct, /8
#define PWM_FREQ_7812HZ    5   // Fast PWM, /8
#define NUM_FREQ_PRESETS    6

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
    uint8_t numChannels;        // Active channel count (1..MAX_CHANNELS)
    uint8_t pwmFreq;            // Frequency preset index (0-5)
    // v2: EEPROM-backed instance name. Appended last so an older blob
    // migrates without disturbing the offsets of any field above it.
    char name[DEVICE_NAME_MAXLEN + 1];
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
    DEFAULT_NUM_CHANNELS,
    PWM_FREQ_976HZ,             // default frequency
    DEVICE_NAME                 // name (factory default)
};

// Runtime channel count (mirrors config.numChannels)
uint8_t numChannels;

// ============================================================================
// TIMER / MILLIS CORRECTION
// ============================================================================

// Changing Timer0's prescaler and/or PWM mode from the Arduino default
// (Fast PWM, /64) makes millis() run at the wrong speed.
//
// Overflow period = prescaler * counts_per_cycle / 16 MHz
//   Fast PWM:      counts = 256  (0→255→overflow)
//   Phase Correct: counts = 510  (0→255→0, overflow at BOTTOM)
//
// The correction is close enough to a power-of-2 that bit-shifting
// gives <0.5% error (510 ≈ 512), which is negligible for LED timing.
//
//   Preset     | Actual ratio | Shift | Direction
//   122 Hz  PC /256 | ~8x slow  | <<3  | left
//   244 Hz   F /256 |  4x slow  | <<2  | left
//   490 Hz  PC /64  | ~2x slow  | <<1  | left
//   976 Hz   F /64  |  1x       |  0   | —
//   3922 Hz PC /8   | ~4x fast  | >>2  | right
//   7812 Hz  F /8   |  8x fast  | >>3  | right

static uint8_t millisShift = 0;
static bool millisShiftRight = true;

static uint32_t correctedMillis() {
    uint32_t raw = millis();
    if (millisShift == 0) return raw;
    if (millisShiftRight) return raw >> millisShift;
    return raw << millisShift;
}

static void correctedDelay(uint16_t ms) {
    uint32_t start = correctedMillis();
    while (correctedMillis() - start < ms) {
        // yield
    }
}

// Apply PWM frequency preset to all three hardware timers.
//
// Timer prescaler CS bit encodings differ between timers:
//   Timer0/1: 001=/1, 010=/8, 011=/64, 100=/256, 101=/1024
//   Timer2:   001=/1, 010=/8, 011=/32, 100=/64, 101=/128, 110=/256, 111=/1024
//
// Phase Correct PWM halves the frequency vs Fast PWM at the same prescaler,
// because the counter counts up then down (510 ticks vs 256).
static void applyPwmFrequency(uint8_t preset) {
    uint8_t cs01, cs2;
    bool phaseCorrect;

    switch (preset) {
        case PWM_FREQ_122HZ:
            cs01 = 0b100; cs2 = 0b110; // /256
            phaseCorrect = true;
            millisShift = 3; millisShiftRight = false;
            break;
        case PWM_FREQ_244HZ:
            cs01 = 0b100; cs2 = 0b110; // /256
            phaseCorrect = false;
            millisShift = 2; millisShiftRight = false;
            break;
        case PWM_FREQ_490HZ:
            cs01 = 0b011; cs2 = 0b100; // /64
            phaseCorrect = true;
            millisShift = 1; millisShiftRight = false;
            break;
        default:
        case PWM_FREQ_976HZ:
            cs01 = 0b011; cs2 = 0b100; // /64
            phaseCorrect = false;
            millisShift = 0; millisShiftRight = true;
            break;
        case PWM_FREQ_3922HZ:
            cs01 = 0b010; cs2 = 0b010; // /8
            phaseCorrect = true;
            millisShift = 2; millisShiftRight = true;
            break;
        case PWM_FREQ_7812HZ:
            cs01 = 0b010; cs2 = 0b010; // /8
            phaseCorrect = false;
            millisShift = 3; millisShiftRight = true;
            break;
    }

    if (phaseCorrect) {
        // Phase Correct PWM on all timers
        // Timer0: WGM01:WGM00 = 01
        TCCR0A = (TCCR0A & 0b11111100) | _BV(WGM00);
        TCCR0B = cs01;
        // Timer1: WGM13:12:11:10 = 0001 (Phase Correct 8-bit)
        TCCR1A = (TCCR1A & 0b11111100) | _BV(WGM10);
        TCCR1B = cs01;
        // Timer2: WGM22:21:20 = 001
        TCCR2A = (TCCR2A & 0b11111100) | _BV(WGM20);
        TCCR2B = cs2;
    } else {
        // Fast PWM on all timers
        // Timer0: WGM01:WGM00 = 11 (Arduino default mode)
        TCCR0A = (TCCR0A & 0b11111100) | _BV(WGM01) | _BV(WGM00);
        TCCR0B = cs01;
        // Timer1: WGM13:12:11:10 = 0101 (Fast PWM 8-bit)
        TCCR1A = (TCCR1A & 0b11111100) | _BV(WGM10);
        TCCR1B = _BV(WGM12) | cs01;
        // Timer2: WGM22:21:20 = 011
        TCCR2A = (TCCR2A & 0b11111100) | _BV(WGM21) | _BV(WGM20);
        TCCR2B = cs2;
    }
}

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
uint8_t modePhase = 0;

struct {
    uint32_t framesReceived = 0;
    uint32_t framesDisplayed = 0;
    uint32_t bytesReceived = 0;
    uint16_t checksumErrors = 0;
    uint16_t bufferOverflows = 0;
    uint32_t startTime = 0;
} stats;

// Heartbeat - use LED_BUILTIN (D13) which is not a PWM pin
#define HEARTBEAT_ENABLED   true
#define HEARTBEAT_PIN       LED_BUILTIN
#define HEARTBEAT_INTERVAL  500
uint32_t lastHeartbeat = 0;
bool heartbeatState = false;

// ============================================================================
// CONTROL DEFINITIONS
// ============================================================================

// Control name/description strings in PROGMEM to save RAM
static const char cn0[] PROGMEM = "brightness";   static const char cd0[] PROGMEM = "0-255";
static const char cn1[] PROGMEM = "gamma";         static const char cd1[] PROGMEM = "x10, 10=1.0";
static const char cn2[] PROGMEM = "idle_timeout";  static const char cd2[] PROGMEM = "secs, 0=off";
static const char cn3[] PROGMEM = "auto_show";     static const char cd3[] PROGMEM = "show after cmds";
static const char cn4[] PROGMEM = "frame_ack";     static const char cd4[] PROGMEM = "ack frames";
static const char cn5[] PROGMEM = "status_interval"; static const char cd5[] PROGMEM = "ms, 0=off";
static const char cn6[] PROGMEM = "local_mode";    static const char cd6[] PROGMEM = "idle display mode";
static const char cn7[] PROGMEM = "cycle_time";    static const char cd7[] PROGMEM = "mode cycle, secs";
static const char cn8[] PROGMEM = "num_channels";  static const char cd8[] PROGMEM = "active PWM channels";
static const char cn9[] PROGMEM = "pwm_freq";      static const char cd9[] PROGMEM = "PWM frequency";
static const char cn10[] PROGMEM = "save";          static const char cd10[] PROGMEM = "save to EEPROM";
static const char cn11[] PROGMEM = "reboot";        static const char cd11[] PROGMEM = "restart";

// Enum option labels (PROGMEM)
static const char el_off[]     PROGMEM = "Off";
static const char el_breathe[] PROGMEM = "Breathe";
static const char el_chase[]   PROGMEM = "Chase";
static const char el_random[]  PROGMEM = "Random";
static const char el_stagger[] PROGMEM = "Stagger";
static const char el_cycle[]   PROGMEM = "Cycle";

static const char el_122hz[]   PROGMEM = "122 Hz";
static const char el_244hz[]   PROGMEM = "244 Hz";
static const char el_490hz[]   PROGMEM = "490 Hz";
static const char el_976hz[]   PROGMEM = "976 Hz";
static const char el_3922hz[]  PROGMEM = "3.9 kHz";
static const char el_7812hz[]  PROGMEM = "7.8 kHz";

// Enum option definitions
struct EnumOption {
    uint8_t value;
    const char* label;  // PROGMEM pointer
};

static const EnumOption localModeOpts[] PROGMEM = {
    { 0, el_off }, { 1, el_breathe }, { 2, el_chase },
    { 3, el_random }, { 4, el_stagger },
    { 255, el_cycle }
};
#define LOCAL_MODE_NUM_OPTS 6

static const EnumOption pwmFreqOpts[] PROGMEM = {
    { 0, el_122hz }, { 1, el_244hz }, { 2, el_490hz },
    { 3, el_976hz }, { 4, el_3922hz }, { 5, el_7812hz }
};
#define PWM_FREQ_NUM_OPTS 6

struct ControlDef {
    uint8_t id;
    uint8_t type;
    uint8_t flags;
    int16_t minVal;
    int16_t maxVal;
    const char* name;        // PROGMEM pointer
    const char* description; // PROGMEM pointer
    uint8_t numEnumOpts;
    const EnumOption* enumOpts; // PROGMEM pointer, NULL for non-enum
};

static const ControlDef controlDefs[NUM_CONTROLS] PROGMEM = {
    { CTRL_ID_BRIGHTNESS,      CTRL_TYPE_UINT8,  CTRL_FLAG_HARDWARE, 0,     255,          cn0, cd0, 0, NULL },
    { CTRL_ID_GAMMA,           CTRL_TYPE_UINT8,  CTRL_FLAG_HARDWARE, 10,    30,           cn1, cd1, 0, NULL },
    { CTRL_ID_IDLE_TIMEOUT,    CTRL_TYPE_UINT16, CTRL_FLAG_HARDWARE, 0,     32767,        cn2, cd2, 0, NULL },
    { CTRL_ID_AUTO_SHOW,       CTRL_TYPE_BOOL,   CTRL_FLAG_HARDWARE | CTRL_FLAG_VOLATILE, 0, 1, cn3, cd3, 0, NULL },
    { CTRL_ID_FRAME_ACK,       CTRL_TYPE_BOOL,   CTRL_FLAG_HARDWARE | CTRL_FLAG_VOLATILE, 0, 1, cn4, cd4, 0, NULL },
    { CTRL_ID_STATUS_INTERVAL, CTRL_TYPE_UINT16, CTRL_FLAG_HARDWARE | CTRL_FLAG_VOLATILE, 0, 32767, cn5, cd5, 0, NULL },
    { CTRL_ID_LOCAL_MODE,      CTRL_TYPE_ENUM,   CTRL_FLAG_HARDWARE, 0,     255,          cn6, cd6, LOCAL_MODE_NUM_OPTS, localModeOpts },
    { CTRL_ID_CYCLE_TIME,      CTRL_TYPE_UINT16, CTRL_FLAG_HARDWARE, 1,     3600,         cn7, cd7, 0, NULL },
    { CTRL_ID_NUM_CHANNELS,    CTRL_TYPE_UINT8,  CTRL_FLAG_HARDWARE, 1,     MAX_CHANNELS, cn8, cd8, 0, NULL },
    { CTRL_ID_PWM_FREQ,        CTRL_TYPE_ENUM,   CTRL_FLAG_HARDWARE, 0,     5,            cn9, cd9, PWM_FREQ_NUM_OPTS, pwmFreqOpts },
    { CTRL_ID_SAVE_CONFIG,     CTRL_TYPE_ACTION, CTRL_FLAG_HARDWARE | CTRL_FLAG_ACTION, 0, 0, cn10, cd10, 0, NULL },
    { CTRL_ID_REBOOT,          CTRL_TYPE_ACTION, CTRL_FLAG_HARDWARE | CTRL_FLAG_ACTION, 0, 0, cn11, cd11, 0, NULL },
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
        case CTRL_ID_NUM_CHANNELS:    return config.numChannels;
        case CTRL_ID_PWM_FREQ:        return config.pwmFreq;
        case CTRL_ID_SAVE_CONFIG:
        case CTRL_ID_REBOOT:          return 0;
        default:                      return 0;
    }
}

// ============================================================================
// PWM OUTPUT
// ============================================================================

// Apply 8-bit brightness scaling
static uint8_t scale8(uint8_t value, uint8_t bright) {
    return ((uint16_t)value * (uint16_t)(bright + 1)) >> 8;
}

// Convert RGB to monochrome brightness
static uint8_t rgbToMono(uint8_t r, uint8_t g, uint8_t b) {
    uint8_t m = r;
    if (g > m) m = g;
    if (b > m) m = b;
    return m;
}

void setChannel(uint8_t idx, uint8_t r, uint8_t g, uint8_t b) {
    if (idx >= numChannels) return;
    channelValues[idx] = rgbToMono(r, g, b);
}

void getChannel(uint8_t idx, uint8_t& r, uint8_t& g, uint8_t& b) {
    if (idx >= numChannels) { r = g = b = 0; return; }
    r = g = b = channelValues[idx];
}

void clearChannels() {
    memset(channelValues, 0, numChannels);
}

void fillAll(uint8_t r, uint8_t g, uint8_t b) {
    uint8_t mono = rgbToMono(r, g, b);
    memset(channelValues, mono, numChannels);
}

void fillRange(uint16_t start, uint16_t end, uint8_t r, uint8_t g, uint8_t b) {
    if (start >= numChannels) return;
    if (end > numChannels) end = numChannels;
    uint8_t mono = rgbToMono(r, g, b);
    for (uint16_t i = start; i < end; i++) {
        channelValues[i] = mono;
    }
}

void showLeds() {
    for (uint8_t i = 0; i < numChannels; i++) {
        analogWrite(pwmPins[i], scale8(channelValues[i], config.brightness));
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
    } else if (stored.magic == CONFIG_MAGIC && stored.version == 1) {
        // Migrate v1 -> v2: name field appended; earlier fields keep their
        // offsets, so keep them and just default the (stale) name.
        config = stored;
        config.version = CONFIG_VERSION;
        strncpy(config.name, DEVICE_NAME, DEVICE_NAME_MAXLEN);
        config.name[DEVICE_NAME_MAXLEN] = '\0';
        saveConfig();
    }
    config.name[DEVICE_NAME_MAXLEN] = '\0';  // never let a stale EEPROM name run past bounds
    if (config.numChannels == 0 || config.numChannels > MAX_CHANNELS) {
        config.numChannels = DEFAULT_NUM_CHANNELS;
    }
    if (config.pwmFreq >= NUM_FREQ_PRESETS) {
        config.pwmFreq = PWM_FREQ_976HZ;
    }
    numChannels = config.numChannels;
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
    config.numChannels = DEFAULT_NUM_CHANNELS;
    config.pwmFreq = PWM_FREQ_976HZ;
    strncpy(config.name, DEVICE_NAME, DEVICE_NAME_MAXLEN);
    config.name[DEVICE_NAME_MAXLEN] = '\0';
    numChannels = config.numChannels;
    applyPwmFrequency(config.pwmFreq);
    saveConfig();
}

// CMD_SET_NAME: payload is the new instance name (raw UTF-8, no length
// prefix). Copied up to DEVICE_NAME_MAXLEN bytes, persisted immediately,
// and acked. An empty payload restores the factory default name.
void handleSetName(const uint8_t* payload, uint16_t length) {
    uint8_t n = 0;
    while (n < length && n < DEVICE_NAME_MAXLEN) {
        uint8_t c = payload[n];
        if (c == 0) break;
        if (c < 0x20) c = '_';
        config.name[n] = (char)c;
        n++;
    }
    config.name[n] = '\0';
    if (n == 0) {
        strncpy(config.name, DEVICE_NAME, DEVICE_NAME_MAXLEN);
        config.name[DEVICE_NAME_MAXLEN] = '\0';
    }
    saveConfig();
    protocol.sendAck(CMD_SET_NAME);
}

// ============================================================================
// IDLE TIMEOUT
// ============================================================================

void resetActivityTimer() {
    lastActivityTime = correctedMillis();
    if (isIdle) {
        isIdle = false;
        showLeds();
    }
}

void checkIdleTimeout() {
    if (config.idleTimeout == 0) return;

    uint32_t now = correctedMillis();
    uint32_t elapsed = (now - lastActivityTime) / 1000;

    if (!isIdle && elapsed >= config.idleTimeout) {
        isIdle = true;
        clearChannels();
        showLeds();
    }
}

// ============================================================================
// LOCAL DISPLAY MODES
// ============================================================================

void exitLocalMode() {
    if (localModeActive) {
        localModeActive = false;
        clearChannels();
    }
}

void startLocalMode(uint8_t mode) {
    config.localMode = mode;
    modePosition = 0;
    modePhase = 0;
    lastModeUpdate = correctedMillis();
    modeStartTime = correctedMillis();

    if (mode == LOCAL_MODE_BLANK) {
        localModeActive = false;
        clearChannels();
        showLeds();
    } else {
        localModeActive = true;
        if (mode == LOCAL_MODE_CYCLE) {
            currentDisplayMode = LOCAL_MODE_BREATHE;
        } else {
            currentDisplayMode = mode;
        }
    }
}

// Smooth breathing curve (0-255 in, 0-255 out)
// Quadratic ease-in / ease-out per half period.
static uint8_t breathCurve(uint8_t phase) {
    uint16_t p = phase;
    if (p < 128) {
        return (uint8_t)((p * p) >> 6);
    } else {
        p = 255 - p;
        return (uint8_t)((p * p) >> 6);
    }
}

void updateBreathe() {
    uint8_t val = breathCurve(modePhase);
    memset(channelValues, val, numChannels);
    modePhase += 2;
    showLeds();
}

void updateChase() {
    clearChannels();
    if (numChannels > 0) {
        channelValues[modePosition % numChannels] = 255;
        // Dimmer trail on previous channel
        uint8_t prev = (modePosition + numChannels - 1) % numChannels;
        if (channelValues[prev] == 0) channelValues[prev] = 64;
    }
    modePosition = (modePosition + 1) % numChannels;
    showLeds();
}

void updateRandom() {
    for (uint8_t i = 0; i < numChannels; i++) {
        if (channelValues[i] > 10) {
            channelValues[i] -= 10;
        } else {
            channelValues[i] = 0;
        }
    }
    uint8_t idx = random(numChannels);
    channelValues[idx] = random(128, 256);
    showLeds();
}

void updateStagger() {
    uint8_t phaseStep = 256 / numChannels;
    for (uint8_t i = 0; i < numChannels; i++) {
        uint8_t p = modePhase + (i * phaseStep);
        channelValues[i] = breathCurve(p);
    }
    modePhase += 2;
    showLeds();
}

void updateLocalMode() {
    if (!localModeActive) return;

    uint32_t now = correctedMillis();
    uint32_t interval;

    switch (currentDisplayMode) {
        case LOCAL_MODE_BREATHE: interval = 20; break;
        case LOCAL_MODE_CHASE:   interval = 200; break;
        case LOCAL_MODE_RANDOM:  interval = 50; break;
        case LOCAL_MODE_STAGGER: interval = 20; break;
        default: interval = 50; break;
    }

    if (now - lastModeUpdate < interval) return;
    lastModeUpdate = now;

    if (config.localMode == LOCAL_MODE_CYCLE) {
        if (now - modeStartTime > (uint32_t)config.cycleTime * 1000) {
            modeStartTime = now;
            currentDisplayMode++;
            if (currentDisplayMode >= LOCAL_MODE_COUNT) {
                currentDisplayMode = LOCAL_MODE_BREATHE;
            }
            modePosition = 0;
            modePhase = 0;
            clearChannels();
        }
    }

    switch (currentDisplayMode) {
        case LOCAL_MODE_BREATHE: updateBreathe(); break;
        case LOCAL_MODE_CHASE:   updateChase(); break;
        case LOCAL_MODE_RANDOM:  updateRandom(); break;
        case LOCAL_MODE_STAGGER: updateStagger(); break;
        default: break;
    }
}

// ============================================================================
// PROTOCOL HANDLERS
// ============================================================================

void sendHello() {
    uint8_t payload[10];
    payload[0] = LTP_PROTOCOL_MAJOR;
    payload[1] = LTP_PROTOCOL_MINOR;
    payload[2] = (FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR;
    payload[3] = 0;
    payload[4] = 1; // 1 strip (the PWM bank)
    payload[5] = numChannels & 0xFF;
    payload[6] = numChannels >> 8;
    payload[7] = COLOR_RGB;
    payload[8] = CAPS_BRIGHTNESS | CAPS_EXTENDED;
    payload[9] = CAPS_PIXEL_READBACK | CAPS_EEPROM;

    protocol.sendPacket(CMD_HELLO, payload, 10);
}

// Streaming packet send - writes directly to Serial byte by byte
static uint8_t streamChecksum;

static void streamBegin(uint8_t cmd, uint16_t length) {
    streamChecksum = 0;
    Serial.write(LTP_START_BYTE);
    uint8_t txFlags = FLAG_RESPONSE;
    Serial.write(txFlags);
    streamChecksum ^= txFlags;
    Serial.write((uint8_t)(length & 0xFF));
    streamChecksum ^= (uint8_t)(length & 0xFF);
    Serial.write((uint8_t)(length >> 8));
    streamChecksum ^= (uint8_t)(length >> 8);
    Serial.write(cmd);
    streamChecksum ^= cmd;
}

static void streamByte(uint8_t b) {
    Serial.write(b);
    streamChecksum ^= b;
}

static void streamEnd() {
    Serial.write(streamChecksum);
}

// Send INFO_CONTROLS response by streaming directly to Serial.
static void sendInfoControls() {
    // Pass 1: compute total payload length
    uint16_t totalLen = 1; // NUM_CONTROLS byte
    for (uint8_t i = 0; i < NUM_CONTROLS; i++) {
        totalLen += 7; // id + type + flags + min(2) + max(2)
        uint8_t valueSize;
        uint8_t id = pgm_read_byte(&controlDefs[i].id);
        uint8_t type = pgm_read_byte(&controlDefs[i].type);
        getControlValue(id, &valueSize);
        totalLen += valueSize;
        const char* name = (const char*)pgm_read_ptr(&controlDefs[i].name);
        totalLen += strlen_P(name) + 1;
        const char* desc = (const char*)pgm_read_ptr(&controlDefs[i].description);
        totalLen += strlen_P(desc) + 1;
        if (type == CTRL_TYPE_ENUM) {
            uint8_t numOpts = pgm_read_byte(&controlDefs[i].numEnumOpts);
            const EnumOption* opts = (const EnumOption*)pgm_read_ptr(&controlDefs[i].enumOpts);
            totalLen += 1;
            for (uint8_t j = 0; j < numOpts; j++) {
                totalLen += 1;
                const char* label = (const char*)pgm_read_ptr(&opts[j].label);
                totalLen += strlen_P(label) + 1;
            }
        }
    }

    // Pass 2: stream the packet
    streamBegin(CMD_INFO_RESPONSE, totalLen);
    streamByte(NUM_CONTROLS);

    for (uint8_t i = 0; i < NUM_CONTROLS; i++) {
        uint8_t id = pgm_read_byte(&controlDefs[i].id);
        uint8_t type = pgm_read_byte(&controlDefs[i].type);
        uint8_t ctrlFlags = pgm_read_byte(&controlDefs[i].flags);
        int16_t minVal = (int16_t)pgm_read_word(&controlDefs[i].minVal);
        int16_t maxVal = (int16_t)pgm_read_word(&controlDefs[i].maxVal);

        streamByte(id);
        streamByte(type);
        streamByte(ctrlFlags);
        streamByte(minVal & 0xFF);
        streamByte((minVal >> 8) & 0xFF);
        streamByte(maxVal & 0xFF);
        streamByte((maxVal >> 8) & 0xFF);

        uint8_t valueSize;
        uint16_t value = getControlValue(id, &valueSize);
        streamByte(value & 0xFF);
        if (valueSize > 1) {
            streamByte((value >> 8) & 0xFF);
        }

        const char* name = (const char*)pgm_read_ptr(&controlDefs[i].name);
        char c;
        while ((c = pgm_read_byte(name++)) != 0) {
            streamByte(c);
        }
        streamByte(0);

        const char* desc = (const char*)pgm_read_ptr(&controlDefs[i].description);
        while ((c = pgm_read_byte(desc++)) != 0) {
            streamByte(c);
        }
        streamByte(0);

        if (type == CTRL_TYPE_ENUM) {
            uint8_t numOpts = pgm_read_byte(&controlDefs[i].numEnumOpts);
            const EnumOption* opts = (const EnumOption*)pgm_read_ptr(&controlDefs[i].enumOpts);
            streamByte(numOpts);
            for (uint8_t j = 0; j < numOpts; j++) {
                streamByte(pgm_read_byte(&opts[j].value));
                const char* label = (const char*)pgm_read_ptr(&opts[j].label);
                while ((c = pgm_read_byte(label++)) != 0) {
                    streamByte(c);
                }
                streamByte(0);
            }
        }
    }

    streamEnd();
}

void handleGetInfo(const uint8_t* payload, uint16_t length) {
    if (length < 1) {
        protocol.sendNak(CMD_GET_INFO, ERR_INVALID_LENGTH);
        return;
    }

    uint8_t infoType = payload[0];
    uint8_t response[48];
    uint16_t respLen = 0;

    switch (infoType) {
        case INFO_ALL:
            response[respLen++] = LTP_PROTOCOL_MAJOR;
            response[respLen++] = LTP_PROTOCOL_MINOR;
            response[respLen++] = (FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR;
            response[respLen++] = 0;
            response[respLen++] = 1; // 1 strip (PWM bank)
            response[respLen++] = numChannels & 0xFF;
            response[respLen++] = numChannels >> 8;
            response[respLen++] = COLOR_RGB;
            response[respLen++] = CAPS_BRIGHTNESS | CAPS_EXTENDED;
            response[respLen++] = CAPS_PIXEL_READBACK | CAPS_EEPROM;
            response[respLen++] = NUM_CONTROLS;
            {
                const char* name = config.name;
                uint8_t i = 0;
                while (name[i] && i < DEVICE_NAME_MAXLEN) {
                    response[respLen++] = name[i++];
                }
                response[respLen++] = 0;
            }
            // Matrix dimensions (not applicable - linear)
            response[respLen++] = 1;    // width_lo (1 = linear)
            response[respLen++] = 0;    // width_hi
            response[respLen++] = 0;    // height_lo
            response[respLen++] = 0;    // height_hi
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
            response[respLen++] = 1; // 1 strip
            response[respLen++] = 0; // strip ID 0
            response[respLen++] = numChannels & 0xFF;
            response[respLen++] = numChannels >> 8;
            response[respLen++] = COLOR_RGB;
            response[respLen++] = LED_TYPE_PWM;
            response[respLen++] = 0; // data pin N/A (multiple)
            response[respLen++] = 0; // clock pin N/A
            response[respLen++] = 0x01; // active
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
                uint32_t uptime = (correctedMillis() - stats.startTime) / 1000;
                response[respLen++] = uptime & 0xFF;
                response[respLen++] = (uptime >> 8) & 0xFF;
                response[respLen++] = (uptime >> 16) & 0xFF;
                response[respLen++] = (uptime >> 24) & 0xFF;
            }
            break;

        case INFO_INPUTS:
            response[respLen++] = 0; // no inputs
            break;

        case INFO_CONTROLS:
            sendInfoControls();
            return;

        default:
            protocol.sendNak(CMD_GET_INFO, ERR_INVALID_PARAM);
            return;
    }

    protocol.sendPacket(CMD_INFO_RESPONSE, response, respLen);
}

void handleShow(const uint8_t* payload, uint16_t length) {
    resetActivityTimer();
    showLeds();
    stats.framesDisplayed++;

    if (config.frameAck && length >= 2) {
        uint8_t response[4];
        response[0] = payload[0];
        response[1] = payload[1];
        uint16_t timestamp = correctedMillis() & 0xFFFF;
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

    if (end > numChannels) {
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
    uint16_t expectedBytes = count * 3; // RGB on the wire

    if (length < dataOffset + expectedBytes) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_INVALID_LENGTH);
        return;
    }

    if (start + count > numChannels) {
        protocol.sendNak(CMD_PIXEL_FRAME, ERR_PIXEL_OVERFLOW);
        return;
    }

    exitLocalMode();

    const uint8_t* pixelData = payload + dataOffset;
    for (uint16_t i = 0; i < count; i++) {
        uint16_t offset = i * 3;
        setChannel(start + i, pixelData[offset], pixelData[offset + 1], pixelData[offset + 2]);
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

        case CTRL_ID_NUM_CHANNELS:
            if (payload[1] >= 1 && payload[1] <= MAX_CHANNELS) {
                // Turn off old channels beyond new count
                for (uint8_t i = payload[1]; i < numChannels; i++) {
                    analogWrite(pwmPins[i], 0);
                }
                config.numChannels = payload[1];
                numChannels = payload[1];
                clearChannels();
            } else {
                protocol.sendNak(CMD_SET_CONTROL, ERR_INVALID_PARAM);
                return;
            }
            break;

        case CTRL_ID_PWM_FREQ:
            if (payload[1] < NUM_FREQ_PRESETS) {
                config.pwmFreq = payload[1];
                applyPwmFrequency(payload[1]);
                // Re-anchor all timing to avoid jumps from millis correction change
                lastActivityTime = correctedMillis();
                lastHeartbeat = correctedMillis();
                if (localModeActive) {
                    lastModeUpdate = correctedMillis();
                    modeStartTime = correctedMillis();
                }
                stats.startTime = correctedMillis();
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
            correctedDelay(100);
            #if defined(__AVR__)
            asm volatile ("jmp 0");
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
        case CTRL_ID_NUM_CHANNELS:    response[respLen++] = config.numChannels; break;
        case CTRL_ID_PWM_FREQ:        response[respLen++] = config.pwmFreq; break;
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

    if (count == 0) count = numChannels - start;
    if (start + count > numChannels) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_PIXEL_OVERFLOW);
        return;
    }

    // Response: 5 byte header + count * 3 bytes (RGB, all equal for mono)
    uint16_t totalLen = 5 + count * 3;

    streamBegin(CMD_PIXEL_RESPONSE, totalLen);
    streamByte(0); // strip ID
    streamByte(start & 0xFF);
    streamByte(start >> 8);
    streamByte(count & 0xFF);
    streamByte(count >> 8);
    for (uint16_t i = 0; i < count; i++) {
        uint8_t val = channelValues[start + i];
        streamByte(val); // R
        streamByte(val); // G
        streamByte(val); // B
    }
    streamEnd();
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
            correctedDelay(10);
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
            applyPwmFrequency(config.pwmFreq);
            protocol.sendAck(CMD_LOAD_CONFIG);
            break;

        case CMD_RESET_CONFIG:
            resetConfig();
            protocol.sendAck(CMD_RESET_CONFIG);
            break;

        case CMD_SET_NAME:
            handleSetName(pkt.payload, pkt.length);
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

    // Apply saved PWM frequency (sets timer prescalers, mode, millis correction)
    applyPwmFrequency(config.pwmFreq);

    // Initialize all PWM pins as outputs, off
    for (uint8_t i = 0; i < MAX_CHANNELS; i++) {
        pinMode(pwmPins[i], OUTPUT);
        analogWrite(pwmPins[i], 0);
    }

    clearChannels();

    // Initialize timers
    lastActivityTime = correctedMillis();
    stats.startTime = lastActivityTime;

    // Brief startup indicator - ramp up first channel and back down
    for (uint8_t v = 0; v < 64; v += 4) {
        analogWrite(pwmPins[0], v);
        correctedDelay(5);
    }
    for (uint8_t v = 64; v > 0; v -= 4) {
        analogWrite(pwmPins[0], v);
        correctedDelay(5);
    }
    analogWrite(pwmPins[0], 0);

    // Start local mode if configured
    if (config.localMode != LOCAL_MODE_BLANK) {
        startLocalMode(config.localMode);
    }

    correctedDelay(100);
    sendHello();
}

void loop() {
    // Heartbeat LED
    #if HEARTBEAT_ENABLED
    uint32_t now = correctedMillis();
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

    // Check idle timeout
    checkIdleTimeout();

    // Update local display mode
    updateLocalMode();
}
