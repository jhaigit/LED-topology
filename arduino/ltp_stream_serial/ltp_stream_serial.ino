/**
 * LTP Stream (serial) - pattern-only LED controller for Arduino and Teensy 3.2
 *
 * Speaks LTP serial protocol v2 (spec/serial-protocol-v2.md) but accepts no
 * pixel data: the device shows its own patterns, chosen and tuned through
 * controls. Every pixel is calculated while the frame goes out, so there is
 * no frame buffer and the pixel count is not limited by RAM.
 *
 *   - Patterns and their maths:   libraries/LtpStreamFx/src/ltp_fx.cpp
 *   - Controls and settings:      libraries/LtpStreamFx/src/ltp_fx_controls.h
 *   - Pins, limits, timing:       config.h
 *   - LED output and host link:   platform_avr.h, platform_teensy.h
 *
 * Pixel commands (PIXEL_*, SHOW) are read and dropped. GET_PIXELS works: the
 * pattern is a function of position and time, so any pixel of the current
 * frame can be recalculated on request.
 */

#include <EEPROM.h>
#include "config.h"
#include <ltp_protocol.h>
#include <ltp_fx.h>

#define FXC_MAX_PIXELS    MAX_PIXELS
#define FXC_DEFAULT_NAME  DEVICE_NAME
#define FXC_HAS_APA102    1
#define FXC_HAS_LPD8806   HAS_LPD8806
#include <ltp_fx_controls.h>

#include "build_info.h"

#if defined(BOARD_AVR)
#include "platform_avr.h"
#else
#include "platform_teensy.h"
#endif

// The control table repeats these protocol numbers so that the ESP32 sketch
// can use it without the serial library; make sure they still agree.
static_assert(FXC_TYPE_BOOL == CTRL_TYPE_BOOL && FXC_TYPE_UINT8 == CTRL_TYPE_UINT8 &&
              FXC_TYPE_UINT16 == CTRL_TYPE_UINT16 && FXC_TYPE_ACTION == CTRL_TYPE_ACTION &&
              FXC_TYPE_ENUM == CTRL_TYPE_ENUM, "control types differ from ltp_protocol.h");
static_assert(FXC_FLAG_HARDWARE == CTRL_FLAG_HARDWARE && FXC_FLAG_READONLY == CTRL_FLAG_READONLY &&
              FXC_FLAG_VOLATILE == CTRL_FLAG_VOLATILE && FXC_FLAG_ACTION == CTRL_FLAG_ACTION,
              "control flags differ from ltp_protocol.h");
static_assert(FX_LAYOUT_SERPENTINE == SUBMATRIX_SERPENTINE &&
              FX_LAYOUT_VERTICAL_FIRST == SUBMATRIX_VERTICAL_FIRST &&
              FX_LAYOUT_ORIGIN_BOTTOM == SUBMATRIX_ORIGIN_BOTTOM,
              "matrix layout bits differ from ltp_protocol.h");
static_assert(FXC_ID_BRIGHTNESS == CTRL_ID_BRIGHTNESS && FXC_ID_LOCAL_MODE == CTRL_ID_LOCAL_MODE &&
              FXC_ID_CYCLE_TIME == CTRL_ID_CYCLE_TIME && FXC_ID_SAVE == CTRL_ID_SAVE_CONFIG &&
              FXC_ID_REBOOT == CTRL_ID_REBOOT, "control ids differ from ltp_protocol.h");

// ============================================================================
// STATE
// ============================================================================

static uint8_t protocolBuffer[MAX_PAYLOAD_SIZE];
LtpProtocol protocol(HOST, protocolBuffer, MAX_PAYLOAD_SIZE);

static struct {
    uint32_t framesDisplayed;
    uint32_t pixelPacketsIgnored;  // pixel commands and SHOW, read and dropped
    uint16_t framesCutShort;       // frames the pattern could not finish in time
    uint16_t hostOverflows;        // times host bytes were lost
    uint32_t uptimeMs;
} stats;

static uint16_t sinceFrameMs;      // time since the last frame started
static uint16_t sinceRefreshMs;    // time since a static frame was last sent
static uint16_t fpsWindowMs;       // frame-rate measurement window so far
static uint16_t fpsWindowFrames;
static bool frameDirty = true;     // a setting changed since the last frame

#define FPS_WINDOW_MS 1000

// After host bytes were lost, input is ignored until the line has been quiet
// this long, so the middle of a broken packet is not searched for commands.
#define HOST_QUIET_MS 20
static bool hostDeaf;
static uint16_t hostQuietMs;

