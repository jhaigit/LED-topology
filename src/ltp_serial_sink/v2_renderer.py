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
    CTRL_ID_LOCAL_MODE,
    CMD_SAVE_CONFIG,
    CMD_LOAD_CONFIG,
    CMD_RESET_CONFIG,
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


@dataclass
class DeviceInput:
    """Information about an input exposed by the serial device."""

    id: int
    name: str
    input_type: str  # "button", "switch", "encoder", "analog", etc.
    value: Any = False  # Current value


# Input type mapping from protocol constants to names
INPUT_TYPE_NAMES = {
    0x01: "button",
    0x02: "encoder",
    0x03: "encoder_button",
    0x04: "analog",
    0x05: "touch",
    0x06: "switch",
    0x07: "multi_button",
}


# Callback type for input events
InputEventCallback = Any  # Callable[[int, str, str, Any, int], None]


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
        self._inputs: dict[int, DeviceInput] = {}

        # External callback for input events
        self._input_event_callback: InputEventCallback | None = None

        # External callback for device reset
        self._reset_callback: Any = None

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

                # Query and store inputs
                self._populate_inputs()

                # Set up input event callback
                self._device.set_input_callback(self._handle_input_event)

                # Set up reset detection callback
                self._device.set_reset_callback(self._handle_device_reset)

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

        # Idle timeout (available on devices with EEPROM)
        self._controls[CTRL_ID_IDLE_TIMEOUT] = DeviceControl(
            id=CTRL_ID_IDLE_TIMEOUT,
            name="idle_timeout",
            control_type="uint16",
            min_value=0,
            max_value=65535,
        )

        # Local mode control (uint8 - values are device-specific)
        self._controls[CTRL_ID_LOCAL_MODE] = DeviceControl(
            id=CTRL_ID_LOCAL_MODE,
            name="local_mode",
            control_type="uint8",
            min_value=0,
            max_value=255,
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

    def _populate_inputs(self) -> None:
        """Query and store inputs from the device."""
        self._inputs.clear()

        if not self._device or not self._device_info:
            return

        # Always try to query inputs - some devices may not properly report
        # has_inputs capability but still support inputs
        try:
            # Query all inputs from device
            inputs_data = self._device.get_inputs()
            if inputs_data:
                for input_info in inputs_data:
                    input_id = input_info.get("id", 0)
                    input_type_code = input_info.get("type", 0x01)
                    input_type_name = INPUT_TYPE_NAMES.get(input_type_code, "unknown")
                    input_value = input_info.get("value", False)
                    input_name = input_info.get("name", f"input_{input_id}")

                    self._inputs[input_id] = DeviceInput(
                        id=input_id,
                        name=input_name,
                        input_type=input_type_name,
                        value=input_value,
                    )

            logger.info(f"Device inputs: {[inp.name for inp in self._inputs.values()]}")
        except Exception as e:
            logger.warning(f"Failed to query device inputs: {e}")

    def _handle_input_event(
        self, input_id: int, input_type: int, timestamp: int, data: bytes
    ) -> None:
        """Handle input event from device."""
        # Parse value and optional name from data
        # Format: [value, name_len, name...]
        value = bool(data[0]) if data else False

        # Parse name if present (new format)
        parsed_name = None
        if len(data) >= 2:
            name_len = data[1]
            if name_len > 0 and len(data) >= 2 + name_len:
                parsed_name = data[2:2 + name_len].decode("utf-8", errors="replace")

        input_type_name = INPUT_TYPE_NAMES.get(input_type, "unknown")

        # Update stored input state or create new entry
        if input_id in self._inputs:
            self._inputs[input_id].value = value
            # Update name if we got one from the event and current name is auto-generated
            if parsed_name and self._inputs[input_id].name.startswith("input_"):
                self._inputs[input_id].name = parsed_name
            input_name = self._inputs[input_id].name
            input_type_name = self._inputs[input_id].input_type
        else:
            # Create new input entry for inputs not reported in capabilities
            input_name = parsed_name or f"input_{input_id}"
            self._inputs[input_id] = DeviceInput(
                id=input_id,
                name=input_name,
                input_type=input_type_name,
                value=value,
            )
            logger.info(f"Discovered new input: {input_name} ({input_type_name})")

        logger.debug(f"Input event: {input_name} ({input_type_name}) = {value}")

        # Call external callback if set
        if self._input_event_callback:
            try:
                self._input_event_callback(
                    input_id, input_name, input_type_name, value, timestamp
                )
            except Exception as e:
                logger.warning(f"Input event callback error: {e}")

    def set_input_event_callback(self, callback: InputEventCallback | None) -> None:
        """Set callback for input events.

        The callback receives: (input_id, input_name, input_type, value, timestamp)
        """
        self._input_event_callback = callback

    def _handle_device_reset(self) -> None:
        """Handle device reset detection (unsolicited HELLO)."""
        logger.info("Device reset detected, refreshing controls and inputs")

        # Refresh device info
        self._device_info = self._device.info if self._device else None

        # Re-populate controls with fresh values from device
        self._populate_controls()
        self._populate_inputs()

        # Notify external callback if set
        if self._reset_callback:
            try:
                self._reset_callback()
            except Exception as e:
                logger.warning(f"Reset callback error: {e}")

    def set_reset_callback(self, callback: Any) -> None:
        """Set callback for device reset detection.

        Called when the device resets and control values may have changed.
        """
        self._reset_callback = callback

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

    @property
    def inputs(self) -> dict[int, DeviceInput]:
        """Get available device inputs."""
        return self._inputs

    def get_control(self, control_id: int) -> Any:
        """Get a control value from the device."""
        if not self.is_connected() or not self._device:
            return None

        try:
            return self._device.get_control(control_id)
        except LtpError as e:
            ctrl = self._controls.get(control_id)
            ctrl_name = ctrl.name if ctrl else f"id={control_id}"
            logger.warning(f"Failed to get control {ctrl_name}: {e}")
            return None

    def set_control(self, control_id: int, value: Any) -> bool:
        """Set a control value on the device."""
        if not self.is_connected() or not self._device:
            return False

        try:
            self._device.set_control(control_id, value)
            return True
        except LtpError as e:
            ctrl = self._controls.get(control_id)
            ctrl_name = ctrl.name if ctrl else f"id={control_id}"
            logger.warning(f"Failed to set control {ctrl_name} to {value!r}: {e}")
            return False

    def set_brightness(self, value: int) -> bool:
        """Set device brightness (0-255)."""
        return self.set_control(CTRL_ID_BRIGHTNESS, value)

    def set_gamma(self, value: float) -> bool:
        """Set device gamma (1.0-3.0)."""
        # Gamma is stored as value * 10 in protocol
        return self.set_control(CTRL_ID_GAMMA, int(value * 10))

    def set_idle_timeout(self, seconds: int) -> bool:
        """Set idle timeout in seconds (0 to disable)."""
        return self.set_control(CTRL_ID_IDLE_TIMEOUT, seconds)

    def get_idle_timeout(self) -> int:
        """Get idle timeout in seconds (0 = disabled)."""
        return self.get_control(CTRL_ID_IDLE_TIMEOUT) or 0

    def save_config(self) -> bool:
        """Save current configuration to device EEPROM/flash."""
        if not self.is_connected() or not self._device:
            return False
        try:
            self._device.save_config()
            return True
        except Exception as e:
            logger.warning(f"Failed to save config: {e}")
            return False

    def load_config(self) -> bool:
        """Load configuration from device EEPROM/flash."""
        if not self.is_connected() or not self._device:
            return False
        try:
            self._device.load_config()
            return True
        except Exception as e:
            logger.warning(f"Failed to load config: {e}")
            return False

    def reset_config(self) -> bool:
        """Reset device configuration to factory defaults."""
        if not self.is_connected() or not self._device:
            return False
        try:
            self._device.reset_config()
            return True
        except Exception as e:
            logger.warning(f"Failed to reset config: {e}")
            return False

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
