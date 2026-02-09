"""Core LTP Sink implementation."""

import asyncio
import collections
import logging
import math
import time
from dataclasses import dataclass, field
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
    input_event,
    stream_setup_response,
)
from libltp.types import StreamAction
from libltp.transport import ControlServer, DataReceiver, StreamManager
from ltp_sink.renderers.base import Renderer
from ltp_sink.renderers.terminal import TerminalConfig, TerminalRenderer

logger = logging.getLogger(__name__)

JITTER_WINDOW = 200  # Rolling window size for jitter calculation


@dataclass
class SinkStats:
    """Comprehensive statistics for the sink."""

    # Data packets
    data_packets: int = 0
    data_bytes: int = 0

    # Control messages
    control_rx: int = 0
    control_tx: int = 0

    # Timing
    start_time: float = 0.0
    _last_packet_time: float = 0.0
    _jitter_samples: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=JITTER_WINDOW)
    )

    # Sequence tracking
    _last_seq: int = -1
    seq_gaps: int = 0
    seq_duplicates: int = 0
    seq_out_of_order: int = 0

    # Streams
    total_streams: int = 0
    active_streams: int = 0

    # Rates (updated periodically)
    _rate_window_start: float = 0.0
    _rate_window_packets: int = 0
    _rate_window_bytes: int = 0
    packets_per_sec: float = 0.0
    bytes_per_sec: float = 0.0

    def record_data_packet(self, packet: DataPacket) -> None:
        """Record a data packet arrival."""
        now = time.monotonic()
        nbytes = packet.pixel_data.nbytes

        self.data_packets += 1
        self.data_bytes += nbytes
        self._rate_window_packets += 1
        self._rate_window_bytes += nbytes

        # Jitter (inter-packet interval)
        if self._last_packet_time > 0:
            interval = now - self._last_packet_time
            self._jitter_samples.append(interval)
        self._last_packet_time = now

        # Sequence tracking
        seq = packet.sequence
        if self._last_seq >= 0:
            expected = self._last_seq + 1
            if seq == self._last_seq:
                self.seq_duplicates += 1
            elif seq < self._last_seq:
                self.seq_out_of_order += 1
            elif seq > expected:
                self.seq_gaps += seq - expected
        self._last_seq = seq

        # Update rates every second
        elapsed = now - self._rate_window_start
        if elapsed >= 1.0:
            self.packets_per_sec = self._rate_window_packets / elapsed
            self.bytes_per_sec = self._rate_window_bytes / elapsed
            self._rate_window_start = now
            self._rate_window_packets = 0
            self._rate_window_bytes = 0

    def record_control_rx(self) -> None:
        self.control_rx += 1

    def record_control_tx(self) -> None:
        self.control_tx += 1

    @property
    def uptime(self) -> float:
        if self.start_time <= 0:
            return 0.0
        return time.monotonic() - self.start_time

    @property
    def jitter_ms(self) -> dict[str, float]:
        """Return jitter stats in milliseconds."""
        if len(self._jitter_samples) < 2:
            return {"min": 0.0, "max": 0.0, "avg": 0.0, "stddev": 0.0}
        samples = list(self._jitter_samples)
        avg = sum(samples) / len(samples)
        variance = sum((s - avg) ** 2 for s in samples) / len(samples)
        return {
            "min": min(samples) * 1000,
            "max": max(samples) * 1000,
            "avg": avg * 1000,
            "stddev": math.sqrt(variance) * 1000,
        }

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of all stats as a dict."""
        jitter = self.jitter_ms
        return {
            "data_packets": self.data_packets,
            "data_bytes": self.data_bytes,
            "packets_per_sec": self.packets_per_sec,
            "bytes_per_sec": self.bytes_per_sec,
            "control_rx": self.control_rx,
            "control_tx": self.control_tx,
            "jitter_min_ms": jitter["min"],
            "jitter_max_ms": jitter["max"],
            "jitter_avg_ms": jitter["avg"],
            "jitter_stddev_ms": jitter["stddev"],
            "seq_gaps": self.seq_gaps,
            "seq_duplicates": self.seq_duplicates,
            "seq_out_of_order": self.seq_out_of_order,
            "active_streams": self.active_streams,
            "total_streams": self.total_streams,
            "uptime": self.uptime,
        }


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

    # Inputs: list of {"name": str, "type": str} dicts
    inputs: list[dict[str, str]] = Field(default_factory=list)


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

        # Statistics
        self._stats = SinkStats()

        # Inputs
        self._inputs = self.config.inputs
        self._input_values: dict[int, Any] = {}
        for i, inp in enumerate(self._inputs):
            itype = inp.get("type", "button")
            self._input_values[i] = False if itype in ("button", "switch") else 0

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
        elif self.config.renderer_type == "gui":
            from ltp_sink.renderers.gui import GuiConfig, GuiRenderer

            gui_config = GuiConfig(
                title=self.config.name, **self.config.renderer_config
            )
            renderer = GuiRenderer(gui_config)
            renderer.set_stats_provider(self.get_stats)
            renderer.set_input_callback(self._gui_input_callback)
            renderer.set_input_defs(self._inputs)
            self._renderer = renderer

    def _handle_message(self, message: Message) -> Message | None:
        """Handle incoming control channel messages."""
        logger.debug(f"Handling message: {message.type}")
        self._stats.record_control_rx()

        response = None
        if message.type == MessageType.CAPABILITY_REQUEST:
            response = self._handle_capability_request(message)
        elif message.type == MessageType.STREAM_SETUP:
            response = self._handle_stream_setup(message)
        elif message.type == MessageType.STREAM_CONTROL:
            response = self._handle_stream_control(message)
        elif message.type == MessageType.CONTROL_GET:
            response = self._handle_control_get(message)
        elif message.type == MessageType.CONTROL_SET:
            response = self._handle_control_set(message)

        if response is not None:
            self._stats.record_control_tx()
        return response

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
        if self._inputs:
            device_info["inputs"] = [
                {"id": i, "name": inp["name"], "type": inp.get("type", "button")}
                for i, inp in enumerate(self._inputs)
            ]
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
        self._stats.total_streams += 1
        self._stats.active_streams = len(self._stream_manager.active_streams)

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
            self._stats.active_streams = len(self._stream_manager.active_streams)
            logger.info(f"Started stream: {stream_id}")
        elif action == StreamAction.STOP:
            logger.info(f"Processing STOP for stream {stream_id}")
            self._stream_manager.stop_stream(stream_id)
            self._stats.active_streams = len(self._stream_manager.active_streams)
            # Clear renderer display when stream stops
            logger.info(f"Stopping stream {stream_id}, clearing renderer (renderer={self._renderer})")
            if self._renderer:
                self._renderer.clear()
                logger.info(f"Renderer cleared for stream {stream_id}")
            else:
                logger.warning(f"No renderer to clear for stream {stream_id}")
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

        for control_id, value in applied.items():
            logger.info(f"Set control {control_id}={value}")

        status = "ok" if not errors else "partial"
        return control_set_response(message.seq, status, applied, errors or None)

    def _handle_data_packet(self, packet: DataPacket) -> None:
        """Handle incoming data packet."""
        # Only process data if there's an active stream
        if not self._stream_manager.active_streams:
            logger.debug("Ignoring data packet - no active streams")
            return

        # Record stats
        self._stats.record_data_packet(packet)

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

    def get_stats(self) -> dict[str, Any]:
        """Return a snapshot of sink statistics."""
        stats = self._stats.snapshot()
        if self._renderer:
            stats["fps"] = self._renderer.fps
            stats["frame_count"] = self._renderer.frame_count
        return stats

    def trigger_input(self, input_id: int, value: Any) -> None:
        """Trigger an input event and broadcast to connected clients.

        Called from the GUI thread via _gui_input_callback.
        """
        if input_id < 0 or input_id >= len(self._inputs):
            logger.warning(f"Invalid input_id: {input_id}")
            return

        inp = self._inputs[input_id]
        self._input_values[input_id] = value
        msg = input_event(
            input_id=input_id,
            input_name=inp["name"],
            input_type=inp.get("type", "button"),
            value=value,
        )
        logger.info(f"Input event: {inp['name']} = {value}")

        # Broadcast to all connected control clients
        if self._control_server:
            asyncio.run_coroutine_threadsafe(
                self._control_server.broadcast(msg),
                self._loop,
            )

    def _gui_input_callback(self, input_id: int, value: Any) -> None:
        """Callback from GUI thread for input events."""
        self.trigger_input(input_id, value)

    async def start(self) -> None:
        """Start the sink."""
        if self._running:
            return

        logger.info(f"Starting sink: {self.config.name}")
        self._loop = asyncio.get_event_loop()
        self._stats.start_time = time.monotonic()
        self._stats._rate_window_start = time.monotonic()

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
