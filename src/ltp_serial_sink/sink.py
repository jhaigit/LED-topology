"""LTP Serial Sink - receives LED data via LTP protocol and outputs to serial."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
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

        # Thread pool for non-blocking serial I/O
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="serial")
        self._loop: asyncio.AbstractEventLoop | None = None

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
        action = StreamAction(message.data.get("action", "start"))

        if action == StreamAction.START:
            self._stream_manager.start_stream(stream_id)
            logger.info(f"Started stream: {stream_id}")
        elif action == StreamAction.STOP:
            self._stream_manager.stop_stream(stream_id)
            logger.info(f"Stopped stream: {stream_id}")
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

        # Send to serial renderer in thread pool to avoid blocking event loop
        if self._renderer.is_connected():
            self._executor.submit(self._renderer.render, display_pixels)

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

        # Start serial monitor for reconnection
        self._reconnect_task = asyncio.create_task(self._serial_monitor())

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

        # Shutdown thread pool (wait for pending serial writes)
        self._executor.shutdown(wait=True)

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
        }
