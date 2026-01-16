/**
 * LTP Serial Protocol v2 - Protocol Definitions
 *
 * Binary bidirectional protocol for LED strip control.
 * See spec/serial-protocol-v2.md for full specification.
 */

#ifndef LTP_PROTOCOL_H
#define LTP_PROTOCOL_H

#include <Arduino.h>

// Protocol constants
#define LTP_START_BYTE      0xAA
#define LTP_MAX_PAYLOAD     1024
#define LTP_PROTOCOL_MAJOR  2
#define LTP_PROTOCOL_MINOR  0

// Packet flags
#define FLAG_COMPRESSED     0x10
#define FLAG_CONTINUED      0x08
#define FLAG_RESPONSE       0x04
#define FLAG_ACK_REQ        0x02
#define FLAG_ERROR          0x01

// System Commands (0x00-0x0F)
#define CMD_NOP             0x00
#define CMD_RESET           0x01
#define CMD_ACK             0x02
#define CMD_NAK             0x03
#define CMD_HELLO           0x04
#define CMD_SHOW            0x05

// Query Commands (0x10-0x1F)
#define CMD_GET_INFO        0x10
#define CMD_GET_PIXELS      0x11
#define CMD_GET_CONTROL     0x12
#define CMD_GET_STRIP       0x13
#define CMD_GET_INPUT       0x14

// Query Response Commands (0x20-0x2F)
#define CMD_INFO_RESPONSE   0x20
#define CMD_PIXEL_RESPONSE  0x21
#define CMD_CONTROL_RESPONSE 0x22
#define CMD_STRIP_RESPONSE  0x23
#define CMD_CONTROLS_LIST   0x24
#define CMD_INPUT_RESPONSE  0x25
#define CMD_INPUTS_LIST     0x26

// Pixel Data Commands (0x30-0x3F)
#define CMD_PIXEL_SET_ALL   0x30
#define CMD_PIXEL_SET_RANGE 0x31
#define CMD_PIXEL_SET_INDEXED 0x32
#define CMD_PIXEL_FRAME     0x33
#define CMD_PIXEL_FRAME_RLE 0x34
#define CMD_PIXEL_DELTA     0x35

// Configuration Commands (0x40-0x4F)
#define CMD_SET_CONTROL     0x40
#define CMD_SET_STRIP       0x41
#define CMD_SAVE_CONFIG     0x42
#define CMD_LOAD_CONFIG     0x43
#define CMD_RESET_CONFIG    0x44
#define CMD_SET_SEGMENT     0x45

// Event Commands (0x50-0x5F)
#define CMD_STATUS_UPDATE   0x50
#define CMD_FRAME_ACK       0x51
#define CMD_ERROR_EVENT     0x52
#define CMD_INPUT_EVENT     0x53

// Info types for GET_INFO
#define INFO_ALL            0x00
#define INFO_VERSION        0x01
#define INFO_STRIPS         0x02
#define INFO_STATUS         0x03
#define INFO_CONTROLS       0x04
#define INFO_STATS          0x05
#define INFO_INPUTS         0x06

// Error codes
#define ERR_OK              0x00
#define ERR_CHECKSUM        0x01
#define ERR_INVALID_CMD     0x02
#define ERR_INVALID_LENGTH  0x03
#define ERR_INVALID_PARAM   0x04
#define ERR_BUFFER_OVERFLOW 0x05
#define ERR_PIXEL_OVERFLOW  0x06
#define ERR_BUSY            0x07
#define ERR_NOT_SUPPORTED   0x08
#define ERR_TIMEOUT         0x09
#define ERR_HARDWARE        0x0A
#define ERR_CONFIG          0x0B

// Color formats
#define COLOR_RGB           0x03
#define COLOR_RGBW          0x04
#define COLOR_GRB           0x13
#define COLOR_GRBW          0x14

// LED types
#define LED_TYPE_WS2812     0x00
#define LED_TYPE_SK6812     0x01
#define LED_TYPE_APA102     0x02
#define LED_TYPE_LPD8806    0x03
#define LED_TYPE_DOTSTAR    0x04

// Capabilities flags byte 1
#define CAPS_BRIGHTNESS     0x01
#define CAPS_GAMMA          0x02
#define CAPS_RLE            0x04
#define CAPS_FLOW_CTRL      0x08
#define CAPS_TEMP_SENSOR    0x10
#define CAPS_VOLT_SENSOR    0x20
#define CAPS_SEGMENTS       0x40
#define CAPS_EXTENDED       0x80

// Capabilities flags byte 2 (extended)
#define CAPS_FRAME_ACK      0x01
#define CAPS_PIXEL_READBACK 0x02
#define CAPS_EEPROM         0x04
#define CAPS_USB_HIGHSPEED  0x08
#define CAPS_MULTI_STRIP    0x10
#define CAPS_INPUTS         0x20

// Control types
#define CTRL_BOOL           0x01
#define CTRL_UINT8          0x02
#define CTRL_UINT16         0x03
#define CTRL_INT8           0x04
#define CTRL_INT16          0x05
#define CTRL_ENUM           0x06
#define CTRL_STRING         0x07
#define CTRL_COLOR          0x08
#define CTRL_ACTION         0x09

// Control IDs (standard)
#define CTRL_ID_BRIGHTNESS  0
#define CTRL_ID_GAMMA       1
#define CTRL_ID_IDLE_TIMEOUT 2
#define CTRL_ID_AUTO_SHOW   3
#define CTRL_ID_FRAME_ACK   4
#define CTRL_ID_STATUS_INTERVAL 5

// Input types
#define INPUT_BUTTON        0x01
#define INPUT_ENCODER       0x02
#define INPUT_ENCODER_BTN   0x03
#define INPUT_ANALOG        0x04
#define INPUT_TOUCH         0x05
#define INPUT_SWITCH        0x06
#define INPUT_MULTI_BUTTON  0x07

// Strip ID for all strips
#define STRIP_ALL           0xFF

// Parser states
enum class ParserState : uint8_t {
    WAIT_START,
    READ_FLAGS,
    READ_LENGTH_LOW,
    READ_LENGTH_HIGH,
    READ_CMD,
    READ_PAYLOAD,
    READ_CHECKSUM
};

// Packet structure
struct LtpPacket {
    uint8_t flags;
    uint16_t length;
    uint8_t cmd;
    uint8_t payload[LTP_MAX_PAYLOAD];
    uint8_t checksum;

    void clear() {
        flags = 0;
        length = 0;
        cmd = 0;
        checksum = 0;
    }
};

// Protocol handler class
class LtpProtocol {
public:
    LtpProtocol(Stream& serial, uint16_t maxPayload = 512);

    // Process incoming bytes, returns true when complete packet received
    bool processInput();

    // Get the received packet (valid after processInput returns true)
    const LtpPacket& getPacket() const { return rxPacket; }

    // Send packet
    void sendPacket(uint8_t cmd, const uint8_t* payload, uint16_t length, uint8_t flags = 0);

    // Send simple responses
    void sendAck(uint8_t cmd, uint8_t seq = 0);
    void sendNak(uint8_t cmd, uint8_t errorCode);

    // Reset parser state
    void reset();

private:
    Stream& serial;
    LtpPacket rxPacket;
    ParserState state;
    uint16_t payloadIndex;
    uint8_t runningChecksum;
    uint16_t maxPayload;
    uint32_t lastByteTime;

    static const uint32_t INTER_BYTE_TIMEOUT = 10; // ms
};

#endif // LTP_PROTOCOL_H
