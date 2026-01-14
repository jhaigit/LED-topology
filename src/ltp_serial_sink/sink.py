"""LTP Serial Sink - receives LED data via LTP protocol and outputs to serial."""

import asyncio
import logging
import threading
from typing import Any
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
from ltp_serial_sink.serial_renderer import SerialConfig, SerialRenderer

logger = logging.getLogger(__name__)


class SerialSinkConfig(BaseModel):
    """Configuration for serial LTP sink."""

    # Device identity
    device_id: UUID = Field(default_factory=uuid4)
    name: str = "Serial LED Strip"
    description: str = ""

    # Display configuration
    device_type: DeviceType = DeviceType.STRING
    pixels: int = 160
    dimensions: list[int] = Field(default_factory=lambda: [160])
    color_format: ColorFormat = ColorFormat.RGB
    max_refresh_hz: int = 30

    # Network
    control_port: int = 0  # 0 = auto
    data_port: int = 0

    # Serial settings
    port: str = ""
    baud: int = 38400
    timeout: float = 1.0
    write_timeout: float = 1.0

    # Protocol settings
    hex_format: str = "0x"  # "0x" or "#"
    line_ending: str = "\n"  # "\n", "\r", or "\r\n"
    command_delay: float = 0.001
    frame_delay: float = 0.0

    # Optimization
    change_detection: bool = True
    run_length: bool = True
    max_commands_per_frame: int = 100


