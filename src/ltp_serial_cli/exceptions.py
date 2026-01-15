"""
LTP Serial Protocol v2 - Exceptions
"""


class LtpError(Exception):
    """Base exception for LTP protocol errors."""
    pass


class LtpConnectionError(LtpError):
    """Failed to connect to device."""
    pass


class LtpTimeoutError(LtpError):
    """Timeout waiting for device response."""
    pass


class LtpProtocolError(LtpError):
    """Protocol-level error (checksum, invalid packet, etc.)."""
    pass


class LtpDeviceError(LtpError):
    """Device returned an error (NAK response)."""

    # Error code mapping
    ERROR_NAMES = {
        0x00: "OK",
        0x01: "CHECKSUM_ERROR",
        0x02: "INVALID_COMMAND",
        0x03: "INVALID_LENGTH",
        0x04: "INVALID_PARAMETER",
        0x05: "BUFFER_OVERFLOW",
        0x06: "PIXEL_OVERFLOW",
        0x07: "BUSY",
        0x08: "NOT_SUPPORTED",
        0x09: "TIMEOUT",
        0x0A: "HARDWARE_ERROR",
        0x0B: "CONFIG_ERROR",
    }

    def __init__(self, error_code: int, command: int = 0, message: str = ""):
        self.error_code = error_code
        self.command = command
        error_name = self.ERROR_NAMES.get(error_code, f"UNKNOWN_0x{error_code:02X}")
        if message:
            super().__init__(f"{error_name}: {message}")
        else:
            super().__init__(f"{error_name} (code 0x{error_code:02X}) for command 0x{command:02X}")