#ifdef HEARTBEAT_PIN
static uint16_t heartbeatMs;
#endif

// ============================================================================
// SETTINGS STORAGE
// ============================================================================

static void saveConfig() {
    fxConfig.magic = FXC_MAGIC;
    fxConfig.version = FXC_VERSION;
    EEPROM.put(EEPROM_CONFIG_ADDR, fxConfig);
}

static void loadConfig() {
    FxConfig stored;
    EEPROM.get(EEPROM_CONFIG_ADDR, stored);
    if (fxcStoredValid(stored)) {
        fxConfig = stored;
    } else {
        fxcDefaults();
    }
    fxcSanitize();
    fxcRestartCycle();
    frameDirty = true;
}

static void resetConfig() {
    uint16_t oldPixels = fxConfig.numPixels;
    uint8_t oldLedType = fxConfig.ledType;
    fxcDefaults();
    fxcSanitize();
    fxcRestartCycle();
    fxcRequestBlank(oldPixels, oldLedType);
    saveConfig();
    frameDirty = true;
}

// ============================================================================
// FRAMES
// ============================================================================

// An effect whose frame is the same every time needs sending only when a
// setting changes.
static bool isStaticEffect(uint8_t effect) {
    return effect == FX_OFF || effect == FX_SOLID;
}

static void renderFrame() {
    uint8_t order[3];

    // LEDs left lit on the output we just switched away from.
    if (fxRuntime.blankTo && fxRuntime.blankLedType != fxConfig.ledType) {
        fxcWireOrder(fxRuntime.blankLedType, order);
        platformShow(fxRuntime.blankLedType, 0, fxRuntime.blankTo, order);
        fxRuntime.blankTo = 0;
    }

    // If the strip got shorter, send black to the pixels dropped off the end.
    uint16_t total = fxConfig.numPixels;
    if (fxRuntime.blankTo > total) total = fxRuntime.blankTo;

    fxcBeginFrame();
    fxStart();
    fxcWireOrder(fxConfig.ledType, order);
    if (platformShow(fxConfig.ledType, fxConfig.numPixels, total, order)) {
        fxRuntime.blankTo = 0;
        frameDirty = false;
    } else {
        stats.framesCutShort++;
    }
    stats.framesDisplayed++;
    fpsWindowFrames++;
}

static void updateFrames(uint16_t elapsedMs) {
    sinceFrameMs += elapsedMs;
    sinceRefreshMs += elapsedMs;

    fpsWindowMs += elapsedMs;
    if (fpsWindowMs >= FPS_WINDOW_MS) {
        fxRuntime.fps = (uint16_t)(((uint32_t)fpsWindowFrames * 1000) / fpsWindowMs);
        fpsWindowMs = 0;
        fpsWindowFrames = 0;
    }

    uint16_t interval = 1000 / (fxConfig.maxFps ? fxConfig.maxFps : 1);
    if (sinceFrameMs < interval) return;

    if (isStaticEffect(fxcCurrentEffect()) && !frameDirty && sinceRefreshMs < STATIC_REFRESH_MS) {
        return;
    }
    sinceFrameMs = 0;
    sinceRefreshMs = 0;
    renderFrame();
}

// ============================================================================
// PROTOCOL: REPLIES
// ============================================================================

// Streaming packet send: bytes go straight to the host with a running
// checksum, so no reply needs a buffer of its own size.
static uint8_t streamChecksum;

static void streamByte(uint8_t b) {
    HOST.write(b);
    streamChecksum ^= b;
}

static void streamBegin(uint8_t cmd, uint16_t length) {
    HOST.write((uint8_t)LTP_START_BYTE);
    streamChecksum = 0;
    streamByte(FLAG_RESPONSE);
    streamByte((uint8_t)(length & 0xFF));
    streamByte((uint8_t)(length >> 8));
    streamByte(cmd);
}

static void streamEnd() {
    HOST.write(streamChecksum);
}

static void streamWord(uint16_t value) {
    streamByte((uint8_t)(value & 0xFF));
    streamByte((uint8_t)(value >> 8));
}

static void streamLong(uint32_t value) {
    streamWord((uint16_t)value);
    streamWord((uint16_t)(value >> 16));
}

// A flash string and its terminating NUL.
static void streamText_P(const char* text) {
    char c;
    do {
        c = (char)pgm_read_byte(text++);
        streamByte((uint8_t)c);
    } while (c);
}

