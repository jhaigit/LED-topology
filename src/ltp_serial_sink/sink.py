"""LTP Serial Sink - receives LED data via LTP protocol and outputs to serial.

Uses LTP Serial Protocol v2 for communication with the microcontroller.
"""

import asyncio
import logging
import sys
import threading
from typing import Any, TextIO
from uuid import UUID, uuid4

import numpy as np
from pydantic import BaseModel, Field

from libltp import (
    BooleanControl,
    ColorFormat,
    ControlRegistry,
    DataPacket,
    DeviceType,
    Message,
    MessageType,
    NumberControl,
    SinkAdvertiser,
    capability_response,
    control_get_response,
    control_set_response,
    create_linear_topology,
    stream_setup_response,
)
from libltp.types import StreamAction
from libltp.transport import ControlServer, DataReceiver, StreamManager

from ltp_serial_sink.v2_renderer import V2Renderer, V2RendererConfig
from ltp_serial_cli.protocol import CTRL_ID_BRIGHTNESS, CTRL_ID_GAMMA

logger = logging.getLogger(__name__)


class SerialSinkConfig(BaseModel):
    """Configuration for serial LTP sink using v2 protocol."""

    # Device identity
    device_id: UUID = Field(default_factory=uuid4)
    name: str = "Serial LED Strip"
    description: str = ""

    # Display configuration
    device_type: DeviceType = DeviceType.STRING
    pixels: int = 0  # 0 = auto-detect from device
    dimensions: list[int] = Field(default_factory=list)  # Empty = auto-detect
    color_format: ColorFormat = ColorFormat.RGB
    max_refresh_hz: int = 60

    # Network
    control_port: int = 0  # 0 = auto
    data_port: int = 0

    # Serial settings (v2 protocol)
    port: str = ""
    baudrate: int = 115200
    timeout: float = 2.0

    # Debug options
    debug: bool = False  # Show packets sent/received
    debug_file: TextIO | None = None

    model_config = {"arbitrary_types_allowed": True}


