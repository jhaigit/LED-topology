"""Base class for logical sources sharing a media context.

A LogicalSource is an independent LTP source that shares media decoding
with other LogicalSources through a SharedMediaContext. Each LogicalSource
has its own network identity (mDNS, control port) but gets its underlying
media data from the shared context.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING
from uuid import UUID, uuid4

import numpy as np
from pydantic import BaseModel, Field

from libltp import (
    ColorFormat,
    ControlRegistry,
    EnumControl,
    EnumOption,
    Message,
    MessageType,
    NumberControl,
    BooleanControl,
    SourceAdvertiser,
    SourceMode,
    capability_response,
    control_get_response,
    control_set_response,
    subscribe_response,
)
from libltp.types import StreamAction
from libltp.transport import ControlServer, DataSender, StreamManager

if TYPE_CHECKING:
    from ltp_media_source.shared_context import SharedMediaContext

logger = logging.getLogger(__name__)


class LogicalSourceConfig(BaseModel):
    """Configuration for a logical source."""

    # Device identity
    device_id: UUID = Field(default_factory=uuid4)
    name: str = "Logical Source"
    description: str = ""

    # Output configuration
    dimensions: list[int] = Field(default_factory=lambda: [16, 16])
    color_format: ColorFormat = ColorFormat.RGB
    rate: int = 30  # Output frame rate

    # Network
    control_port: int = 0  # 0 = auto

    # Source type identifier (for capability reporting)
    source_type: str = "logical"

    model_config = {"arbitrary_types_allowed": True}


class LogicalSource(ABC):
    """Base class for logical sources sharing a media context.

    A LogicalSource is an LTP source that:
    - Has its own network identity (device_id, mDNS, control port)
    - Shares media decoding with other sources via SharedMediaContext
    - Implements render_frame() to produce output frames
    - Handles its own subscribers independently

    Subclasses implement render_frame() to produce their specific output
    (video frames, audio visualizations, etc.).
    """

    def __init__(
        self,
        context: SharedMediaContext,
        config: LogicalSourceConfig | None = None,
    ):
        """Initialize the logical source.

        Args:
            context: SharedMediaContext to get media data from
            config: Source configuration
        """
        self._context = context
        self.config = config or LogicalSourceConfig()

        # Parse dimensions
        if len(self.config.dimensions) == 1:
            self._width = self.config.dimensions[0]
            self._height = 1
        else:
            self._width = self.config.dimensions[0]
            self._height = self.config.dimensions[1]

        self._pixel_count = self._width * self._height

        # Controls
        self._controls = ControlRegistry()
        self._setup_controls()

        # LTP network components
        self._advertiser: SourceAdvertiser | None = None
        self._control_server: ControlServer | None = None
        self._stream_manager = StreamManager()
        self._data_senders: dict[str, DataSender] = {}

        # State
        self._running = False
        self._current_frame: np.ndarray | None = None
        self._render_task: asyncio.Task | None = None

        # Statistics
        self._frame_count = 0
        self._last_stats_time = 0.0
        self._stats_task: asyncio.Task | None = None

    @property
    def context(self) -> SharedMediaContext:
        """The shared media context."""
        return self._context

    @property
    def width(self) -> int:
        """Output width in pixels."""
        return self._width

    @property
    def height(self) -> int:
        """Output height in pixels."""
        return self._height

    @property
    def dimensions(self) -> tuple[int, int]:
        """Output dimensions (width, height)."""
        return (self._width, self._height)

    @property
    def is_running(self) -> bool:
        """True if the source is running."""
        return self._running

    @property
    def current_frame(self) -> np.ndarray | None:
        """Most recently rendered frame."""
        return self._current_frame

    def _setup_controls(self) -> None:
        """Set up source controls. Override in subclasses to add more."""
        self._controls.register(
            NumberControl(
                id="brightness",
                name="Brightness",
                description="Output brightness",
                value=1.0,
                min=0.0,
                max=1.0,
                step=0.05,
                group="output",
            )
        )

        # Shared playback controls (forwarded to context)
        self._controls.register(
            BooleanControl(
                id="paused",
                name="Paused",
                description="Pause playback",
                value=False,
                group="playback",
            )
        )

        self._controls.register(
            BooleanControl(
                id="loop",
                name="Loop",
                description="Loop playback",
                value=True,
                group="playback",
            )
        )

        self._controls.register(
            NumberControl(
                id="speed",
                name="Speed",
                description="Playback speed",
                value=1.0,
                min=0.1,
                max=4.0,
                step=0.1,
                group="playback",
            )
        )

    async def _handle_message(self, message: Message) -> Message | None:
        """Handle control channel messages."""
        logger.debug(f"[{self.config.name}] Handling message: {message.type}")

        if message.type == MessageType.CAPABILITY_REQUEST:
            return self._handle_capability_request(message)
        elif message.type == MessageType.SUBSCRIBE:
            return await self._handle_subscribe(message)
        elif message.type == MessageType.STREAM_CONTROL:
            return await self._handle_stream_control(message)
        elif message.type == MessageType.CONTROL_GET:
            return self._handle_control_get(message)
        elif message.type == MessageType.CONTROL_SET:
            return await self._handle_control_set(message)

        return None

    def _handle_capability_request(self, message: Message) -> Message:
        """Return device capabilities."""
        dims = [self._width] if self._height == 1 else [self._width, self._height]

        device_info = {
            "id": str(self.config.device_id),
            "name": self.config.name,
            "description": self.config.description,
            "output_dimensions": dims,
            "color_format": self.config.color_format.name.lower(),
            "rate": self.config.rate,
            "mode": SourceMode.STREAM.value,
            "source_type": self.config.source_type,
            "protocol_version": "0.1",
            "controls": self._controls.to_list(),
            "shared_context": {
                "media_path": self._context.media_path,
                "duration": self._context.duration,
                "has_audio": self._context.has_audio,
            },
        }

        return capability_response(message.seq, device_info)

    async def _handle_subscribe(self, message: Message) -> Message:
        """Handle subscription request."""
        data = message.data
        callback = data.get("callback", {})
        callback_host = callback.get("host")
        callback_port = callback.get("port")

        if not callback_host or not callback_port:
            return subscribe_response(
                message.seq,
                status="error",
                actual={"error": "callback_host and callback_port required"},
                stream_id="",
            )

        # Create stream
        stream_id = self._stream_manager.create_stream(
            color_format=self.config.color_format,
        )

        # Create data sender
        sender = DataSender(host=callback_host, port=callback_port)
        await sender.start()
        self._data_senders[stream_id] = sender

        self._stream_manager.start_stream(stream_id)

        logger.info(
            f"[{self.config.name}] Subscription created: "
            f"stream {stream_id} -> {callback_host}:{callback_port}"
        )

        return subscribe_response(
            message.seq,
            status="ok",
            actual={
                "dimensions": [self._width] if self._height == 1 else [self._width, self._height],
                "color_format": self.config.color_format.name.lower(),
                "rate": self.config.rate,
            },
            stream_id=stream_id,
        )

    async def _handle_stream_control(self, message: Message) -> Message:
        """Handle stream control (start/stop/pause)."""
        stream_id = message.data.get("stream_id")
        action_str = message.data.get("action", "start")
        action = StreamAction(action_str)

        logger.info(f"[{self.config.name}] Stream control: {stream_id} -> {action.value}")

        if action == StreamAction.STOP:
            self._stream_manager.stop_stream(stream_id)
            if stream_id in self._data_senders:
                sender = self._data_senders.pop(stream_id)
                await sender.stop()
            logger.info(f"[{self.config.name}] Stopped stream: {stream_id}")

        elif action == StreamAction.START:
            self._stream_manager.start_stream(stream_id)

        return Message(
            MessageType.STREAM_CONTROL_RESPONSE,
            message.seq,
            status="ok",
            stream_id=stream_id,
        )

    def _handle_control_get(self, message: Message) -> Message:
        """Get control values."""
        ids = message.data.get("ids")
        values = self._controls.get_values(ids)

        # Add shared context values
        values["position"] = self._context.position
        values["duration"] = self._context.duration
        values["playing"] = self._context.playing

        return control_get_response(message.seq, "ok", values)

    async def _handle_control_set(self, message: Message) -> Message:
        """Set control values."""
        values = message.data.get("values", {})
        applied = {}
        errors = {}

        # Shared controls that affect all sources via context
        shared_controls = {"paused", "loop", "speed", "seek", "position", "play", "pause"}

        for control_id, value in values.items():
            try:
                if control_id in shared_controls:
                    # Forward to shared context
                    handled = await self._context.handle_control(control_id, value)
                    if handled:
                        applied[control_id] = value
                        # Update local control state for consistency
                        if control_id in ("paused", "loop", "speed"):
                            self._controls.set_value(control_id, value)
                    else:
                        errors[control_id] = "Control not handled"
                else:
                    # Local control
                    self._controls.set_value(control_id, value)
                    applied[control_id] = self._controls.get_value(control_id)
            except Exception as e:
                errors[control_id] = str(e)

        status = "ok" if not errors else "partial"
        return control_set_response(message.seq, status, applied, errors or None)

    @abstractmethod
    async def render_frame(self) -> np.ndarray | None:
        """Render the next output frame.

        Subclasses implement this to produce their specific output.

        Returns:
            Frame as RGB uint8 array (height, width, 3), or None if no frame.
        """
        pass

    async def _render_loop(self) -> None:
        """Main render loop - render frames and send to subscribers."""
        frame_interval = 1.0 / self.config.rate
        last_frame_time = time.monotonic()

        logger.info(f"[{self.config.name}] Render loop started at {self.config.rate} fps")

        while self._running:
            now = time.monotonic()
            elapsed = now - last_frame_time

            if elapsed < frame_interval:
                await asyncio.sleep(frame_interval - elapsed)
                continue

            last_frame_time = now

            # Skip if context is paused
            if self._context.paused:
                await asyncio.sleep(0.01)
                continue

            # Render frame (subclass implementation)
            try:
                frame = await self.render_frame()
            except Exception as e:
                logger.error(f"[{self.config.name}] Failed to render frame: {e}")
                await asyncio.sleep(0.1)
                continue

            if frame is None:
                await asyncio.sleep(0.01)
                continue

            # Apply brightness
            brightness = self._controls.get_value("brightness")
            if brightness < 1.0:
                frame = (frame * brightness).astype(np.uint8)

            self._current_frame = frame
            self._frame_count += 1

            # Send to all active streams
            await self._send_frame(frame)

    async def _send_frame(self, frame: np.ndarray) -> None:
        """Send frame to all subscribers."""
        if not self._stream_manager.active_streams:
            return

        # Flatten for transmission
        output = frame.reshape(-1, 3)

        for stream_id in list(self._stream_manager.active_streams):
            sender = self._data_senders.get(stream_id)
            if sender:
                try:
                    sender.send(output, self.config.color_format)
                except Exception as e:
                    logger.error(f"[{self.config.name}] Failed to send to stream {stream_id}: {e}")

    async def _stats_loop(self) -> None:
        """Log statistics periodically."""
        self._last_stats_time = time.monotonic()
        last_frame_count = 0
        interval = 10.0  # Log every 10 seconds

        while self._running:
            await asyncio.sleep(interval)

            now = time.monotonic()
            elapsed = now - self._last_stats_time
            frames = self._frame_count - last_frame_count
            fps = frames / elapsed if elapsed > 0 else 0

            if frames > 0:
                logger.debug(
                    f"[{self.config.name}] Stats: {fps:.1f} fps, "
                    f"{len(self._stream_manager.active_streams)} subscribers"
                )

            self._last_stats_time = now
            last_frame_count = self._frame_count

    def _on_context_state_change(self, state: dict[str, Any]) -> None:
        """Handle state change notification from SharedMediaContext.

        This method is called when another source changes shared playback
        state (play/pause/seek/loop/speed). We update our local control
        values to stay in sync.

        Args:
            state: Dictionary with changed state values
        """
        logger.debug(f"[{self.config.name}] Context state changed: {state}")

        # Sync local controls with context state
        for key, value in state.items():
            try:
                if key == "paused":
                    self._controls.set_value("paused", value)
                elif key == "loop":
                    self._controls.set_value("loop", value)
                elif key == "speed":
                    self._controls.set_value("speed", value)
                # position and playing are not stored as controls
            except Exception as e:
                logger.debug(f"[{self.config.name}] Could not sync control {key}: {e}")

    async def start(self) -> None:
        """Start the logical source."""
        if self._running:
            return

        logger.info(f"Starting logical source: {self.config.name}")

        # Register for context state changes (control coordination)
        self._context.add_state_observer(self._on_context_state_change)

        # Sync initial state from context
        self._controls.set_value("paused", self._context.paused)
        self._controls.set_value("loop", self._context.loop)
        self._controls.set_value("speed", self._context.speed)

        # Start control server
        self._control_server = ControlServer(
            port=self.config.control_port,
            handler=self._handle_message,
        )
        await self._control_server.start()

        # Start mDNS advertisement
        dims = [self._width] if self._height == 1 else [self._width, self._height]
        self._advertiser = SourceAdvertiser(
            name=self.config.name.lower().replace(" ", "-"),
            port=self._control_server.actual_port,
            device_id=self.config.device_id,
            display_name=self.config.name,
            description=self.config.description,
            dimensions=dims,
            color_format=self.config.color_format,
            rate=self.config.rate,
            mode=SourceMode.STREAM,
            has_controls=True,
        )
        await self._advertiser.start()

        self._running = True

        # Start render loop
        self._render_task = asyncio.create_task(self._render_loop())
        self._stats_task = asyncio.create_task(self._stats_loop())

        logger.info(
            f"[{self.config.name}] Started - Control: {self._control_server.actual_port}, "
            f"Output: {self._width}x{self._height} @ {self.config.rate}fps"
        )

    async def stop(self) -> None:
        """Stop the logical source."""
        if not self._running:
            return

        logger.info(f"[{self.config.name}] Stopping")
        self._running = False

        # Unregister from context state changes
        self._context.remove_state_observer(self._on_context_state_change)

        # Cancel tasks
        if self._render_task:
            self._render_task.cancel()
            try:
                await self._render_task
            except asyncio.CancelledError:
                pass

        if self._stats_task:
            self._stats_task.cancel()
            try:
                await self._stats_task
            except asyncio.CancelledError:
                pass

        # Stop senders
        for sender in self._data_senders.values():
            await sender.stop()
        self._data_senders.clear()

        # Stop advertiser
        if self._advertiser:
            await self._advertiser.stop()

        # Stop control server
        if self._control_server:
            await self._control_server.stop()

        logger.info(f"[{self.config.name}] Stopped. Total frames: {self._frame_count}")

    def get_stats(self) -> dict[str, Any]:
        """Get source statistics."""
        return {
            "name": self.config.name,
            "running": self._running,
            "frame_count": self._frame_count,
            "dimensions": [self._width, self._height],
            "rate": self.config.rate,
            "subscribers": len(self._stream_manager.active_streams),
            "context": {
                "position": self._context.position,
                "duration": self._context.duration,
                "playing": self._context.playing,
                "paused": self._context.paused,
            },
        }
