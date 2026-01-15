"""
LTP Serial Protocol v2 - High-Level Device Interface

Provides a convenient API for communicating with LTP devices.
"""

import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import serial

from .protocol import (
    LtpProtocol,
    LtpPacket,
    CMD_ACK,
    CMD_NAK,
    CMD_HELLO,
    CMD_SHOW,
    CMD_GET_INFO,
    CMD_INFO_RESPONSE,
    CMD_PIXEL_RESPONSE,
    CMD_CONTROL_RESPONSE,
    CMD_INPUT_EVENT,
    CMD_FRAME_ACK,
    CMD_STATUS_UPDATE,
    INFO_ALL,
    INFO_STRIPS,
    INFO_STATUS,
    INFO_STATS,
    CTRL_ID_BRIGHTNESS,
    CTRL_ID_GAMMA,
    CTRL_ID_AUTO_SHOW,
    CTRL_ID_FRAME_ACK,
    CAPS_EXTENDED,
    LED_TYPE_NAMES,
    COLOR_FORMAT_NAMES,
    STRIP_ALL,
)
from .exceptions import (
    LtpConnectionError,
    LtpTimeoutError,
    LtpProtocolError,
    LtpDeviceError,
)


@dataclass
class StripInfo:
    """Information about a single LED strip."""

    strip_id: int = 0
    pixel_count: int = 0
    color_format: int = 0
    led_type: int = 0
    data_pin: int = 0
    clock_pin: int = 0
    flags: int = 0

    @property
    def led_type_name(self) -> str:
        return LED_TYPE_NAMES.get(self.led_type, f"Unknown(0x{self.led_type:02X})")

    @property
    def color_format_name(self) -> str:
        return COLOR_FORMAT_NAMES.get(self.color_format, f"Unknown(0x{self.color_format:02X})")

    @property
    def is_reversed(self) -> bool:
        return bool(self.flags & 0x01)


@dataclass
class DeviceInfo:
    """Device information returned by HELLO or GET_INFO."""

    protocol_major: int = 0
    protocol_minor: int = 0
    firmware_major: int = 0
    firmware_minor: int = 0
    strip_count: int = 0
    total_pixels: int = 0
    color_format: int = 0
    capabilities1: int = 0
    capabilities2: int = 0
    control_count: int = 0
    input_count: int = 0
    device_name: str = ""
    strips: list[StripInfo] = field(default_factory=list)

    @property
    def protocol_version(self) -> str:
        return f"{self.protocol_major}.{self.protocol_minor}"

    @property
    def firmware_version(self) -> str:
        return f"{self.firmware_major}.{self.firmware_minor}"

    @property
    def has_brightness(self) -> bool:
        return bool(self.capabilities1 & 0x01)

    @property
    def has_gamma(self) -> bool:
        return bool(self.capabilities1 & 0x02)

    @property
    def has_rle(self) -> bool:
        return bool(self.capabilities1 & 0x04)

    @property
    def has_temp_sensor(self) -> bool:
        return bool(self.capabilities1 & 0x10)

    @property
    def has_inputs(self) -> bool:
        return bool(self.capabilities1 & CAPS_EXTENDED) and bool(self.capabilities2 & 0x20)

    @property
    def is_usb_highspeed(self) -> bool:
        return bool(self.capabilities1 & CAPS_EXTENDED) and bool(self.capabilities2 & 0x08)


@dataclass
class DeviceStatus:
    """Current device status."""

    state: int = 0  # 0=idle, 1=running, 2=error
    brightness: int = 0
    temperature: Optional[float] = None  # Celsius
    voltage: Optional[float] = None  # Volts
    error_code: int = 0

    @property
    def state_name(self) -> str:
        return {0: "idle", 1: "running", 2: "error"}.get(self.state, "unknown")


@dataclass
class DeviceStats:
    """Device statistics."""

    frames_received: int = 0
    frames_displayed: int = 0
    bytes_received: int = 0
    checksum_errors: int = 0
    buffer_overflows: int = 0
    uptime_seconds: int = 0


# Type alias for input event callback
InputEventCallback = Callable[[int, int, int, bytes], None]