class SerialSink:
    """LTP Sink with serial output backend using v2 protocol.

    This sink:
    - Receives LED data via LTP network protocol
    - Outputs to a microcontroller using LTP Serial Protocol v2
    - Exposes device controls (brightness, gamma) via the network protocol
    """

    def __init__(self, config: SerialSinkConfig | None = None):
        self.config = config or SerialSinkConfig()

        # Network components
        self._advertiser: SinkAdvertiser | None = None
        self._control_server: ControlServer | None = None
        self._data_receiver: DataReceiver | None = None
        self._stream_manager = StreamManager()

        # Serial renderer (v2 protocol)
        renderer_config = V2RendererConfig(
            port=self.config.port,
            baudrate=self.config.baudrate,
            timeout=self.config.timeout,
            debug=self.config.debug,
            debug_file=self.config.debug_file or sys.stderr,
            auto_show=True,
        )
        self._renderer = V2Renderer(renderer_config)

        # Controls registry - will be populated after device connection
        self._controls = ControlRegistry()
        self._setup_local_controls()

        # Actual pixel count (may be updated from device)
        self._pixel_count = self.config.pixels
        self._dimensions = self.config.dimensions.copy() if self.config.dimensions else []
        self._topology = None

        # State
        self._running = False
        self._pixel_buffer: np.ndarray | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._stats_task: asyncio.Task | None = None

        # Serial render thread with frame dropping
        self._render_thread: threading.Thread | None = None
        self._render_lock = threading.Lock()
        self._render_event = threading.Event()
        self._pending_frame: np.ndarray | None = None
        self._render_running = False

        # Data packet statistics
        self._packet_count = 0
        self._packet_bytes = 0
        self._frames_dropped = 0
        self._frames_rendered = 0
        self._last_stats_time = 0.0
        self._last_stats_packets = 0
        self._last_stats_bytes = 0

    def _setup_local_controls(self) -> None:
        """Set up local controls (not from device)."""
        self._controls.register(
            BooleanControl(
                id="test_mode",
                name="Test Mode",
                description="Display test pattern instead of input",
                value=False,
                group="general",
            )
        )

    def _setup_device_controls(self) -> None:
        """Set up controls based on connected device capabilities."""
        device_info = self._renderer.device_info
        if not device_info:
            return

        # Add brightness control if device supports it
        if device_info.has_brightness:
            # Get current value from device
            current_brightness = self._renderer.get_control(CTRL_ID_BRIGHTNESS)
            if current_brightness is None:
                current_brightness = 255

            self._controls.register(
                NumberControl(
                    id="hw_brightness",
                    name="Hardware Brightness",
                    description="LED controller brightness (hardware)",
                    value=float(current_brightness),
                    min=0.0,
                    max=255.0,
                    step=1.0,
                    group="hardware",
                )
            )

        # Add gamma control if device supports it
        if device_info.has_gamma:
            current_gamma = self._renderer.get_control(CTRL_ID_GAMMA)
            if current_gamma is None:
                current_gamma = 22  # 2.2 * 10

            self._controls.register(
                NumberControl(
                    id="hw_gamma",
                    name="Hardware Gamma",
                    description="LED controller gamma correction (hardware)",
                    value=float(current_gamma) / 10.0,
                    min=1.0,
                    max=3.0,
                    step=0.1,
                    group="hardware",
                )
            )

        logger.info(f"Device controls registered: {[c.id for c in self._controls._controls.values()]}")

    def _update_from_device(self) -> None:
        """Update configuration from connected device."""
        device_info = self._renderer.device_info
        if not device_info:
            return

        # Update pixel count if not specified in config
        if self.config.pixels == 0:
            self._pixel_count = device_info.total_pixels
            logger.info(f"Auto-detected {self._pixel_count} pixels from device")
        else:
            self._pixel_count = self.config.pixels

        # Update dimensions
        if not self._dimensions:
            self._dimensions = [self._pixel_count]

        # Create topology
        if len(self._dimensions) == 1:
            self._topology = create_linear_topology(self._dimensions[0])
        else:
            from libltp import create_matrix_topology
            self._topology = create_matrix_topology(
                self._dimensions[0], self._dimensions[1]
            )

        # Initialize pixel buffer
        self._pixel_buffer = np.zeros(
            (self._pixel_count, self.config.color_format.bytes_per_pixel),
            dtype=np.uint8,
        )

        # Setup device controls
        self._setup_device_controls()

    def _handle_message(self, message: Message) -> Message | None:
        """Handle incoming control channel messages."""
        logger.debug(f"Handling message: {message.type}")

        if message.type == MessageType.CAPABILITY_REQUEST:
            return self._handle_capability_request(message)
        elif message.type == MessageType.STREAM_SETUP:
            return self._handle_stream_setup(message)
        elif message.type == MessageType.STREAM_CONTROL:
            return self._handle_stream_control(message)
        elif message.type == MessageType.CONTROL_GET:
            return self._handle_control_get(message)
        elif message.type == MessageType.CONTROL_SET:
            return self._handle_control_set(message)

        return None

    def _handle_capability_request(self, message: Message) -> Message:
        """Handle capability request."""
        from libltp.topology import TopologyMapper

        # Use topology if available
        topology_dict = {}
        if self._topology:
            mapper = TopologyMapper(self._topology)
            topology_dict = mapper.to_dict()

        device_info = {
            "id": str(self.config.device_id),
            "name": self.config.name,
            "description": self.config.description,
            "type": self.config.device_type.value,
            "pixels": self._pixel_count,
            "dimensions": self._dimensions or [self._pixel_count],
            "topology": topology_dict,
            "color_formats": [self.config.color_format.name.lower()],
            "max_refresh_hz": self.config.max_refresh_hz,
            "protocol_version": "0.1",
            "controls": self._controls.to_list(),
            "backend": {
                "type": "serial_v2",
                "port": self.config.port,
                "baudrate": self.config.baudrate,
                "connected": self._renderer.is_connected(),
            },
        }

        # Add device info if connected
        if self._renderer.device_info:
            device_info["backend"]["firmware"] = self._renderer.device_info.firmware_version
            device_info["backend"]["device_name"] = self._renderer.device_info.device_name

        return capability_response(message.seq, device_info)

    def _handle_stream_setup(self, message: Message) -> Message:
        """Handle stream setup request."""
        if self._data_receiver is None:
            logger.warning("Data receiver not initialized")

        # Create stream
        stream_id = self._stream_manager.create_stream(
            receiver=self._data_receiver,
            color_format=self.config.color_format,
        )
        self._stream_manager.start_stream(stream_id)

        return stream_setup_response(
            message.seq,
            status="ok",
            udp_port=self._data_receiver.actual_port if self._data_receiver else 0,
            stream_id=stream_id,
        )

    def _handle_stream_control(self, message: Message) -> Message:
        """Handle stream control request (start/stop/pause)."""
        stream_id = message.data.get("stream_id")
        action_str = message.data.get("action", "start")
        logger.info(f"STREAM_CONTROL received: stream_id={stream_id}, action={action_str}")
        action = StreamAction(action_str)

        if action == StreamAction.START:
            self._stream_manager.start_stream(stream_id)
            logger.info(f"Started stream: {stream_id}")
        elif action == StreamAction.STOP:
            logger.info(f"Processing STOP for stream {stream_id}")
            self._stream_manager.stop_stream(stream_id)
            self._renderer.clear()
            logger.info(f"Stopped stream: {stream_id}, renderer cleared")
        elif action == StreamAction.PAUSE:
            logger.info(f"Paused stream: {stream_id}")

        return Message(
            MessageType.STREAM_CONTROL_RESPONSE,
            message.seq,
            status="ok",
            stream_id=stream_id,
        )

    def _handle_control_get(self, message: Message) -> Message:
        """Handle control get request."""
        ids = message.data.get("ids")
        values = self._controls.get_values(ids)
        return control_get_response(message.seq, "ok", values)

    def _handle_control_set(self, message: Message) -> Message:
        """Handle control set request."""
        values = message.data.get("values", {})
        applied = {}
        errors = {}

        for control_id, value in values.items():
            # Handle hardware controls specially - forward to device
            if control_id == "hw_brightness":
                try:
                    int_value = int(value)
                    if self._renderer.set_brightness(int_value):
                        self._controls.set_value(control_id, float(int_value))
                        applied[control_id] = float(int_value)
                    else:
                        errors[control_id] = "Failed to set on device"
                except Exception as e:
                    errors[control_id] = str(e)

            elif control_id == "hw_gamma":
                try:
                    float_value = float(value)
                    if self._renderer.set_gamma(float_value):
                        self._controls.set_value(control_id, float_value)
                        applied[control_id] = float_value
                    else:
                        errors[control_id] = "Failed to set on device"
                except Exception as e:
                    errors[control_id] = str(e)

            else:
                # Local control
                try:
                    self._controls.set_value(control_id, value)
                    applied[control_id] = self._controls.get_value(control_id)
                except Exception as e:
                    errors[control_id] = str(e)

        status = "ok" if not errors else "partial"
        return control_set_response(message.seq, status, applied, errors or None)

    def _handle_data_packet(self, packet: DataPacket) -> None:
        """Handle incoming data packet."""
        # Only process data if there's an active stream
        if not self._stream_manager.active_streams:
            logger.debug("Ignoring data packet - no active streams")
            return

        if self._pixel_buffer is None:
            logger.warning("Pixel buffer not initialized")
            return

        # Update packet statistics
        pixel_count = packet.pixel_count
        packet_bytes = len(packet.pixel_data) * packet.pixel_data.itemsize if hasattr(packet.pixel_data, 'itemsize') else len(packet.pixel_data)
        self._packet_count += 1
        self._packet_bytes += packet_bytes

        logger.debug(
            f"Data packet #{self._packet_count}: {pixel_count} pixels, "
            f"{packet_bytes} bytes, format={packet.color_format.name}"
        )

        # Get control values
        test_mode = self._controls.get_value("test_mode")

        # Store pixel data
        if len(packet.pixel_data) <= len(self._pixel_buffer):
            self._pixel_buffer[: len(packet.pixel_data)] = packet.pixel_data

        # Check test mode
        if test_mode:
            display_pixels = self._generate_test_pattern()
        else:
            display_pixels = self._pixel_buffer.copy()

        # Submit frame to render thread (with frame dropping)
        self._submit_frame(display_pixels)

    def _submit_frame(self, pixels: np.ndarray) -> None:
        """Submit a frame to the render thread."""
        with self._render_lock:
            if self._pending_frame is not None:
                self._frames_dropped += 1
                logger.debug(f"Dropping frame (serial backlog), total dropped: {self._frames_dropped}")

            self._pending_frame = pixels.copy()
            self._render_event.set()

    def _render_loop(self) -> None:
        """Render thread main loop."""
        logger.info("Serial render thread started (v2 protocol)")

        while self._render_running:
            if not self._render_event.wait(timeout=0.5):
                continue

            with self._render_lock:
                frame = self._pending_frame
                self._pending_frame = None
                self._render_event.clear()

            if frame is None:
                continue

            if self._renderer.is_connected():
                try:
                    bytes_sent = self._renderer.render(frame)
                    self._frames_rendered += 1
                    logger.debug(f"Rendered frame {self._frames_rendered}, {bytes_sent} bytes sent")
                except Exception as e:
                    logger.error(f"Error rendering frame: {e}")

        logger.info("Serial render thread stopped")

    def _generate_test_pattern(self) -> np.ndarray:
        """Generate RGB sweep test pattern."""
        if self._pixel_buffer is None:
            return np.array([])

        pixels = np.zeros_like(self._pixel_buffer)

        for i in range(len(pixels)):
            phase = (i / len(pixels)) * 3
            if phase < 1:
                pixels[i] = [255, 0, 0]
            elif phase < 2:
                pixels[i] = [0, 255, 0]
            else:
                pixels[i] = [0, 0, 255]

        return pixels

    async def _serial_monitor(self) -> None:
        """Monitor serial connection and reconnect if needed."""
        reconnect_delay = 1.0
        max_delay = 30.0
        was_connected = False

        while self._running:
            if not self._renderer.is_connected():
                try:
                    self._renderer.open()
                    logger.info(f"Serial device connected via v2 protocol")
                    reconnect_delay = 1.0

                    # Update config from device on first connect
                    if not was_connected:
                        self._update_from_device()
                        was_connected = True

                except Exception as e:
                    logger.warning(f"Serial connection failed: {e}")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_delay)
                    continue

            await asyncio.sleep(1.0)

    async def _stats_monitor(self) -> None:
        """Periodically log data packet statistics."""
        import time

        self._last_stats_time = time.time()
        self._last_stats_packets = 0
        self._last_stats_bytes = 0
        stats_interval = 5.0

        while self._running:
            await asyncio.sleep(stats_interval)

            now = time.time()
            elapsed = now - self._last_stats_time

            if elapsed > 0:
                packets_delta = self._packet_count - self._last_stats_packets
                bytes_delta = self._packet_bytes - self._last_stats_bytes

                packets_per_sec = packets_delta / elapsed
                bytes_per_sec = bytes_delta / elapsed

                if bytes_per_sec > 1024 * 1024:
                    rate_str = f"{bytes_per_sec / 1024 / 1024:.2f} MB/s"
                elif bytes_per_sec > 1024:
                    rate_str = f"{bytes_per_sec / 1024:.2f} KB/s"
                else:
                    rate_str = f"{bytes_per_sec:.0f} B/s"

                if packets_delta > 0:
                    drop_rate = (self._frames_dropped / max(self._packet_count, 1)) * 100
                    logger.info(
                        f"Data stats: {packets_per_sec:.1f} packets/s, {rate_str}, "
                        f"rendered: {self._frames_rendered}, dropped: {self._frames_dropped} ({drop_rate:.1f}%)"
                    )

                self._last_stats_time = now
                self._last_stats_packets = self._packet_count
                self._last_stats_bytes = self._packet_bytes

    async def start(self) -> None:
        """Start the serial sink."""
        if self._running:
            return

        logger.info(f"Starting serial sink: {self.config.name} (v2 protocol)")

        # Try to open serial port
        if self.config.port:
            try:
                self._renderer.open()
                logger.info(f"Serial device connected")
                self._update_from_device()
            except Exception as e:
                logger.warning(f"Could not open serial port: {e}")
                logger.info("Will retry in background...")

                # Use configured values if device not connected
                if self.config.pixels > 0:
                    self._pixel_count = self.config.pixels
                    self._dimensions = self.config.dimensions or [self._pixel_count]
                    self._topology = create_linear_topology(self._pixel_count)
                    self._pixel_buffer = np.zeros(
                        (self._pixel_count, self.config.color_format.bytes_per_pixel),
                        dtype=np.uint8,
                    )

        # Ensure we have at least some pixel count configured
        if self._pixel_count == 0:
            self._pixel_count = 160  # Default fallback
            self._dimensions = [160]
            self._topology = create_linear_topology(160)
            self._pixel_buffer = np.zeros((160, 3), dtype=np.uint8)
            logger.warning("No pixel count configured and device not connected, using default: 160")

        # Start control server
        self._control_server = ControlServer(
            port=self.config.control_port, handler=self._handle_message
        )
        await self._control_server.start()

        # Start data receiver
        self._data_receiver = DataReceiver(port=self.config.data_port)
        self._data_receiver.handler = self._handle_data_packet
        await self._data_receiver.start()

        # Start mDNS advertisement
        self._advertiser = SinkAdvertiser(
            name=self.config.name.lower().replace(" ", "-"),
            port=self._control_server.actual_port,
            device_id=self.config.device_id,
            display_name=self.config.name,
            description=self.config.description,
            device_type=self.config.device_type,
            pixels=self._pixel_count,
            dimensions=self._dimensions,
            color_format=self.config.color_format,
            max_rate=self.config.max_refresh_hz,
            has_controls=True,
        )
        await self._advertiser.start()

        self._running = True

        # Start render thread
        self._render_running = True
        self._render_thread = threading.Thread(
            target=self._render_loop,
            name="serial-render-v2",
            daemon=True,
        )
        self._render_thread.start()

        # Start monitors
        self._reconnect_task = asyncio.create_task(self._serial_monitor())
        self._stats_task = asyncio.create_task(self._stats_monitor())

        logger.info(
            f"Serial sink started - Control: {self._control_server.actual_port}, "
            f"Data: {self._data_receiver.actual_port}, "
            f"Serial: {self.config.port} ({self.config.baudrate} baud)"
        )

    async def stop(self) -> None:
        """Stop the serial sink."""
        if not self._running:
            return

        logger.info("Stopping serial sink")
        self._running = False

        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        if self._stats_task:
            self._stats_task.cancel()
            try:
                await self._stats_task
            except asyncio.CancelledError:
                pass
            self._stats_task = None

        self._render_running = False
        self._render_event.set()
        if self._render_thread:
            self._render_thread.join(timeout=2.0)
            self._render_thread = None

        logger.info(
            f"Final stats: {self._packet_count} packets, {self._packet_bytes} bytes received, "
            f"{self._frames_rendered} frames rendered, {self._frames_dropped} frames dropped"
        )

        self._renderer.close()

        if self._advertiser:
            await self._advertiser.stop()

        if self._data_receiver:
            await self._data_receiver.stop()

        if self._control_server:
            await self._control_server.stop()

        logger.info("Serial sink stopped")

    async def run(self) -> None:
        """Run the sink until interrupted."""
        await self.start()
        try:
            while self._running:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def control_port(self) -> int:
        return self._control_server.actual_port if self._control_server else 0

    @property
    def data_port(self) -> int:
        return self._data_receiver.actual_port if self._data_receiver else 0

    @property
    def serial_connected(self) -> bool:
        return self._renderer.is_connected()

    @property
    def pixel_count(self) -> int:
        return self._pixel_count

    def get_stats(self) -> dict[str, Any]:
        """Get sink statistics."""
        return {
            "running": self._running,
            "serial": self._renderer.get_stats(),
            "control_port": self.control_port,
            "data_port": self.data_port,
            "pixels": self._pixel_count,
            "packets_received": self._packet_count,
            "bytes_received": self._packet_bytes,
            "frames_rendered": self._frames_rendered,
            "frames_dropped": self._frames_dropped,
        }
