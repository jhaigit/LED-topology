"""LTP Serial Protocol v2 renderer for LTP Serial Sink.

This renderer uses the binary v2 protocol to communicate with microcontrollers
running the ltp_serial_v2 firmware.
"""

import logging
import sys
from dataclasses import dataclass, field
from typing import Any, TextIO

import numpy as np

try:
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

# Import from ltp_serial_cli (the v2 protocol implementation)
from ltp_serial_cli import (
    LtpDevice,
    DeviceInfo,
    DeviceStatus,
    DeviceStats,
    StripInfo,
    LtpError,
    LtpConnectionError,
    LtpTimeoutError,
)
from ltp_serial_cli.protocol import (
    CTRL_ID_BRIGHTNESS,
    CTRL_ID_GAMMA,
    CTRL_ID_AUTO_SHOW,
    CTRL_ID_FRAME_ACK,
    CTRL_ID_IDLE_TIMEOUT,
)

logger = logging.getLogger(__name__)


@dataclass
class V2RendererConfig:
    """Configuration for the v2 protocol renderer."""

    port: str = ""
    baudrate: int = 115200
    timeout: float = 2.0

    # Debug options
    debug: bool = False
    debug_file: TextIO | None = None

    # Frame options
    auto_show: bool = True  # Automatically call show() after sending pixels
    use_frame_ack: bool = False  # Wait for frame acknowledgment


@dataclass
class DeviceControl:
    """Information about a control exposed by the serial device."""

    id: int
    name: str
    control_type: str  # "bool", "uint8", "uint16", "enum", "action"
    value: Any = None
    min_value: int | None = None
    max_value: int | None = None
    enum_values: list[str] = field(default_factory=list)
    readonly: bool = False


