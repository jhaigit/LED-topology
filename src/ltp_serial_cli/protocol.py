"""
LTP Serial Protocol v2 - Protocol Constants and Packet Handling

Low-level protocol implementation for building and parsing LTP packets.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional
import struct

# Protocol constants
LTP_START_BYTE = 0xAA
LTP_MAX_PAYLOAD = 1024
LTP_PROTOCOL_MAJOR = 2
LTP_PROTOCOL_MINOR = 0

# Packet flags
FLAG_COMPRESSED = 0x10
FLAG_CONTINUED = 0x08
FLAG_RESPONSE = 0x04
FLAG_ACK_REQ = 0x02
FLAG_ERROR = 0x01

# System Commands (0x00-0x0F)
CMD_NOP = 0x00
CMD_RESET = 0x01
CMD_ACK = 0x02
CMD_NAK = 0x03
CMD_HELLO = 0x04
CMD_SHOW = 0x05

# Query Commands (0x10-0x1F)
CMD_GET_INFO = 0x10
CMD_GET_PIXELS = 0x11
CMD_GET_CONTROL = 0x12
CMD_GET_STRIP = 0x13
CMD_GET_INPUT = 0x14

# Query Response Commands (0x20-0x2F)
CMD_INFO_RESPONSE = 0x20
CMD_PIXEL_RESPONSE = 0x21
CMD_CONTROL_RESPONSE = 0x22
CMD_STRIP_RESPONSE = 0x23
CMD_CONTROLS_LIST = 0x24
CMD_INPUT_RESPONSE = 0x25
CMD_INPUTS_LIST = 0x26

# Pixel Data Commands (0x30-0x3F)
CMD_PIXEL_SET_ALL = 0x30
CMD_PIXEL_SET_RANGE = 0x31
CMD_PIXEL_SET_INDEXED = 0x32
CMD_PIXEL_FRAME = 0x33
CMD_PIXEL_FRAME_RLE = 0x34
CMD_PIXEL_DELTA = 0x35

# Configuration Commands (0x40-0x4F)
CMD_SET_CONTROL = 0x40
CMD_SET_STRIP = 0x41
CMD_SAVE_CONFIG = 0x42
CMD_LOAD_CONFIG = 0x43
CMD_RESET_CONFIG = 0x44
CMD_SET_SEGMENT = 0x45

# Event Commands (0x50-0x5F)
CMD_STATUS_UPDATE = 0x50
CMD_FRAME_ACK = 0x51
CMD_ERROR_EVENT = 0x52
CMD_INPUT_EVENT = 0x53

# Info types
INFO_ALL = 0x00
INFO_VERSION = 0x01
INFO_STRIPS = 0x02
INFO_STATUS = 0x03
INFO_CONTROLS = 0x04
INFO_STATS = 0x05
INFO_INPUTS = 0x06

# Error codes
ERR_OK = 0x00
ERR_CHECKSUM = 0x01
ERR_INVALID_CMD = 0x02
ERR_INVALID_LENGTH = 0x03
ERR_INVALID_PARAM = 0x04
ERR_BUFFER_OVERFLOW = 0x05
ERR_PIXEL_OVERFLOW = 0x06
ERR_BUSY = 0x07
ERR_NOT_SUPPORTED = 0x08
ERR_TIMEOUT = 0x09
ERR_HARDWARE = 0x0A
ERR_CONFIG = 0x0B

# Color formats
COLOR_RGB = 0x03
COLOR_RGBW = 0x04
COLOR_GRB = 0x13
COLOR_GRBW = 0x14
COLOR_BGR = 0x23
COLOR_BRG = 0x33

# LED types
LED_TYPE_WS2812 = 0x00
LED_TYPE_SK6812 = 0x01
LED_TYPE_APA102 = 0x02
LED_TYPE_LPD8806 = 0x03
LED_TYPE_DOTSTAR = 0x04

# Capabilities flags byte 1
CAPS_BRIGHTNESS = 0x01
CAPS_GAMMA = 0x02
CAPS_RLE = 0x04
CAPS_FLOW_CTRL = 0x08
CAPS_TEMP_SENSOR = 0x10
CAPS_VOLT_SENSOR = 0x20
CAPS_SEGMENTS = 0x40
CAPS_EXTENDED = 0x80

# Capabilities flags byte 2 (extended)
CAPS_FRAME_ACK = 0x01
CAPS_PIXEL_READBACK = 0x02
CAPS_EEPROM = 0x04
CAPS_USB_HIGHSPEED = 0x08
CAPS_MULTI_STRIP = 0x10
CAPS_INPUTS = 0x20

# Control types
CTRL_BOOL = 0x01
CTRL_UINT8 = 0x02
CTRL_UINT16 = 0x03
CTRL_INT8 = 0x04
CTRL_INT16 = 0x05
CTRL_ENUM = 0x06
CTRL_STRING = 0x07
CTRL_COLOR = 0x08
CTRL_ACTION = 0x09

# Control IDs (standard)
CTRL_ID_BRIGHTNESS = 0
CTRL_ID_GAMMA = 1
CTRL_ID_IDLE_TIMEOUT = 2
CTRL_ID_AUTO_SHOW = 3
CTRL_ID_FRAME_ACK = 4
CTRL_ID_STATUS_INTERVAL = 5

# Input types
INPUT_BUTTON = 0x01
INPUT_ENCODER = 0x02
INPUT_ENCODER_BTN = 0x03
INPUT_ANALOG = 0x04
INPUT_TOUCH = 0x05
INPUT_SWITCH = 0x06
INPUT_MULTI_BUTTON = 0x07

# Strip ID for all strips
STRIP_ALL = 0xFF

# Command names for debugging
COMMAND_NAMES = {
    CMD_NOP: "NOP",
    CMD_RESET: "RESET",
    CMD_ACK: "ACK",
    CMD_NAK: "NAK",
    CMD_HELLO: "HELLO",
    CMD_SHOW: "SHOW",
    CMD_GET_INFO: "GET_INFO",
    CMD_GET_PIXELS: "GET_PIXELS",
    CMD_GET_CONTROL: "GET_CONTROL",
    CMD_GET_STRIP: "GET_STRIP",
    CMD_GET_INPUT: "GET_INPUT",
    CMD_INFO_RESPONSE: "INFO_RESPONSE",
    CMD_PIXEL_RESPONSE: "PIXEL_RESPONSE",
    CMD_CONTROL_RESPONSE: "CONTROL_RESPONSE",
    CMD_STRIP_RESPONSE: "STRIP_RESPONSE",
    CMD_CONTROLS_LIST: "CONTROLS_LIST",
    CMD_INPUT_RESPONSE: "INPUT_RESPONSE",
    CMD_INPUTS_LIST: "INPUTS_LIST",
    CMD_PIXEL_SET_ALL: "PIXEL_SET_ALL",
    CMD_PIXEL_SET_RANGE: "PIXEL_SET_RANGE",
    CMD_PIXEL_SET_INDEXED: "PIXEL_SET_INDEXED",
    CMD_PIXEL_FRAME: "PIXEL_FRAME",
    CMD_PIXEL_FRAME_RLE: "PIXEL_FRAME_RLE",
    CMD_PIXEL_DELTA: "PIXEL_DELTA",
    CMD_SET_CONTROL: "SET_CONTROL",
    CMD_SET_STRIP: "SET_STRIP",
    CMD_SAVE_CONFIG: "SAVE_CONFIG",
    CMD_LOAD_CONFIG: "LOAD_CONFIG",
    CMD_RESET_CONFIG: "RESET_CONFIG",
    CMD_SET_SEGMENT: "SET_SEGMENT",
    CMD_STATUS_UPDATE: "STATUS_UPDATE",
    CMD_FRAME_ACK: "FRAME_ACK",
    CMD_ERROR_EVENT: "ERROR_EVENT",
    CMD_INPUT_EVENT: "INPUT_EVENT",
}

LED_TYPE_NAMES = {
    LED_TYPE_WS2812: "WS2812",
    LED_TYPE_SK6812: "SK6812",
    LED_TYPE_APA102: "APA102",
    LED_TYPE_LPD8806: "LPD8806",
    LED_TYPE_DOTSTAR: "DotStar",
}

COLOR_FORMAT_NAMES = {
    COLOR_RGB: "RGB",
    COLOR_RGBW: "RGBW",
    COLOR_GRB: "GRB",
    COLOR_GRBW: "GRBW",
    COLOR_BGR: "BGR",
    COLOR_BRG: "BRG",
}


@dataclass
class LtpPacket:
    """Represents an LTP protocol packet."""

    cmd: int = 0
    payload: bytes = field(default_factory=bytes)
    flags: int = 0

    @property
    def is_response(self) -> bool:
        return bool(self.flags & FLAG_RESPONSE)

    @property
    def is_error(self) -> bool:
        return bool(self.flags & FLAG_ERROR)

    @property
    def ack_requested(self) -> bool:
        return bool(self.flags & FLAG_ACK_REQ)

    @property
    def command_name(self) -> str:
        return COMMAND_NAMES.get(self.cmd, f"UNKNOWN_0x{self.cmd:02X}")

    def __repr__(self) -> str:
        flags_str = []
        if self.is_response:
            flags_str.append("RSP")
        if self.is_error:
            flags_str.append("ERR")
        if self.ack_requested:
            flags_str.append("ACK")
        flags_part = f" [{','.join(flags_str)}]" if flags_str else ""
        payload_preview = self.payload[:16].hex() if self.payload else ""
        if len(self.payload) > 16:
            payload_preview += "..."
        return f"LtpPacket({self.command_name}{flags_part}, {len(self.payload)} bytes: {payload_preview})"


class LtpProtocol:
    """
    Low-level LTP protocol handler.

    Handles packet building and parsing.
    """

    def __init__(self):
        self._rx_buffer = bytearray()

    @staticmethod
    def build_packet(cmd: int, payload: bytes = b"", flags: int = 0) -> bytes:
        """
        Build a complete LTP packet.

        Args:
            cmd: Command byte
            payload: Payload data
            flags: Packet flags

        Returns:
            Complete packet bytes ready to send
        """
        length = len(payload)
        if length > LTP_MAX_PAYLOAD:
            raise ValueError(f"Payload too large: {length} > {LTP_MAX_PAYLOAD}")

        # Build packet
        packet = bytearray()
        packet.append(LTP_START_BYTE)
        packet.append(flags)
        packet.append(length & 0xFF)
        packet.append((length >> 8) & 0xFF)
        packet.append(cmd)
        packet.extend(payload)

        # Calculate checksum (XOR of flags through payload)
        checksum = 0
        for b in packet[1:]:  # Skip start byte
            checksum ^= b
        packet.append(checksum)

        return bytes(packet)

    def feed(self, data: bytes) -> list[LtpPacket]:
        """
        Feed received bytes to the parser.

        Args:
            data: Received bytes

        Returns:
            List of complete packets parsed from the buffer
        """
        self._rx_buffer.extend(data)
        packets = []

        while True:
            packet = self._try_parse_packet()
            if packet is None:
                break
            packets.append(packet)

        return packets

    def _try_parse_packet(self) -> Optional[LtpPacket]:
        """Try to parse a complete packet from the buffer."""
        # Find start byte
        while self._rx_buffer and self._rx_buffer[0] != LTP_START_BYTE:
            self._rx_buffer.pop(0)

        # Need at least 6 bytes for minimal packet (start + flags + length(2) + cmd + checksum)
        if len(self._rx_buffer) < 6:
            return None

        # Parse header
        flags = self._rx_buffer[1]
        length = self._rx_buffer[2] | (self._rx_buffer[3] << 8)

        # Check if we have complete packet
        total_length = 6 + length  # start + flags + length(2) + cmd + payload + checksum
        if len(self._rx_buffer) < total_length:
            return None

        # Extract packet bytes
        packet_bytes = bytes(self._rx_buffer[:total_length])
        self._rx_buffer = self._rx_buffer[total_length:]

        # Verify checksum
        checksum = 0
        for b in packet_bytes[1:-1]:  # Skip start byte and checksum
            checksum ^= b

        if checksum != packet_bytes[-1]:
            # Checksum error - packet discarded
            return None

        # Build packet object
        cmd = packet_bytes[4]
        payload = packet_bytes[5:-1]

        return LtpPacket(cmd=cmd, payload=payload, flags=flags)

    def reset(self):
        """Clear the receive buffer."""
        self._rx_buffer.clear()

    # Convenience methods for building common packets

    @staticmethod
    def build_nop(ack_request: bool = False) -> bytes:
        """Build a NOP packet."""
        flags = FLAG_ACK_REQ if ack_request else 0
        return LtpProtocol.build_packet(CMD_NOP, flags=flags)

    @staticmethod
    def build_reset() -> bytes:
        """Build a RESET packet."""
        return LtpProtocol.build_packet(CMD_RESET)

    @staticmethod
    def build_show(frame_number: int = 0) -> bytes:
        """Build a SHOW packet."""
        payload = struct.pack("<H", frame_number)
        return LtpProtocol.build_packet(CMD_SHOW, payload)

    @staticmethod
    def build_get_info(info_type: int = INFO_ALL) -> bytes:
        """Build a GET_INFO packet."""
        return LtpProtocol.build_packet(CMD_GET_INFO, bytes([info_type]))

    @staticmethod
    def build_get_pixels(strip_id: int, start: int, count: int) -> bytes:
        """Build a GET_PIXELS packet."""
        payload = struct.pack("<BHH", strip_id, start, count)
        return LtpProtocol.build_packet(CMD_GET_PIXELS, payload)

    @staticmethod
    def build_get_control(control_id: int) -> bytes:
        """Build a GET_CONTROL packet."""
        return LtpProtocol.build_packet(CMD_GET_CONTROL, bytes([control_id]))

    @staticmethod
    def build_pixel_set_all(r: int, g: int, b: int, strip_id: int = STRIP_ALL) -> bytes:
        """Build a PIXEL_SET_ALL packet."""
        payload = bytes([strip_id, r, g, b])
        return LtpProtocol.build_packet(CMD_PIXEL_SET_ALL, payload)

    @staticmethod
    def build_pixel_set_range(
        strip_id: int, start: int, end: int, r: int, g: int, b: int
    ) -> bytes:
        """Build a PIXEL_SET_RANGE packet."""
        payload = struct.pack("<BHHBBB", strip_id, start, end, r, g, b)
        return LtpProtocol.build_packet(CMD_PIXEL_SET_RANGE, payload)

    @staticmethod
    def build_pixel_frame(
        strip_id: int, start: int, pixel_data: bytes
    ) -> bytes:
        """Build a PIXEL_FRAME packet."""
        # Assume 3 bytes per pixel (RGB)
        count = len(pixel_data) // 3
        payload = struct.pack("<BHH", strip_id, start, count) + pixel_data
        return LtpProtocol.build_packet(CMD_PIXEL_FRAME, payload)

    @staticmethod
    def build_set_control_uint8(control_id: int, value: int) -> bytes:
        """Build a SET_CONTROL packet for UINT8 value."""
        payload = bytes([control_id, value & 0xFF])
        return LtpProtocol.build_packet(CMD_SET_CONTROL, payload)

    @staticmethod
    def build_set_control_uint16(control_id: int, value: int) -> bytes:
        """Build a SET_CONTROL packet for UINT16 value."""
        payload = struct.pack("<BH", control_id, value)
        return LtpProtocol.build_packet(CMD_SET_CONTROL, payload)

    @staticmethod
    def build_set_control_bool(control_id: int, value: bool) -> bytes:
        """Build a SET_CONTROL packet for BOOL value."""
        payload = bytes([control_id, 1 if value else 0])
        return LtpProtocol.build_packet(CMD_SET_CONTROL, payload)
