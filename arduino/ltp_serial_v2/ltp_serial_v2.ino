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

#include "protocol.h"
#include "led_driver.h"
#include "led_driver_lpd8806.h"

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

// Maximum payload we can handle (limited by RAM)
// 160 pixels * 3 bytes = 480 bytes for full frame
#define MAX_PAYLOAD_SIZE    512

// ============================================================================
// GLOBALS
// ============================================================================

// LED driver - change this line to use a different LED chip
LedDriverLPD8806 leds(NUM_PIXELS, DATA_PIN, CLOCK_PIN, USE_HARDWARE_SPI);

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

// Control definitions
#define NUM_CONTROLS 6

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
    payload[9] = CAPS_PIXEL_READBACK; // Caps byte 2 (extended)
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
    uint8_t response[64];
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
            response[respLen++] = CAPS_PIXEL_READBACK;
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

        default:
            protocol.sendNak(CMD_GET_INFO, ERR_INVALID_PARAM);
            return;
    }

    protocol.sendPacket(CMD_INFO_RESPONSE, response, respLen);
}

void handleShow(const uint8_t* payload, uint16_t length) {
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

    // Copy pixel data
    const uint8_t* pixelData = payload + dataOffset;
    uint8_t bpp = leds.getBytesPerPixel();

    for (uint16_t i = 0; i < count; i++) {
        uint16_t offset = i * bpp;
        leds.setPixel(start + i, pixelData[offset], pixelData[offset + 1], pixelData[offset + 2]);
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
// ARDUINO SETUP AND LOOP
// ============================================================================

void setup() {
    // Initialize serial
    Serial.begin(SERIAL_BAUD);

    // Initialize LED driver
    leds.begin();
    leds.clear();
    leds.show();

    // Record start time
    stats.startTime = millis();

    // Send HELLO to announce ourselves
    delay(100); // Small delay for serial to stabilize
    sendHello();
}

void loop() {
    // Process incoming serial data
    if (protocol.processInput()) {
        processPacket(protocol.getPacket());
    }
}
