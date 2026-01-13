"""Core LTP Source implementation."""

import asyncio
import logging
import time
from typing import Any
from uuid import UUID, uuid4

import numpy as np
from pydantic import BaseModel, Field

from libltp import (
    ColorFormat,
    ControlRegistry,
    Message,
    MessageType,
    NumberControl,
    ServiceBrowser,
    SourceAdvertiser,
    SourceMode,
    capability_response,
    control_get_response,
    control_set_response,
    subscribe_response,
)
from libltp.transport import ControlClient, ControlServer, DataSender, StreamManager
from ltp_source.patterns import Pattern, PatternRegistry

logger = logging.getLogger(__name__)


class SourceConfig(BaseModel):
    """Configuration for an LTP source."""

    # Device identity
    device_id: UUID = Field(default_factory=uuid4)
    name: str = "LTP Source"
    description: str = ""

    # Output configuration
    dimensions: list[int] = Field(default_factory=lambda: [60])
    color_format: ColorFormat = ColorFormat.RGB
    rate: int = 30
    mode: SourceMode = SourceMode.STREAM

    # Pattern
    pattern: str = "rainbow"
    pattern_params: dict[str, Any] = Field(default_factory=dict)

    # Network
    control_port: int = 0


class Source:
    """LTP Source - generates and streams LED data."""

    def __init__(self, config: SourceConfig | None = None):
        self.config = config or SourceConfig()

        # Initialize pattern
        self._pattern: Pattern | None = None
        self._setup_pattern()

        # Initialize controls
        self._controls = ControlRegistry()
        self._setup_controls()

        # Calculate pixel count
        self._pixel_count = 1
        for d in self.config.dimensions:
            self._pixel_count *= d

        # Create pixel buffer
        self._buffer = np.zeros(
            (self._pixel_count, self.config.color_format.bytes_per_pixel),
            dtype=np.uint8,
        )

        # Network components
        self._advertiser: SourceAdvertiser | None = None
        self._control_server: ControlServer | None = None
        self._data_senders: dict[str, DataSender] = {}
        self._stream_manager = StreamManager()
        self._browser: ServiceBrowser | None = None

        # State
        self._running = False
        self._render_task: asyncio.Task | None = None
        self._last_frame_time = 0.0

    def _setup_pattern(self) -> None:
        """Set up the pattern generator."""
        try:
            self._pattern = PatternRegistry.create(
                self.config.pattern, self.config.pattern_params
            )
        except KeyError:
            logger.warning(f"Unknown pattern: {self.config.pattern}, using rainbow")
            self._pattern = PatternRegistry.create("rainbow")

    def _setup_controls(self) -> None:
        """Set up source controls."""
        self._controls.register(
            NumberControl(
                id="master_brightness",
                name="Master Brightness",
                description="Global brightness multiplier",
                value=1.0,
                min=0.0,
                max=1.0,
                step=0.05,
                group="output",
            )
        )
        self._controls.register(
            NumberControl(
                id="rate",
                name="Frame Rate",
                description="Output frames per second",
                value=float(self.config.rate),
                min=1,
                max=120,
                step=1,
                group="output",
            )
        )

        # Add pattern-specific controls
        if self._pattern:
            for control_def in self._pattern.get_controls():
                from libltp import control_from_dict

                try:
                    control = control_from_dict(control_def)
                    self._controls.register(control)
                except Exception as e:
                    logger.warning(f"Failed to register pattern control: {e}")

    def _handle_message(self, message: Message) -> Message | None:
        """Handle incoming control channel messages."""
        logger.debug(f"Handling message: {message.type}")

        if message.type == MessageType.CAPABILITY_REQUEST:
            return self._handle_capability_request(message)
        elif message.type == MessageType.SUBSCRIBE:
            return self._handle_subscribe(message)
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
            "output_dimensions": self.config.dimensions,
            "color_format": self.config.color_format.name.lower(),
            "rate": self.config.rate,
            "mode": self.config.mode.value,
            "source_type": "pattern",
            "protocol_version": "0.1",
            "controls": self._controls.to_list(),
        }
        return capability_response(message.seq, device_info)

    def _handle_subscribe(self, message: Message) -> Message:
        """Handle subscribe request from a sink or controller."""
        target = message.data.get("target", {})

        # Create stream for this subscriber
        stream_id = self._stream_manager.create_stream(
            color_format=self.config.color_format,
        )

        # We'll need the subscriber to tell us where to send data
        # For now, return that they need to set up UDP
        return subscribe_response(
            message.seq,
            status="ok",
            actual={
                "dimensions": self.config.dimensions,
                "color": self.config.color_format.name.lower(),
                "rate": int(self._controls.get_value("rate")),
            },
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

        # Update pattern parameters
        if self._pattern:
            for key, value in applied.items():
                if hasattr(self._pattern.params, key):
                    self._pattern.set_param(key, value)

        status = "ok" if not errors else "partial"
        return control_set_response(message.seq, status, applied, errors or None)

    async def _render_loop(self) -> None:
        """Main render loop."""
        logger.info("Starting render loop")

        while self._running:
            frame_start = time.time()

            # Get current rate
            rate = int(self._controls.get_value("rate"))
            frame_time = 1.0 / rate

            # Update pattern time
            if self._pattern and self._last_frame_time > 0:
                dt = frame_start - self._last_frame_time
                self._pattern.update_time(dt)

            self._last_frame_time = frame_start

            # Render pattern
            if self._pattern:
                if len(self.config.dimensions) == 1:
                    self._pattern.render(self._buffer)
                else:
                    # Reshape for 2D
                    buffer_2d = self._buffer.reshape(
                        self.config.dimensions[1],
                        self.config.dimensions[0],
                        -1,
                    )
                    self._pattern.render(buffer_2d)

            # Apply master brightness
            brightness = self._controls.get_value("master_brightness")
            output = (self._buffer * brightness).astype(np.uint8)

            # Send to all active streams
            for stream_id in self._stream_manager.active_streams:
                stream = self._stream_manager.get_stream(stream_id)
                if stream and stream.get("sender"):
                    try:
                        stream["sender"].send(output, self.config.color_format)
                        self._stream_manager.record_frame_sent(stream_id)
                    except Exception as e:
                        logger.warning(f"Failed to send to stream {stream_id}: {e}")

            # Sleep for remaining frame time
            elapsed = time.time() - frame_start
            sleep_time = frame_time - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    async def connect_to_sink(self, host: str, port: int) -> str | None:
        """Connect to a sink and start streaming.

        Returns:
            Stream ID if successful, None otherwise
        """
        logger.info(f"Connecting to sink at {host}:{port}")

        try:
            # Connect control channel
            client = ControlClient(host, port)
            await client.connect()

            # Request capabilities
            from libltp import capability_request

            cap_msg = capability_request(1)
            response = await client.request(cap_msg)
            logger.info(f"Sink capabilities: {response.data}")

            # Set up stream
            from libltp import stream_setup

            setup_msg = stream_setup(2, self.config.color_format)
            response = await client.request(setup_msg)

            if response.data.get("status") != "ok":
                logger.error(f"Stream setup failed: {response.data}")
                return None

            udp_port = response.data.get("udp_port")
            stream_id = response.data.get("stream_id")

            # Create data sender
            sender = DataSender(host, udp_port)
            await sender.start()

            # Register stream
            self._stream_manager.create_stream(sender=sender)
            self._stream_manager.start_stream(stream_id)
            self._data_senders[stream_id] = sender

            # Store stream info
            stream = self._stream_manager.get_stream(stream_id)
            if stream:
                stream["sender"] = sender
                stream["client"] = client

            logger.info(f"Connected to sink, streaming to {host}:{udp_port}")
            return stream_id

        except Exception as e:
            logger.error(f"Failed to connect to sink: {e}")
            return None

    async def start(self) -> None:
        """Start the source."""
        if self._running:
            return

        logger.info(f"Starting source: {self.config.name}")

        # Start control server
        self._control_server = ControlServer(
            port=self.config.control_port, handler=self._handle_message
        )
        await self._control_server.start()

        # Start mDNS advertisement
        self._advertiser = SourceAdvertiser(
            name=self.config.name.lower().replace(" ", "-"),
            port=self._control_server.actual_port,
            device_id=self.config.device_id,
            display_name=self.config.name,
            description=self.config.description,
            dimensions=self.config.dimensions,
            color_format=self.config.color_format,
            rate=self.config.rate,
            mode=self.config.mode,
            has_controls=True,
        )
        await self._advertiser.start()

        # Start service browser to find sinks
        self._browser = ServiceBrowser()
        await self._browser.start()

        self._running = True

        # Start render loop
        self._render_task = asyncio.create_task(self._render_loop())

        logger.info(f"Source started - Control: {self._control_server.actual_port}")

    async def stop(self) -> None:
        """Stop the source."""
        if not self._running:
            return

        logger.info("Stopping source")
        self._running = False

        # Stop render loop
        if self._render_task:
            self._render_task.cancel()
            try:
                await self._render_task
            except asyncio.CancelledError:
                pass

        # Stop data senders
        for sender in self._data_senders.values():
            await sender.stop()
        self._data_senders.clear()

        # Stop browser
        if self._browser:
            await self._browser.stop()

        # Stop advertiser
        if self._advertiser:
            await self._advertiser.stop()

        # Stop control server
        if self._control_server:
            await self._control_server.stop()

        logger.info("Source stopped")

    async def run(self) -> None:
        """Run the source until interrupted."""
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
    def discovered_sinks(self) -> list[Any]:
        """Get list of discovered sinks."""
        if self._browser:
            return self._browser.sinks
        return []

    def set_pattern(self, name: str, params: dict[str, Any] | None = None) -> None:
        """Change the active pattern."""
        try:
            self._pattern = PatternRegistry.create(name, params)
            logger.info(f"Pattern changed to: {name}")
        except KeyError:
            logger.error(f"Unknown pattern: {name}")
