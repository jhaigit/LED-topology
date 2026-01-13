"""Core LTP Sink implementation."""

import asyncio
import logging
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
    EnumControl,
    EnumOption,
    Message,
    MessageType,
    NumberControl,
    SinkAdvertiser,
    Topology,
    TopologyMapper,
    capability_response,
    control_get_response,
    control_set_response,
    create_linear_topology,
    stream_setup_response,
)
from libltp.transport import ControlServer, DataReceiver, StreamManager
from ltp_sink.renderers.base import Renderer
from ltp_sink.renderers.terminal import TerminalConfig, TerminalRenderer

logger = logging.getLogger(__name__)


class SinkConfig(BaseModel):
    """Configuration for an LTP sink."""

    # Device identity
    device_id: UUID = Field(default_factory=uuid4)
    name: str = "LTP Sink"
    description: str = ""

    # Display configuration
    device_type: DeviceType = DeviceType.STRING
    pixels: int = 60
    dimensions: list[int] = Field(default_factory=lambda: [60])
    color_format: ColorFormat = ColorFormat.RGB
    max_refresh_hz: int = 60

    # Network
    control_port: int = 0  # 0 = auto
    data_port: int = 0

    # Renderer
    renderer_type: str = "terminal"
    renderer_config: dict[str, Any] = Field(default_factory=dict)


class Sink:
    """LTP Sink - receives and displays LED data."""

    def __init__(self, config: SinkConfig | None = None):
        self.config = config or SinkConfig()

        # Initialize topology
        if len(self.config.dimensions) == 1:
            self._topology = create_linear_topology(self.config.dimensions[0])
        else:
            from libltp import create_matrix_topology

            self._topology = create_matrix_topology(
                self.config.dimensions[0], self.config.dimensions[1]
            )
        self._mapper = TopologyMapper(self._topology)

        # Initialize controls
        self._controls = ControlRegistry()
        self._setup_controls()

        # Network components
        self._advertiser: SinkAdvertiser | None = None
        self._control_server: ControlServer | None = None
        self._data_receiver: DataReceiver | None = None
        self._stream_manager = StreamManager()

        # Renderer
        self._renderer: Renderer | None = None
        self._setup_renderer()

        # State
        self._running = False
        self._pixel_buffer = np.zeros(
            (self.config.pixels, self.config.color_format.bytes_per_pixel),
            dtype=np.uint8,
        )

    def _setup_controls(self) -> None:
        """Set up device controls."""
        self._controls.register(
            NumberControl(
                id="brightness",
                name="Global Brightness",
                description="Master brightness applied to display",
                value=255,
                min=0,
                max=255,
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
        self._controls.register(
            EnumControl(
                id="test_pattern",
                name="Test Pattern",
                description="Pattern to display in test mode",
                value="rgb_sweep",
                options=[
                    EnumOption(value="rgb_sweep", label="RGB Sweep"),
                    EnumOption(value="white", label="All White"),
                    EnumOption(value="gradient", label="Gradient"),
                ],
                group="general",
            )
        )

    def _setup_renderer(self) -> None:
        """Set up the renderer."""
        if self.config.renderer_type == "terminal":
            term_config = TerminalConfig(
                title=self.config.name, **self.config.renderer_config
            )
            self._renderer = TerminalRenderer(term_config)

    def _handle_message(self, message: Message) -> Message | None:
        """Handle incoming control channel messages."""
        logger.debug(f"Handling message: {message.type}")

        if message.type == MessageType.CAPABILITY_REQUEST:
            return self._handle_capability_request(message)
        elif message.type == MessageType.STREAM_SETUP:
            return self._handle_stream_setup(message)
        elif message.type == MessageType.CONTROL_GET:
            return self._handle_control_get(message)
        elif message.type == MessageType.CONTROL_SET:
            return self._handle_control_set(message)

        return None

    def _handle_capability_request(self, message: Message) -> Message:
        """Handle capability request."""
        device_info = {
            "id": str(self.config.device_id),
            "name": self.config.name,
            "description": self.config.description,
            "type": self.config.device_type.value,
            "pixels": self.config.pixels,
            "dimensions": self.config.dimensions,
            "topology": self._mapper.to_dict(),
            "color_formats": [self.config.color_format.name.lower()],
            "max_refresh_hz": self.config.max_refresh_hz,
            "protocol_version": "0.1",
            "controls": self._controls.to_list(),
        }
        return capability_response(message.seq, device_info)

    def _handle_stream_setup(self, message: Message) -> Message:
        """Handle stream setup request."""
        # Create data receiver if not exists
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

    def _handle_data_packet(self, packet: DataPacket) -> None:
        """Handle incoming data packet."""
        # Apply brightness
        brightness = self._controls.get_value("brightness") / 255.0

        # Store pixel data
        if len(packet.pixel_data) <= len(self._pixel_buffer):
            self._pixel_buffer[: len(packet.pixel_data)] = packet.pixel_data

        # Apply brightness
        display_pixels = (self._pixel_buffer * brightness).astype(np.uint8)

        # Check test mode
        if self._controls.get_value("test_mode"):
            display_pixels = self._generate_test_pattern()

        # Render
        if self._renderer:
            self._renderer.render(display_pixels, tuple(self.config.dimensions))

    def _generate_test_pattern(self) -> np.ndarray:
        """Generate test pattern based on current setting."""
        pattern = self._controls.get_value("test_pattern")
        pixels = np.zeros_like(self._pixel_buffer)

        if pattern == "white":
            pixels[:] = 255
        elif pattern == "rgb_sweep":
            for i in range(len(pixels)):
                phase = (i / len(pixels)) * 3
                if phase < 1:
                    pixels[i] = [255, 0, 0]
                elif phase < 2:
                    pixels[i] = [0, 255, 0]
                else:
                    pixels[i] = [0, 0, 255]
        elif pattern == "gradient":
            for i in range(len(pixels)):
                v = int((i / len(pixels)) * 255)
                pixels[i] = [v, v, v]

        return pixels

    async def start(self) -> None:
        """Start the sink."""
        if self._running:
            return

        logger.info(f"Starting sink: {self.config.name}")

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

        # Start renderer
        if self._renderer:
            await self._renderer.start()

        self._running = True
        logger.info(
            f"Sink started - Control: {self._control_server.actual_port}, "
            f"Data: {self._data_receiver.actual_port}"
        )

    async def stop(self) -> None:
        """Stop the sink."""
        if not self._running:
            return

        logger.info("Stopping sink")

        if self._renderer:
            await self._renderer.stop()

        if self._advertiser:
            await self._advertiser.stop()

        if self._data_receiver:
            await self._data_receiver.stop()

        if self._control_server:
            await self._control_server.stop()

        self._running = False
        logger.info("Sink stopped")

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