class SerialSink:
    """LTP Sink with serial output backend."""

    def __init__(self, config: SerialSinkConfig | None = None):
        self.config = config or SerialSinkConfig()

        # Initialize topology
        if len(self.config.dimensions) == 1:
            self._topology = create_linear_topology(self.config.dimensions[0])
        else:
            from libltp import create_matrix_topology

            self._topology = create_matrix_topology(
                self.config.dimensions[0], self.config.dimensions[1]
            )

        # Initialize controls
        self._controls = ControlRegistry()
        self._setup_controls()

        # Network components
        self._advertiser: SinkAdvertiser | None = None
        self._control_server: ControlServer | None = None
        self._data_receiver: DataReceiver | None = None
        self._stream_manager = StreamManager()

        # Serial renderer
        serial_config = SerialConfig(
            port=self.config.port,
            baud=self.config.baud,
            timeout=self.config.timeout,
            write_timeout=self.config.write_timeout,
            hex_format=self.config.hex_format,
            line_ending=self.config.line_ending,
            command_delay=self.config.command_delay,
            frame_delay=self.config.frame_delay,
            change_detection=self.config.change_detection,
            run_length=self.config.run_length,
            max_commands_per_frame=self.config.max_commands_per_frame,
        )
        self._renderer = SerialRenderer(serial_config)

        # State
        self._running = False
        self._pixel_buffer = np.zeros(
            (self.config.pixels, self.config.color_format.bytes_per_pixel),
            dtype=np.uint8,
        )
        self._reconnect_task: asyncio.Task | None = None
        self._stats_task: asyncio.Task | None = None

        # Serial render thread with frame dropping
        # Uses a single-slot buffer - new frames replace pending ones
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

    def _setup_controls(self) -> None:
        """Set up device controls."""
        self._controls.register(
            NumberControl(
                id="brightness",
                name="Brightness",
                description="Master brightness (applied before serial output)",
                value=1.0,
                min=0.0,
                max=1.0,
                step=0.05,
                group="output",
            )
        )
        self._controls.register(
            NumberControl(
                id="gamma",
                name="Gamma Correction",
                description="Gamma value for color correction",
                value=2.2,
                min=1.0,
                max=3.0,
                step=0.1,
                group="output",
            )
        )
        self._controls.register(
            BooleanControl(
                id="test_mode",
                name="Test Mode",
                description="Display test pattern instead of input",
                value=False,
                group="general",
            )
        )

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

        mapper = TopologyMapper(self._topology)

        device_info = {
            "id": str(self.config.device_id),
            "name": self.config.name,
            "description": self.config.description,
            "type": self.config.device_type.value,
            "pixels": self.config.pixels,
            "dimensions": self.config.dimensions,
            "topology": mapper.to_dict(),
            "color_formats": [self.config.color_format.name.lower()],
            "max_refresh_hz": self.config.max_refresh_hz,
            "protocol_version": "0.1",
            "controls": self._controls.to_list(),
            "backend": {
                "type": "serial",
                "port": self.config.port,
                "baud": self.config.baud,
            },
        }
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
            # Clear renderer state so next data is treated as new
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
        applied, errors = self._controls.set_values(values)

        status = "ok" if not errors else "partial"
        return control_set_response(message.seq, status, applied, errors or None)

    def _apply_gamma(self, pixels: np.ndarray, gamma: float) -> np.ndarray:
        """Apply gamma correction to pixels."""
        if gamma == 1.0:
            return pixels
        # Normalize, apply gamma, denormalize
        normalized = pixels.astype(np.float32) / 255.0
        corrected = np.power(normalized, gamma)
        return (corrected * 255.0).astype(np.uint8)

    def _handle_data_packet(self, packet: DataPacket) -> None:
        """Handle incoming data packet.

        This is called from the UDP receiver in the event loop.
        We offload the blocking serial I/O to a thread pool to avoid
        blocking the event loop and causing health check failures.
        """
        # Only process data if there's an active stream
        if not self._stream_manager.active_streams:
            logger.debug("Ignoring data packet - no active streams")
            return

        # Update packet statistics
        pixel_count = packet.pixel_count
        packet_bytes = len(packet.pixel_data) * packet.pixel_data.itemsize if hasattr(packet.pixel_data, 'itemsize') else len(packet.pixel_data)
        self._packet_count += 1
        self._packet_bytes += packet_bytes

        # Debug log each packet
        logger.debug(
            f"Data packet #{self._packet_count}: {pixel_count} pixels, "
            f"{packet_bytes} bytes, format={packet.color_format.name}"
        )

        # Get control values
        brightness = self._controls.get_value("brightness")
        gamma = self._controls.get_value("gamma")
        test_mode = self._controls.get_value("test_mode")

        # Store pixel data
        if len(packet.pixel_data) <= len(self._pixel_buffer):
            self._pixel_buffer[: len(packet.pixel_data)] = packet.pixel_data

        # Check test mode
        if test_mode:
            display_pixels = self._generate_test_pattern()
        else:
            display_pixels = self._pixel_buffer.copy()

        # Apply gamma correction
        display_pixels = self._apply_gamma(display_pixels, gamma)

        # Apply brightness (as float multiplier)
        display_pixels = (display_pixels * brightness).astype(np.uint8)

        # Submit frame to render thread (with frame dropping)
        self._submit_frame(display_pixels)

    def _submit_frame(self, pixels: np.ndarray) -> None:
        """Submit a frame to the render thread.

        Uses a single-slot buffer with frame dropping. If a frame is already
        pending when a new one arrives, the old frame is dropped and replaced.
        This ensures we always render the most recent data and don't build up
        a backlog when serial can't keep up with incoming data rate.
        """
        with self._render_lock:
            if self._pending_frame is not None:
                # Frame was waiting - it's being dropped
                self._frames_dropped += 1
                logger.debug(f"Dropping frame (serial backlog), total dropped: {self._frames_dropped}")

            self._pending_frame = pixels.copy()
            self._render_event.set()

    def _render_loop(self) -> None:
        """Render thread main loop.

        Waits for frames and renders them to serial. Only processes the latest
        frame if multiple arrive while rendering.
        """
        logger.info("Serial render thread started")

        while self._render_running:
            # Wait for a frame to be available
            if not self._render_event.wait(timeout=0.5):
                continue

            # Get the pending frame (and clear it)
            with self._render_lock:
                frame = self._pending_frame
                self._pending_frame = None
                self._render_event.clear()

            if frame is None:
                continue

            # Render the frame
            if self._renderer.is_connected():
                try:
                    commands_sent = self._renderer.render(frame)
                    self._frames_rendered += 1
                    logger.debug(f"Rendered frame {self._frames_rendered}, {commands_sent} commands sent")
                except Exception as e:
                    logger.error(f"Error rendering frame: {e}")

        logger.info("Serial render thread stopped")

    def _generate_test_pattern(self) -> np.ndarray:
        """Generate RGB sweep test pattern."""
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

        while self._running:
            if not self._renderer.is_connected():
                try:
                    self._renderer.open()
                    logger.info(f"Serial port {self.config.port} connected")
                    reconnect_delay = 1.0  # Reset delay on success
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
        stats_interval = 5.0  # Log stats every 5 seconds

        while self._running:
            await asyncio.sleep(stats_interval)

            now = time.time()
            elapsed = now - self._last_stats_time

            if elapsed > 0:
                packets_delta = self._packet_count - self._last_stats_packets
                bytes_delta = self._packet_bytes - self._last_stats_bytes

                packets_per_sec = packets_delta / elapsed
                bytes_per_sec = bytes_delta / elapsed

                # Format data rate
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

        logger.info(f"Starting serial sink: {self.config.name}")

        # Open serial port
        if self.config.port:
            try:
                self._renderer.open()
                logger.info(f"Serial port {self.config.port} opened")
            except Exception as e:
                logger.warning(f"Could not open serial port: {e}")
                logger.info("Will retry in background...")

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
            pixels=self.config.pixels,
            dimensions=self.config.dimensions,
            color_format=self.config.color_format,
            max_rate=self.config.max_refresh_hz,
            has_controls=True,
        )
        await self._advertiser.start()

        self._running = True

        # Start render thread for serial output
        self._render_running = True
        self._render_thread = threading.Thread(
            target=self._render_loop,
            name="serial-render",
            daemon=True,
        )
        self._render_thread.start()

        # Start serial monitor for reconnection
        self._reconnect_task = asyncio.create_task(self._serial_monitor())

        # Start stats monitor for data packet logging
        self._stats_task = asyncio.create_task(self._stats_monitor())

        logger.info(
            f"Serial sink started - Control: {self._control_server.actual_port}, "
            f"Data: {self._data_receiver.actual_port}, "
            f"Serial: {self.config.port}"
        )

    async def stop(self) -> None:
        """Stop the serial sink."""
        if not self._running:
            return

        logger.info("Stopping serial sink")
        self._running = False

        # Cancel reconnect task
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        # Cancel stats task
        if self._stats_task:
            self._stats_task.cancel()
            try:
                await self._stats_task
            except asyncio.CancelledError:
                pass
            self._stats_task = None

        # Stop render thread
        self._render_running = False
        self._render_event.set()  # Wake up the thread if it's waiting
        if self._render_thread:
            self._render_thread.join(timeout=2.0)
            self._render_thread = None

        # Log final stats
        logger.info(
            f"Final data stats: {self._packet_count} packets, {self._packet_bytes} bytes received, "
            f"{self._frames_rendered} frames rendered, {self._frames_dropped} frames dropped"
        )

        # Close serial port
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

    def get_stats(self) -> dict[str, Any]:
        """Get sink statistics."""
        return {
            "running": self._running,
            "serial": self._renderer.get_stats(),
            "control_port": self.control_port,
            "data_port": self.data_port,
            "packets_received": self._packet_count,
            "bytes_received": self._packet_bytes,
            "frames_rendered": self._frames_rendered,
            "frames_dropped": self._frames_dropped,
        }