class V2Renderer:
    """Renders pixel data using LTP Serial Protocol v2.

    This renderer communicates with microcontrollers using the binary v2 protocol,
    which provides checksums, proper framing, and support for device controls.
    """

    def __init__(self, config: V2RendererConfig):
        if not SERIAL_AVAILABLE:
            raise ImportError(
                "pyserial is required for serial sink. Install with: pip install pyserial"
            )

        self.config = config
        self._device: LtpDevice | None = None
        self._connected = False
        self._frame_count = 0
        self._last_frame: np.ndarray | None = None

        # Device info cached after connection
        self._device_info: DeviceInfo | None = None
        self._controls: dict[int, DeviceControl] = {}

    def is_connected(self) -> bool:
        """Check if device is connected."""
        return self._connected and self._device is not None and self._device.is_connected

    def open(self) -> None:
        """Open connection to the device."""
        if not self.config.port:
            raise ValueError("Serial port not specified")

        logger.info(f"Connecting to {self.config.port} at {self.config.baudrate} baud (v2 protocol)")

        self._device = LtpDevice(
            port=self.config.port,
            baudrate=self.config.baudrate,
            timeout=self.config.timeout,
            debug=self.config.debug,
            debug_file=self.config.debug_file,
        )

        try:
            self._device.connect()
            self._connected = True
            self._device_info = self._device.info
            self._last_frame = None  # Reset frame buffer on reconnect

            # Log device info
            if self._device_info:
                logger.info(
                    f"Connected to {self._device_info.device_name or 'device'}: "
                    f"{self._device_info.total_pixels} pixels, "
                    f"firmware v{self._device_info.firmware_version}"
                )

                # Build controls list from device capabilities
                self._populate_controls()

            # Configure device for streaming
            if self.config.auto_show:
                self._device.set_auto_show(True)
            if self.config.use_frame_ack:
                self._device.set_frame_ack(True)

        except LtpError as e:
            logger.error(f"Failed to connect: {e}")
            self._connected = False
            if self._device:
                try:
                    self._device.close()
                except Exception:
                    pass
                self._device = None
            raise

    def _populate_controls(self) -> None:
        """Populate controls dict from device capabilities."""
        self._controls.clear()

        if not self._device_info:
            return

        # Standard controls based on capabilities
        if self._device_info.has_brightness:
            self._controls[CTRL_ID_BRIGHTNESS] = DeviceControl(
                id=CTRL_ID_BRIGHTNESS,
                name="brightness",
                control_type="uint8",
                min_value=0,
                max_value=255,
            )

        if self._device_info.has_gamma:
            self._controls[CTRL_ID_GAMMA] = DeviceControl(
                id=CTRL_ID_GAMMA,
                name="gamma",
                control_type="uint8",
                min_value=10,  # 1.0 * 10
                max_value=30,  # 3.0 * 10
            )

        # Auto-show and frame-ack are always available
        self._controls[CTRL_ID_AUTO_SHOW] = DeviceControl(
            id=CTRL_ID_AUTO_SHOW,
            name="auto_show",
            control_type="bool",
        )
        self._controls[CTRL_ID_FRAME_ACK] = DeviceControl(
            id=CTRL_ID_FRAME_ACK,
            name="frame_ack",
            control_type="bool",
        )

        logger.debug(f"Device controls: {list(self._controls.keys())}")

    def close(self) -> None:
        """Close connection to the device."""
        if self._device:
            try:
                self._device.close()
            except Exception as e:
                logger.warning(f"Error closing device: {e}")
            self._device = None
        self._connected = False
        logger.info("Device connection closed")

    def clear(self) -> None:
        """Clear renderer state (called when stream stops)."""
        self._last_frame = None
        logger.debug("Renderer state cleared")

    @property
    def device_info(self) -> DeviceInfo | None:
        """Get cached device info."""
        return self._device_info

    @property
    def pixel_count(self) -> int:
        """Get total pixel count from device."""
        if self._device_info:
            return self._device_info.total_pixels
        return 0

    @property
    def controls(self) -> dict[int, DeviceControl]:
        """Get available device controls."""
        return self._controls

    def get_control(self, control_id: int) -> Any:
        """Get a control value from the device."""
        if not self.is_connected() or not self._device:
            return None

        try:
            return self._device.get_control(control_id)
        except LtpError as e:
            logger.warning(f"Failed to get control {control_id}: {e}")
            return None

    def set_control(self, control_id: int, value: Any) -> bool:
        """Set a control value on the device."""
        if not self.is_connected() or not self._device:
            return False

        try:
            self._device.set_control(control_id, value)
            return True
        except LtpError as e:
            logger.warning(f"Failed to set control {control_id}: {e}")
            return False

    def set_brightness(self, value: int) -> bool:
        """Set device brightness (0-255)."""
        return self.set_control(CTRL_ID_BRIGHTNESS, value)

    def set_gamma(self, value: float) -> bool:
        """Set device gamma (1.0-3.0)."""
        # Gamma is stored as value * 10 in protocol
        return self.set_control(CTRL_ID_GAMMA, int(value * 10))

    def render(self, pixels: np.ndarray) -> int:
        """Render pixel data to the device.

        Args:
            pixels: Pixel data as numpy array (n_pixels, 3) RGB

        Returns:
            Number of bytes sent (0 if nothing sent or error)
        """
        if not self.is_connected() or not self._device:
            return 0

        try:
            # Convert numpy array to bytes
            pixel_bytes = pixels.astype(np.uint8).tobytes()

            # Send the frame
            self._device.set_pixels(pixel_bytes)

            # Show if not using auto-show
            if not self.config.auto_show:
                self._device.show()

            self._frame_count += 1
            return len(pixel_bytes)

        except LtpError as e:
            logger.error(f"Error rendering frame: {e}")
            self._connected = False
            return 0

    def fill(self, r: int, g: int, b: int) -> bool:
        """Fill all pixels with a solid color."""
        if not self.is_connected() or not self._device:
            return False

        try:
            self._device.fill(r, g, b)
            if not self.config.auto_show:
                self._device.show()
            return True
        except LtpError as e:
            logger.error(f"Error filling: {e}")
            return False

    def show(self) -> bool:
        """Trigger display update on the device."""
        if not self.is_connected() or not self._device:
            return False

        try:
            self._device.show()
            return True
        except LtpError as e:
            logger.error(f"Error showing: {e}")
            return False

    def get_stats(self) -> dict[str, Any]:
        """Get renderer statistics."""
        device_stats = None
        if self.is_connected() and self._device:
            try:
                device_stats = self._device.get_stats()
            except LtpError:
                pass

        return {
            "connected": self.is_connected(),
            "port": self.config.port,
            "baudrate": self.config.baudrate,
            "protocol": "v2",
            "frame_count": self._frame_count,
            "device_stats": {
                "frames_received": device_stats.frames_received if device_stats else 0,
                "frames_displayed": device_stats.frames_displayed if device_stats else 0,
                "checksum_errors": device_stats.checksum_errors if device_stats else 0,
                "uptime_seconds": device_stats.uptime_seconds if device_stats else 0,
            } if device_stats else None,
        }

    @staticmethod
    def list_ports() -> list[dict[str, str]]:
        """List available serial ports."""
        if not SERIAL_AVAILABLE:
            return []

        ports = []
        for port in serial.tools.list_ports.comports():
            ports.append({
                "device": port.device,
                "description": port.description,
                "hwid": port.hwid,
            })
        return ports