// A RAM string, at most `maxLen` characters, and a terminating NUL.
static void streamText(const char* text, uint8_t maxLen) {
    for (uint8_t i = 0; i < maxLen && text[i]; i++) streamByte((uint8_t)text[i]);
    streamByte(0);
}

static uint8_t textLength(const char* text, uint8_t maxLen) {
    uint8_t n = 0;
    while (n < maxLen && text[n]) n++;
    return n;
}

#define CAPS_BYTE_1 (CAPS_BRIGHTNESS | CAPS_EXTENDED)
#define CAPS_BYTE_2 (CAPS_PIXEL_READBACK | CAPS_EEPROM | CAPS_NO_PIXEL_INPUT)

// The eleven bytes HELLO and INFO_ALL share.
static void streamIdentity() {
    streamByte(LTP_PROTOCOL_MAJOR);
    streamByte(LTP_PROTOCOL_MINOR);
    streamByte((FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR);
    streamByte(0);
    streamByte(1);  // one strip
    streamWord(fxConfig.numPixels);
    streamByte(COLOR_RGB);
    streamByte(CAPS_BYTE_1);
    streamByte(CAPS_BYTE_2);
    streamByte(FXC_NUM_CONTROLS);
}

#define IDENTITY_LENGTH 11

static void sendHello() {
    streamBegin(CMD_HELLO, IDENTITY_LENGTH);
    streamIdentity();
    streamEnd();
}

static void sendInfoAll() {
    uint8_t nameLen = textLength(fxConfig.name, FXC_NAME_MAXLEN);
    streamBegin(CMD_INFO_RESPONSE, IDENTITY_LENGTH + nameLen + 1 + 4);
    streamIdentity();
    streamText(fxConfig.name, FXC_NAME_MAXLEN);
    // Matrix size; a height of 0 tells the host this is a strip.
    uint16_t width = fxConfig.matrixWidth;
    streamWord(width);
    streamWord(width > 1 ? fxConfig.numPixels / width : 0);
    streamEnd();
}

static void sendInfoBuild() {
    const uint8_t maxLen = 15;
    uint8_t total = textLength(FIRMWARE_NAME, maxLen) + textLength(GIT_COMMIT, maxLen)
                  + textLength(BUILD_DATE, maxLen) + 3;
    streamBegin(CMD_INFO_RESPONSE, total);
    streamText(FIRMWARE_NAME, maxLen);
    streamText(GIT_COMMIT, maxLen);
    streamText(BUILD_DATE, maxLen);
    streamEnd();
}

static void sendInfoStrips() {
    streamBegin(CMD_INFO_RESPONSE, 9);
    streamByte(1);  // strip count
    streamByte(0);  // strip id
    streamWord(fxConfig.numPixels);
    streamByte(COLOR_RGB);
    streamByte(platformLedTypeCode(fxConfig.ledType));
    streamByte(platformDataPin(fxConfig.ledType));
    streamByte(platformClockPin(fxConfig.ledType));
    streamByte(0x01);  // active
    streamEnd();
}

static void sendInfoStatus() {
    streamBegin(CMD_INFO_RESPONSE, 7);
    streamByte(1);  // running
    streamByte(fxConfig.brightness);
    streamWord(0x7FFF);  // temperature: not measured
    streamWord(0xFFFF);  // voltage: not measured
    streamByte(0);
    streamEnd();
}

static void sendInfoStats() {
    streamBegin(CMD_INFO_RESPONSE, 20);
    streamLong(stats.pixelPacketsIgnored);  // "frames received": none are used
    streamLong(stats.framesDisplayed);
    streamLong(0);                          // pixel bytes received
    streamWord(0);                          // checksum errors (not counted by the parser)
    streamWord(stats.framesCutShort + stats.hostOverflows);
    streamLong(stats.uptimeMs / 1000);
    streamEnd();
}

// Walk the control list, adding up its encoded length and, if `send`,
// streaming it. Two walks - measure, then send - mean the reply (about 1 KB)
// never sits in RAM.
static uint16_t walkControls(bool send) {
    uint16_t length = 1;
    if (send) streamByte(FXC_NUM_CONTROLS);
    for (uint8_t i = 0; i < FXC_NUM_CONTROLS; i++) {
        FxControlDef def;
        fxcReadDef(i, def);
        uint8_t size = fxcValueSize(def.type);
        length += 7 + size + strlen_P(def.name) + 1 + strlen_P(def.description) + 1;
        if (send) {
            streamByte(def.id);
            streamByte(def.type);
            streamByte(def.flags);
            streamWord((uint16_t)def.minVal);
            streamWord((uint16_t)def.maxVal);
            uint16_t value = fxcGetDef(def);
            streamByte((uint8_t)(value & 0xFF));
            if (size > 1) streamByte((uint8_t)(value >> 8));
            streamText_P(def.name);
            streamText_P(def.description);
        }
        if (def.type != FXC_TYPE_ENUM) continue;
        length += 1;
        if (send) streamByte(def.numOptions);
        for (uint8_t j = 0; j < def.numOptions; j++) {
            FxEnumOption option;
            fxcReadOption(def, j, option);
            length += 1 + strlen_P(option.label) + 1;
            if (send) {
                streamByte(option.value);
                streamText_P(option.label);
            }
        }
    }
    return length;
}

static void sendInfoControls() {
    streamBegin(CMD_INFO_RESPONSE, walkControls(false));
    walkControls(true);
    streamEnd();
}

// ============================================================================
// PROTOCOL: COMMANDS
// ============================================================================

static void handleGetInfo(const LtpPacket& pkt) {
    if (pkt.length < 1) {
        protocol.sendNak(CMD_GET_INFO, ERR_INVALID_LENGTH);
        return;
    }
    switch (pkt.payload[0]) {
        case INFO_ALL:      sendInfoAll(); break;
        case INFO_VERSION:
            streamBegin(CMD_INFO_RESPONSE, 4);
            streamByte(LTP_PROTOCOL_MAJOR);
            streamByte(LTP_PROTOCOL_MINOR);
            streamByte((FIRMWARE_VERSION_MAJOR << 4) | FIRMWARE_VERSION_MINOR);
            streamByte(0);
            streamEnd();
            break;
        case INFO_STRIPS:   sendInfoStrips(); break;
        case INFO_STATUS:   sendInfoStatus(); break;
        case INFO_CONTROLS: sendInfoControls(); break;
        case INFO_STATS:    sendInfoStats(); break;
        case INFO_INPUTS:
            streamBegin(CMD_INFO_RESPONSE, 1);
            streamByte(0);  // no inputs
            streamEnd();
            break;
        case INFO_BUILD:    sendInfoBuild(); break;
        default:
            protocol.sendNak(CMD_GET_INFO, ERR_INVALID_PARAM);
            break;
    }
}

static void handleGetControl(const LtpPacket& pkt) {
    if (pkt.length < 1) {
        protocol.sendNak(CMD_GET_CONTROL, ERR_INVALID_LENGTH);
        return;
    }
    uint8_t id = pkt.payload[0];
    int8_t index = fxcFind(id);
    if (index < 0) {
        protocol.sendNak(CMD_GET_CONTROL, ERR_INVALID_PARAM);
        return;
    }
    FxControlDef def;
    fxcReadDef((uint8_t)index, def);
    uint8_t size = fxcValueSize(def.type);
    uint16_t value = fxcGetDef(def);
    streamBegin(CMD_CONTROL_RESPONSE, 1 + size);
    streamByte(id);
    streamByte((uint8_t)(value & 0xFF));
    if (size > 1) streamByte((uint8_t)(value >> 8));
    streamEnd();
}

static void handleSetControl(const LtpPacket& pkt) {
    if (pkt.length < 2) {
        protocol.sendNak(CMD_SET_CONTROL, ERR_INVALID_LENGTH);
        return;
    }
    uint8_t id = pkt.payload[0];
    uint16_t value = pkt.payload[1];
    int8_t index = fxcFind(id);
    if (index >= 0 && pgm_read_byte(&fxControlDefs[index].type) == FXC_TYPE_UINT16) {
        if (pkt.length < 3) {
            protocol.sendNak(CMD_SET_CONTROL, ERR_INVALID_LENGTH);
            return;
        }
        value |= (uint16_t)pkt.payload[2] << 8;
    }

    switch (fxcSet(id, value)) {
        case FXC_OK:
            break;
        case FXC_DO_SAVE:
            saveConfig();
            break;
        case FXC_DO_REBOOT:
            protocol.sendAck(CMD_SET_CONTROL);
            platformReboot();
            return;
        default:
            protocol.sendNak(CMD_SET_CONTROL, ERR_INVALID_PARAM);
            return;
    }
    if (id == FXC_ID_LOCAL_MODE) platformPatternChanged();
    frameDirty = true;
    protocol.sendAck(CMD_SET_CONTROL);
}

// Pixel read-back: recalculate the requested pixels of the frame on show.
static void handleGetPixels(const LtpPacket& pkt) {
    if (pkt.length < 5) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_INVALID_LENGTH);
        return;
    }
    uint16_t start = pkt.payload[1] | ((uint16_t)pkt.payload[2] << 8);
    uint16_t count = pkt.payload[3] | ((uint16_t)pkt.payload[4] << 8);
    if (start >= fxConfig.numPixels) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_PIXEL_OVERFLOW);
        return;
    }
    if (count == 0) count = fxConfig.numPixels - start;
    if (count > fxConfig.numPixels - start) {
        protocol.sendNak(CMD_GET_PIXELS, ERR_PIXEL_OVERFLOW);
        return;
    }
    if (count > MAX_READBACK_PIXELS) count = MAX_READBACK_PIXELS;

    fxcBeginFrame();
    streamBegin(CMD_PIXEL_RESPONSE, 5 + count * 3);
    streamByte(0);  // strip id
    streamWord(start);
    streamWord(count);
    for (uint16_t i = 0; i < count; i++) {
        uint8_t rgb[3];
        fxAt(start + i, rgb);
        streamByte(rgb[0]);
        streamByte(rgb[1]);
        streamByte(rgb[2]);
    }
    streamEnd();
}