class LtpDevice:
    """
    High-level interface for LTP Serial Protocol v2 devices.

    Example:
        device = LtpDevice('/dev/ttyUSB0')
        device.connect()

        print(device.info)

        device.fill(255, 0, 0)
        device.show()

        device.close()
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 1.0,
    ):
        """
        Initialize device connection.

        Args:
            port: Serial port path (e.g., '/dev/ttyUSB0', 'COM3')
            baudrate: Baud rate (default 115200)
            timeout: Response timeout in seconds
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

        self._serial: Optional[serial.Serial] = None
        self._protocol = LtpProtocol()
        self._info: Optional[DeviceInfo] = None
        self._frame_number = 0

        # For async input events
        self._input_callback: Optional[InputEventCallback] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_reader = threading.Event()
        self._response_queue: list[LtpPacket] = []
        self._response_lock = threading.Lock()
        self._response_event = threading.Event()

    @property
    def info(self) -> Optional[DeviceInfo]:
        """Device information (populated after connect)."""
        return self._info

    @property
    def is_connected(self) -> bool:
        """Check if device is connected."""
        return self._serial is not None and self._serial.is_open

    @property
    def pixel_count(self) -> int:
        """Total pixel count."""
        return self._info.total_pixels if self._info else 0

    def connect(self, wait_for_hello: bool = True) -> DeviceInfo:
        """
        Connect to the device.

        Args:
            wait_for_hello: Wait for HELLO packet from device

        Returns:
            DeviceInfo from the device

        Raises:
            LtpConnectionError: Failed to open serial port
            LtpTimeoutError: No response from device
        """
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=0.1,  # Short timeout for non-blocking reads
            )
        except serial.SerialException as e:
            raise LtpConnectionError(f"Failed to open {self.port}: {e}") from e

        # Clear any pending data
        self._serial.reset_input_buffer()
        self._protocol.reset()

        # Start reader thread
        self._stop_reader.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        if wait_for_hello:
            # Wait for HELLO or request it
            try:
                packet = self._wait_for_response(CMD_HELLO, timeout=2.0)
                self._info = self._parse_hello(packet)
            except LtpTimeoutError:
                # Send GET_INFO to request device info
                self._send(LtpProtocol.build_get_info(INFO_ALL))
                packet = self._wait_for_response(CMD_INFO_RESPONSE)
                self._info = self._parse_info_response(packet)

            # Get strip info
            if self._info and self._info.strip_count > 0:
                self._send(LtpProtocol.build_get_info(INFO_STRIPS))
                try:
                    packet = self._wait_for_response(CMD_INFO_RESPONSE)
                    self._info.strips = self._parse_strips_response(packet)
                except LtpTimeoutError:
                    pass  # Strip info optional

        return self._info

    def close(self):
        """Close the device connection."""
        self._stop_reader.set()
        if self._reader_thread:
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

        if self._serial:
            self._serial.close()
            self._serial = None

        self._info = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # =========================================================================
    # Pixel Commands
    # =========================================================================

    def fill(self, r: int, g: int, b: int, strip_id: int = STRIP_ALL):
        """
        Fill all pixels with a solid color.

        Args:
            r, g, b: Color values (0-255)
            strip_id: Strip ID (default: all strips)
        """
        self._send(LtpProtocol.build_pixel_set_all(r, g, b, strip_id))

    def fill_range(
        self, start: int, end: int, r: int, g: int, b: int, strip_id: int = 0
    ):
        """
        Fill a range of pixels with a solid color.

        Args:
            start: Start index (inclusive)
            end: End index (exclusive)
            r, g, b: Color values (0-255)
            strip_id: Strip ID
        """
        self._send(LtpProtocol.build_pixel_set_range(strip_id, start, end, r, g, b))

    def set_pixels(self, pixel_data: bytes, start: int = 0, strip_id: int = 0):
        """
        Set pixel data from raw bytes.

        Args:
            pixel_data: RGB data (3 bytes per pixel)
            start: Starting pixel index
            strip_id: Strip ID
        """
        self._send(LtpProtocol.build_pixel_frame(strip_id, start, pixel_data))

    def set_pixel(self, index: int, r: int, g: int, b: int, strip_id: int = 0):
        """
        Set a single pixel.

        Args:
            index: Pixel index
            r, g, b: Color values (0-255)
            strip_id: Strip ID
        """
        self.fill_range(index, index + 1, r, g, b, strip_id)

    def clear(self, strip_id: int = STRIP_ALL):
        """Clear all pixels (set to black)."""
        self.fill(0, 0, 0, strip_id)

    def show(self, wait_for_ack: bool = False) -> Optional[int]:
        """
        Display the current pixel buffer.

        Args:
            wait_for_ack: Wait for frame acknowledgment (if enabled on device)

        Returns:
            Frame number if wait_for_ack, else None
        """
        self._frame_number = (self._frame_number + 1) & 0xFFFF
        self._send(LtpProtocol.build_show(self._frame_number))

        if wait_for_ack:
            try:
                packet = self._wait_for_response(CMD_FRAME_ACK, timeout=0.5)
                if len(packet.payload) >= 2:
                    return struct.unpack("<H", packet.payload[:2])[0]
            except LtpTimeoutError:
                pass

        return None

    # =========================================================================
    # Control Commands
    # =========================================================================

    def set_brightness(self, brightness: int):
        """Set global brightness (0-255)."""
        self._send(LtpProtocol.build_set_control_uint8(CTRL_ID_BRIGHTNESS, brightness))

    def set_gamma(self, gamma: float):
        """Set gamma correction (1.0-3.0, stored as value * 10)."""
        value = int(gamma * 10)
        self._send(LtpProtocol.build_set_control_uint8(CTRL_ID_GAMMA, value))

    def set_auto_show(self, enabled: bool):
        """Enable/disable auto-show after PIXEL_FRAME."""
        self._send(LtpProtocol.build_set_control_bool(CTRL_ID_AUTO_SHOW, enabled))

    def set_frame_ack(self, enabled: bool):
        """Enable/disable frame acknowledgment."""
        self._send(LtpProtocol.build_set_control_bool(CTRL_ID_FRAME_ACK, enabled))

    def set_control(self, control_id: int, value: int):
        """Set a control value (generic UINT8)."""
        self._send(LtpProtocol.build_set_control_uint8(control_id, value))

    def get_control(self, control_id: int) -> int:
        """
        Get a control value.

        Returns:
            Control value (interpretation depends on control type)
        """
        self._send(LtpProtocol.build_get_control(control_id))
        packet = self._wait_for_response(CMD_CONTROL_RESPONSE)
        if len(packet.payload) >= 2:
            return packet.payload[1]
        return 0

    # =========================================================================
    # Query Commands
    # =========================================================================

    def get_status(self) -> DeviceStatus:
        """Get current device status."""
        self._send(LtpProtocol.build_get_info(INFO_STATUS))
        packet = self._wait_for_response(CMD_INFO_RESPONSE)
        return self._parse_status_response(packet)

    def get_stats(self) -> DeviceStats:
        """Get device statistics."""
        self._send(LtpProtocol.build_get_info(INFO_STATS))
        packet = self._wait_for_response(CMD_INFO_RESPONSE)
        return self._parse_stats_response(packet)

    def get_pixels(
        self, start: int = 0, count: int = 0, strip_id: int = 0
    ) -> bytes:
        """
        Read pixel values from device.

        Args:
            start: Start index
            count: Number of pixels (0 = all remaining)
            strip_id: Strip ID

        Returns:
            Raw pixel data (3 bytes per pixel)
        """
        self._send(LtpProtocol.build_get_pixels(strip_id, start, count))
        packet = self._wait_for_response(CMD_PIXEL_RESPONSE)
        if len(packet.payload) > 5:
            return packet.payload[5:]
        return b""

    def ping(self) -> bool:
        """
        Send a ping (NOP with ACK request).

        Returns:
            True if device responded
        """
        self._send(LtpProtocol.build_nop(ack_request=True))
        try:
            self._wait_for_response(CMD_ACK, timeout=0.5)
            return True
        except LtpTimeoutError:
            return False

    def reset_device(self):
        """Request device reset."""
        self._send(LtpProtocol.build_reset())

    # =========================================================================
    # Input Event Handling
    # =========================================================================

    def set_input_callback(self, callback: Optional[InputEventCallback]):
        """
        Set callback for input events.

        Callback signature: callback(input_id, input_type, timestamp, data)
        """
        self._input_callback = callback

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _send(self, packet: bytes):
        """Send a packet to the device."""
        if not self._serial:
            raise LtpConnectionError("Not connected")
        self._serial.write(packet)

    def _reader_loop(self):
        """Background thread for reading responses."""
        while not self._stop_reader.is_set():
            if not self._serial or not self._serial.is_open:
                break

            try:
                data = self._serial.read(256)
                if data:
                    packets = self._protocol.feed(data)
                    for packet in packets:
                        self._handle_packet(packet)
            except serial.SerialException:
                break

    def _handle_packet(self, packet: LtpPacket):
        """Handle a received packet."""
        # Handle async events
        if packet.cmd == CMD_INPUT_EVENT:
            if self._input_callback and len(packet.payload) >= 4:
                input_id = packet.payload[0]
                input_type = packet.payload[1]
                timestamp = struct.unpack("<H", packet.payload[2:4])[0]
                data = packet.payload[4:]
                self._input_callback(input_id, input_type, timestamp, data)
            return

        if packet.cmd == CMD_STATUS_UPDATE:
            # Could emit an event here
            return

        # Queue response for synchronous handlers
        with self._response_lock:
            self._response_queue.append(packet)
            self._response_event.set()

    def _wait_for_response(
        self, expected_cmd: int, timeout: Optional[float] = None
    ) -> LtpPacket:
        """Wait for a specific response packet."""
        timeout = timeout or self.timeout
        deadline = time.time() + timeout

        while time.time() < deadline:
            with self._response_lock:
                for i, packet in enumerate(self._response_queue):
                    # Check for NAK
                    if packet.cmd == CMD_NAK:
                        self._response_queue.pop(i)
                        error_code = packet.payload[1] if len(packet.payload) > 1 else 0
                        cmd = packet.payload[0] if len(packet.payload) > 0 else 0
                        raise LtpDeviceError(error_code, cmd)

                    # Check for expected response
                    if packet.cmd == expected_cmd:
                        self._response_queue.pop(i)
                        return packet

                self._response_event.clear()

            # Wait for new packets
            self._response_event.wait(timeout=0.1)

        raise LtpTimeoutError(f"Timeout waiting for response 0x{expected_cmd:02X}")

    def _parse_hello(self, packet: LtpPacket) -> DeviceInfo:
        """Parse HELLO packet into DeviceInfo."""
        p = packet.payload
        if len(p) < 10:
            raise LtpProtocolError("HELLO payload too short")

        info = DeviceInfo(
            protocol_major=p[0],
            protocol_minor=p[1],
            firmware_major=(p[2] >> 4) & 0x0F,
            firmware_minor=p[2] & 0x0F,
            strip_count=p[4],
            total_pixels=struct.unpack("<H", p[5:7])[0],
            color_format=p[7],
            capabilities1=p[8],
        )

        offset = 9
        if info.capabilities1 & CAPS_EXTENDED and len(p) > offset:
            info.capabilities2 = p[offset]
            offset += 1

        if len(p) > offset:
            info.control_count = p[offset]
            offset += 1

        if len(p) > offset:
            info.input_count = p[offset]
            offset += 1

        return info

    def _parse_info_response(self, packet: LtpPacket) -> DeviceInfo:
        """Parse INFO_RESPONSE (ALL type) into DeviceInfo."""
        return self._parse_hello(packet)  # Same format

    def _parse_strips_response(self, packet: LtpPacket) -> list[StripInfo]:
        """Parse strips from INFO_RESPONSE."""
        p = packet.payload
        if len(p) < 1:
            return []

        strip_count = p[0]
        strips = []
        offset = 1

        for _ in range(strip_count):
            if offset + 8 > len(p):
                break
            strip = StripInfo(
                strip_id=p[offset],
                pixel_count=struct.unpack("<H", p[offset + 1 : offset + 3])[0],
                color_format=p[offset + 3],
                led_type=p[offset + 4],
                data_pin=p[offset + 5],
                clock_pin=p[offset + 6],
                flags=p[offset + 7],
            )
            strips.append(strip)
            offset += 8

        return strips

    def _parse_status_response(self, packet: LtpPacket) -> DeviceStatus:
        """Parse status from INFO_RESPONSE."""
        p = packet.payload
        if len(p) < 7:
            return DeviceStatus()

        status = DeviceStatus(
            state=p[0],
            brightness=p[1],
            error_code=p[6],
        )

        # Temperature (signed, °C × 10)
        temp_raw = struct.unpack("<h", p[2:4])[0]
        if temp_raw != 0x7FFF:
            status.temperature = temp_raw / 10.0

        # Voltage (mV)
        voltage_raw = struct.unpack("<H", p[4:6])[0]
        if voltage_raw != 0xFFFF:
            status.voltage = voltage_raw / 1000.0

        return status

    def _parse_stats_response(self, packet: LtpPacket) -> DeviceStats:
        """Parse stats from INFO_RESPONSE."""
        p = packet.payload
        if len(p) < 20:
            return DeviceStats()

        return DeviceStats(
            frames_received=struct.unpack("<I", p[0:4])[0],
            frames_displayed=struct.unpack("<I", p[4:8])[0],
            bytes_received=struct.unpack("<I", p[8:12])[0],
            checksum_errors=struct.unpack("<H", p[12:14])[0],
            buffer_overflows=struct.unpack("<H", p[14:16])[0],
            uptime_seconds=struct.unpack("<I", p[16:20])[0],
        )
