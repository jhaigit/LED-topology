/**
 * LTP Serial Protocol v2 - Protocol Implementation
 */

#include "protocol.h"

LtpProtocol::LtpProtocol(Stream& serial, uint16_t maxPayload)
    : serial(serial)
    , state(ParserState::WAIT_START)
    , payloadIndex(0)
    , runningChecksum(0)
    , maxPayload(min(maxPayload, (uint16_t)LTP_MAX_PAYLOAD))
    , lastByteTime(0)
{
    rxPacket.clear();
}

void LtpProtocol::reset() {
    state = ParserState::WAIT_START;
    payloadIndex = 0;
    runningChecksum = 0;
    rxPacket.clear();
}

bool LtpProtocol::processInput() {
    // Check for inter-byte timeout
    if (state != ParserState::WAIT_START && millis() - lastByteTime > INTER_BYTE_TIMEOUT) {
        reset();
    }

    while (serial.available()) {
        uint8_t byte = serial.read();
        lastByteTime = millis();

        switch (state) {
            case ParserState::WAIT_START:
                if (byte == LTP_START_BYTE) {
                    rxPacket.clear();
                    runningChecksum = 0;
                    state = ParserState::READ_FLAGS;
                }
                break;

            case ParserState::READ_FLAGS:
                rxPacket.flags = byte;
                runningChecksum ^= byte;
                state = ParserState::READ_LENGTH_LOW;
                break;

            case ParserState::READ_LENGTH_LOW:
                rxPacket.length = byte;
                runningChecksum ^= byte;
                state = ParserState::READ_LENGTH_HIGH;
                break;

            case ParserState::READ_LENGTH_HIGH:
                rxPacket.length |= (uint16_t)byte << 8;
                runningChecksum ^= byte;
                if (rxPacket.length > maxPayload) {
                    // Payload too large, reset
                    reset();
                } else {
                    state = ParserState::READ_CMD;
                }
                break;

            case ParserState::READ_CMD:
                rxPacket.cmd = byte;
                runningChecksum ^= byte;
                payloadIndex = 0;
                if (rxPacket.length > 0) {
                    state = ParserState::READ_PAYLOAD;
                } else {
                    state = ParserState::READ_CHECKSUM;
                }
                break;

            case ParserState::READ_PAYLOAD:
                rxPacket.payload[payloadIndex++] = byte;
                runningChecksum ^= byte;
                if (payloadIndex >= rxPacket.length) {
                    state = ParserState::READ_CHECKSUM;
                }
                break;

            case ParserState::READ_CHECKSUM:
                rxPacket.checksum = byte;
                state = ParserState::WAIT_START;
                if (runningChecksum == byte) {
                    return true; // Valid packet received
                }
                // Checksum error - packet discarded
                break;
        }
    }

    return false;
}

void LtpProtocol::sendPacket(uint8_t cmd, const uint8_t* payload, uint16_t length, uint8_t flags) {
    uint8_t checksum = 0;

    // Start byte
    serial.write(LTP_START_BYTE);

    // Flags (with RESPONSE flag set)
    uint8_t txFlags = flags | FLAG_RESPONSE;
    serial.write(txFlags);
    checksum ^= txFlags;

    // Length (little-endian)
    serial.write((uint8_t)(length & 0xFF));
    checksum ^= (uint8_t)(length & 0xFF);
    serial.write((uint8_t)(length >> 8));
    checksum ^= (uint8_t)(length >> 8);

    // Command
    serial.write(cmd);
    checksum ^= cmd;

    // Payload
    for (uint16_t i = 0; i < length; i++) {
        serial.write(payload[i]);
        checksum ^= payload[i];
    }

    // Checksum
    serial.write(checksum);
}

void LtpProtocol::sendAck(uint8_t cmd, uint8_t seq) {
    uint8_t payload[2] = { cmd, seq };
    sendPacket(CMD_ACK, payload, 2);
}

void LtpProtocol::sendNak(uint8_t cmd, uint8_t errorCode) {
    uint8_t payload[2] = { cmd, errorCode };
    sendPacket(CMD_NAK, payload, 2, FLAG_ERROR);
}