static void handleSetName(const LtpPacket& pkt) {
    fxcSetName(pkt.payload, pkt.length);
    saveConfig();
    protocol.sendAck(CMD_SET_NAME);
}

static bool isPixelCommand(uint8_t cmd) {
    return cmd == CMD_SHOW || (cmd >= CMD_PIXEL_SET_ALL && cmd <= 0x3F);
}

static void processPacket(const LtpPacket& pkt) {
    // Pixel data is not used. Stay silent, as a sink does on success - the
    // host does not wait for a reply to frames - unless it asked for one.
    if (isPixelCommand(pkt.cmd)) {
        stats.pixelPacketsIgnored++;
        if (pkt.flags & FLAG_ACK_REQ) protocol.sendNak(pkt.cmd, ERR_NOT_SUPPORTED);
        return;
    }
    // Anything else too long for the buffer was read without being stored.
    if (protocol.isOversize()) {
        protocol.sendNak(pkt.cmd, ERR_INVALID_LENGTH);
        return;
    }

    switch (pkt.cmd) {
        case CMD_NOP:
            if (pkt.flags & FLAG_ACK_REQ) protocol.sendAck(CMD_NOP);
            break;
        case CMD_RESET:
            protocol.sendAck(CMD_RESET);
            platformReboot();
            break;
        case CMD_HELLO:        sendHello(); break;
        case CMD_GET_INFO:     handleGetInfo(pkt); break;
        case CMD_GET_PIXELS:   handleGetPixels(pkt); break;
        case CMD_GET_CONTROL:  handleGetControl(pkt); break;
        case CMD_SET_CONTROL:  handleSetControl(pkt); break;
        case CMD_SET_NAME:     handleSetName(pkt); break;
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
        case CMD_GET_INPUT:
            protocol.sendNak(CMD_GET_INPUT, ERR_NOT_SUPPORTED);
            break;
        default:
            protocol.sendNak(pkt.cmd, ERR_INVALID_CMD);
            break;
    }
}

