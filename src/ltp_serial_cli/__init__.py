"""
LTP Serial Protocol v2 - Python Host Implementation

Communicate with microcontrollers running the LTP Serial Protocol v2
for LED strip control.

Example:
    from ltp_serial_cli import LtpDevice

    device = LtpDevice('/dev/ttyUSB0')
    device.connect()

    # Fill all pixels with red
    device.fill(255, 0, 0)
    device.show()

    # Set brightness
    device.set_brightness(128)

    device.close()
"""

from .protocol import (
    LtpProtocol,
    LtpPacket,
    # Commands
    CMD_NOP, CMD_RESET, CMD_ACK, CMD_NAK, CMD_HELLO, CMD_SHOW,
    CMD_GET_INFO, CMD_GET_PIXELS, CMD_GET_CONTROL, CMD_GET_STRIP, CMD_GET_INPUT,
    CMD_PIXEL_SET_ALL, CMD_PIXEL_SET_RANGE, CMD_PIXEL_SET_INDEXED,
    CMD_PIXEL_FRAME, CMD_PIXEL_FRAME_RLE, CMD_PIXEL_DELTA,
    CMD_SET_CONTROL, CMD_INPUT_EVENT,
    # Info types
    INFO_ALL, INFO_VERSION, INFO_STRIPS, INFO_STATUS, INFO_CONTROLS, INFO_STATS, INFO_INPUTS,
    # Error codes
    ERR_OK, ERR_CHECKSUM, ERR_INVALID_CMD, ERR_INVALID_LENGTH,
    ERR_INVALID_PARAM, ERR_BUFFER_OVERFLOW, ERR_PIXEL_OVERFLOW,
    # Control IDs
    CTRL_ID_BRIGHTNESS, CTRL_ID_GAMMA, CTRL_ID_IDLE_TIMEOUT,
    CTRL_ID_AUTO_SHOW, CTRL_ID_FRAME_ACK,
    # LED types
    LED_TYPE_WS2812, LED_TYPE_SK6812, LED_TYPE_APA102, LED_TYPE_LPD8806,
)

from .device import LtpDevice, DeviceInfo, StripInfo, DeviceStatus, DeviceStats
from .exceptions import (
    LtpError,
    LtpConnectionError,
    LtpTimeoutError,
    LtpProtocolError,
    LtpDeviceError,
)

__version__ = "2.0.0"
__all__ = [
    # Main class
    "LtpDevice",
    "LtpProtocol",
    "LtpPacket",
    # Data classes
    "DeviceInfo",
    "StripInfo",
    "DeviceStatus",
    "DeviceStats",
    # Exceptions
    "LtpError",
    "LtpConnectionError",
    "LtpTimeoutError",
    "LtpProtocolError",
    "LtpDeviceError",
]
