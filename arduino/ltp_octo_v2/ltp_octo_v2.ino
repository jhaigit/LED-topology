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

#include <OctoWS2811.h>
#include "config.h"
#include "protocol.h"
#include "led_driver_octo.h"

// ============================================================================
// GLOBALS
// ============================================================================

LedDriverOcto leds;

// Protocol handler
LtpProtocol protocol(Serial, MAX_PAYLOAD_SIZE);

// Device state
struct {
    uint8_t brightness = 255;
    uint8_t gamma = 22;         // 2.2 * 10
    uint16_t idleTimeout = 0;   // 0 = disabled
    bool autoShow = false;
    bool frameAck = false;
    uint16_t statusInterval = 0;
} config;

// Statistics
struct {
    uint32_t framesReceived = 0;
    uint32_t framesDisplayed = 0;
    uint32_t bytesReceived = 0;
    uint16_t checksumErrors = 0;
    uint16_t bufferOverflows = 0;
    uint32_t startTime = 0;
} stats;

#define NUM_CONTROLS 6

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
    payload[9] = CAPS_PIXEL_READBACK;
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
            response[respLen++] = CAPS_PIXEL_READBACK;
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

        case CMD_SET_CONTROL:
            handleSetControl(pkt.payload, pkt.length);
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

    leds.begin();
    leds.clear();
    leds.show();

    stats.startTime = millis();

    // Brief startup indicator
    for (int i = 0; i < 3; i++) {
        leds.fillStrip(0, 0, 32, 0);  // Dim green on first strip
        leds.show();
        delay(100);
        leds.clear();
        leds.show();
        delay(100);
    }

    delay(100);
    sendHello();
}

void loop() {
    if (protocol.processInput()) {
        processPacket(protocol.getPacket());
    }
}