static void updateHost(uint16_t elapsedMs) {
    if (platformHostOverflow()) {
        stats.hostOverflows++;
        hostDeaf = true;
        hostQuietMs = 0;
        protocol.reset();
    }
    if (hostDeaf) {
        if (HOST.available()) {
            platformHostDiscard();
            hostQuietMs = 0;
        } else {
            hostQuietMs += elapsedMs;
            if (hostQuietMs >= HOST_QUIET_MS) hostDeaf = false;
        }
        return;
    }
    while (protocol.processInput()) {
        processPacket(protocol.getPacket());
    }
}

// ============================================================================
// SETUP AND LOOP
// ============================================================================

void setup() {
    platformInit();
    protocol.setSkipOversize(true);
    loadConfig();

    // Show the first frame before announcing ourselves.
    renderFrame();
    sendHello();
}

void loop() {
    uint16_t elapsedMs = platformElapsedMs();
    stats.uptimeMs += elapsedMs;

    fxcTick(elapsedMs);
    updateHost(elapsedMs);
    updateFrames(elapsedMs);

#ifdef HEARTBEAT_PIN
    heartbeatMs += elapsedMs;
    if (heartbeatMs >= HEARTBEAT_INTERVAL_MS) {
        heartbeatMs = 0;
        digitalWrite(HEARTBEAT_PIN, !digitalRead(HEARTBEAT_PIN));
    }
#endif
}
