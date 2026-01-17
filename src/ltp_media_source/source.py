"""LTP Media Source - streams video/image content to LED matrices."""

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
    DeviceType,
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

from ltp_media_source.inputs.base import MediaInput, FitMode
from ltp_media_source.inputs import create_input, INPUT_TYPES
from ltp_media_source.processing.scaler import FrameScaler
from ltp_media_source.processing.color import apply_brightness, apply_gamma

logger = logging.getLogger(__name__)


class MediaSourceConfig(BaseModel):
    """Configuration for media source."""

    # Device identity
    device_id: UUID = Field(default_factory=uuid4)
    name: str = "Media Source"
    description: str = ""

    # Output configuration
    dimensions: list[int] = Field(default_factory=lambda: [16, 16])
    color_format: ColorFormat = ColorFormat.RGB
    rate: int = 30  # Output frame rate

    # Input configuration
    input_type: str = "image"  # image, gif, video, camera, screen
    input_path: str = ""
    input_params: dict = Field(default_factory=dict)

    # Fit mode
    fit_mode: str = "contain"

    # Network
    control_port: int = 0  # 0 = auto

    model_config = {"arbitrary_types_allowed": True}


class MediaSource:
    """LTP source that outputs video/image content.

    Supports:
    - Static images (PNG, JPG, etc.)
    - Animated GIFs
    - Video files (MP4, AVI, etc.)
    - Live camera feed
    - Screen capture
    """

    def __init__(self, config: MediaSourceConfig | None = None):
        self.config = config or MediaSourceConfig()

        # Parse dimensions
        if len(self.config.dimensions) == 1:
            self._width = self.config.dimensions[0]
            self._height = 1
        else:
            self._width = self.config.dimensions[0]
            self._height = self.config.dimensions[1]

        self._pixel_count = self._width * self._height

        # Media input
        self._input: MediaInput | None = None
        self._fit_mode = FitMode(self.config.fit_mode)
        self._scaler = FrameScaler(self._width, self._height, self._fit_mode)

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
        self._paused = False
        self._current_frame: np.ndarray | None = None
        self._render_task: asyncio.Task | None = None

        # Statistics
        self._frame_count = 0
        self._last_stats_time = 0.0
        self._stats_task: asyncio.Task | None = None

    def _setup_controls(self) -> None:
        """Set up source controls."""
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

        self._controls.register(
            NumberControl(
                id="gamma",
                name="Gamma",
                description="Gamma correction",
                value=1.0,
                min=1.0,
                max=3.0,
                step=0.1,
                group="output",
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
            BooleanControl(
                id="paused",
                name="Paused",
                description="Pause playback",
                value=False,
                group="playback",
            )
        )

        self._controls.register(
            EnumControl(
                id="fit_mode",
                name="Fit Mode",
                description="How to fit content to display",
                value="contain",
                options=[
                    EnumOption(value="contain", label="Contain", description="Fit within bounds, letterbox"),
                    EnumOption(value="cover", label="Cover", description="Fill and crop edges"),
                    EnumOption(value="stretch", label="Stretch", description="Stretch to fill"),
                    EnumOption(value="tile", label="Tile", description="Repeat pattern"),
                    EnumOption(value="center", label="Center", description="Center without scaling"),
                ],
                group="display",
            )
        )

    def _handle_message(self, message: Message) -> Message | None:
        """Handle control channel messages."""
        logger.debug(f"Handling message: {message.type}")

        if message.type == MessageType.CAPABILITY_REQUEST:
            return self._handle_capability_request(message)
        elif message.type == MessageType.SUBSCRIBE:
            return self._handle_subscribe(message)
        elif message.type == MessageType.STREAM_CONTROL:
            return self._handle_stream_control(message)
        elif message.type == MessageType.CONTROL_GET:
            return self._handle_control_get(message)
        elif message.type == MessageType.CONTROL_SET:
            return self._handle_control_set(message)

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
            "source_type": "media",
            "protocol_version": "0.1",
            "controls": self._controls.to_list(),
            "input": {
                "type": self.config.input_type,
                "path": self.config.input_path,
                "native_dimensions": self._input.native_dimensions if self._input else None,
                "duration": self._input.duration if self._input else None,
                "is_live": self._input.is_live if self._input else False,
            },
        }

        return capability_response(message.seq, device_info)

    async def _handle_subscribe(self, message: Message) -> Message:
        """Handle subscription request."""
        data = message.data
        callback_host = data.get("callback_host")
        callback_port = data.get("callback_port")

        if not callback_host or not callback_port:
            return subscribe_response(
                message.seq,
                status="error",
                error="callback_host and callback_port required",
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

        logger.info(f"Subscription created: stream {stream_id} -> {callback_host}:{callback_port}")

        return subscribe_response(
            message.seq,
            status="ok",
            stream_id=stream_id,
            dimensions=[self._width] if self._height == 1 else [self._width, self._height],
            color_format=self.config.color_format.name.lower(),
            rate=self.config.rate,
        )

    async def _handle_stream_control(self, message: Message) -> Message:
        """Handle stream control (start/stop/pause)."""
        stream_id = message.data.get("stream_id")
        action_str = message.data.get("action", "start")
        action = StreamAction(action_str)

        logger.info(f"Stream control: {stream_id} -> {action.value}")

        if action == StreamAction.STOP:
            self._stream_manager.stop_stream(stream_id)
            if stream_id in self._data_senders:
                sender = self._data_senders.pop(stream_id)
                await sender.stop()
            logger.info(f"Stopped stream: {stream_id}")

        elif action == StreamAction.PAUSE:
            # Pause handled at source level
            pass

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

        # Add dynamic values
        if self._input:
            values["position"] = self._input.position
            values["frame_index"] = self._input.frame_index

        return control_get_response(message.seq, "ok", values)

    def _handle_control_set(self, message: Message) -> Message:
        """Set control values."""
        values = message.data.get("values", {})
        applied = {}
        errors = {}

        for control_id, value in values.items():
            try:
                if control_id == "fit_mode":
                    self._fit_mode = FitMode(value)
                    self._scaler.set_fit_mode(self._fit_mode)
                    applied[control_id] = value
                elif control_id == "speed" and self._input:
                    self._input.speed = float(value)
                    self._controls.set_value(control_id, value)
                    applied[control_id] = value
                elif control_id == "loop" and self._input:
                    self._input.loop = bool(value)
                    self._controls.set_value(control_id, value)
                    applied[control_id] = value
                elif control_id == "paused":
                    self._paused = bool(value)
                    self._controls.set_value(control_id, value)
                    applied[control_id] = value
                elif control_id == "seek" and self._input:
                    self._input.seek(float(value))
                    applied[control_id] = value
                else:
                    self._controls.set_value(control_id, value)
                    applied[control_id] = self._controls.get_value(control_id)
            except Exception as e:
                errors[control_id] = str(e)

        status = "ok" if not errors else "partial"
        return control_set_response(message.seq, status, applied, errors or None)

    def set_input(
        self,
        input_type: str,
        path: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Change the current input source.

        Args:
            input_type: Type of input (image, gif, video, camera, screen)
            path: Path/URL for file-based inputs
            **kwargs: Additional input-specific parameters
        """
        # Close existing input
        if self._input:
            self._input.close()

        # Merge fit mode
        kwargs.setdefault("fit_mode", self._fit_mode)

        # Create new input
        if input_type == "camera":
            device = kwargs.pop("device", 0)
            self._input = create_input(input_type, device=device, **kwargs)
        elif input_type == "screen":
            monitor = kwargs.pop("monitor", 0)
            region = kwargs.pop("region", None)
            self._input = create_input(input_type, monitor=monitor, region=region, **kwargs)
        else:
            self._input = create_input(input_type, path=path, **kwargs)

        # Open it
        self._input.open()

        # Update config
        self.config.input_type = input_type
        self.config.input_path = path or ""

        logger.info(f"Input changed to: {input_type} ({path or 'live'})")

    async def _render_loop(self) -> None:
        """Main render loop - read frames and send to subscribers."""
        frame_interval = 1.0 / self.config.rate
        last_frame_time = time.monotonic()

        logger.info(f"Render loop started at {self.config.rate} fps")

        while self._running:
            now = time.monotonic()
            elapsed = now - last_frame_time

            if elapsed < frame_interval:
                await asyncio.sleep(frame_interval - elapsed)
                continue

            last_frame_time = now

            # Skip if paused
            if self._paused:
                await asyncio.sleep(0.01)
                continue

            # Skip if no input
            if not self._input or not self._input.is_opened:
                await asyncio.sleep(0.1)
                continue

            # Read frame from input
            try:
                frame = self._input.read_frame()
            except Exception as e:
                logger.error(f"Failed to read frame: {e}")
                await asyncio.sleep(0.1)
                continue

            if frame is None:
                # No frame available (end of video, error, etc.)
                await asyncio.sleep(0.01)
                continue

            # Scale to output dimensions
            scaled = self._scaler.scale(frame)

            # Apply effects
            brightness = self._controls.get_value("brightness")
            if brightness < 1.0:
                scaled = apply_brightness(scaled, brightness)

            gamma = self._controls.get_value("gamma")
            if gamma != 1.0:
                scaled = apply_gamma(scaled, gamma)

            self._current_frame = scaled
            self._frame_count += 1

            # Send to all active streams
            await self._send_frame(scaled)

    async def _send_frame(self, frame: np.ndarray) -> None:
        """Send frame to all subscribers."""
        if not self._stream_manager.active_streams:
            return

        # Flatten for 1D strips or keep 2D for matrices
        if self._height == 1:
            output = frame.reshape(-1, 3)
        else:
            output = frame.reshape(-1, 3)  # Flatten to (pixels, 3)

        for stream_id in list(self._stream_manager.active_streams):
            sender = self._data_senders.get(stream_id)
            if sender:
                try:
                    await sender.send(output, self.config.color_format)
                except Exception as e:
                    logger.error(f"Failed to send to stream {stream_id}: {e}")

    async def _stats_loop(self) -> None:
        """Log statistics periodically."""
        self._last_stats_time = time.monotonic()
        last_frame_count = 0
        interval = 5.0

        while self._running:
            await asyncio.sleep(interval)

            now = time.monotonic()
            elapsed = now - self._last_stats_time
            frames = self._frame_count - last_frame_count
            fps = frames / elapsed if elapsed > 0 else 0

            if frames > 0:
                input_info = ""
                if self._input:
                    if self._input.duration:
                        input_info = f", pos={self._input.position:.1f}s"
                    else:
                        input_info = " (live)"

                logger.info(
                    f"Stats: {fps:.1f} fps, {self._frame_count} frames{input_info}, "
                    f"{len(self._stream_manager.active_streams)} subscribers"
                )

            self._last_stats_time = now
            last_frame_count = self._frame_count

    async def start(self) -> None:
        """Start the media source."""
        if self._running:
            return

        logger.info(f"Starting media source: {self.config.name}")

        # Open input if configured
        if self.config.input_path or self.config.input_type in ("camera", "screen"):
            try:
                self.set_input(
                    self.config.input_type,
                    self.config.input_path or None,
                    **self.config.input_params,
                )
            except Exception as e:
                logger.warning(f"Failed to open initial input: {e}")

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
            f"Media source started - Control: {self._control_server.actual_port}, "
            f"Output: {self._width}x{self._height} @ {self.config.rate}fps"
        )

    async def stop(self) -> None:
        """Stop the media source."""
        if not self._running:
            return

        logger.info("Stopping media source")
        self._running = False

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

        # Close input
        if self._input:
            self._input.close()

        # Stop advertiser
        if self._advertiser:
            await self._advertiser.stop()

        # Stop control server
        if self._control_server:
            await self._control_server.stop()

        logger.info(f"Media source stopped. Total frames: {self._frame_count}")

    async def run(self) -> None:
        """Run until interrupted."""
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
    def current_frame(self) -> np.ndarray | None:
        return self._current_frame

    def get_stats(self) -> dict[str, Any]:
        """Get source statistics."""
        return {
            "running": self._running,
            "paused": self._paused,
            "frame_count": self._frame_count,
            "dimensions": [self._width, self._height],
            "rate": self.config.rate,
            "subscribers": len(self._stream_manager.active_streams),
            "input": {
                "type": self.config.input_type,
                "path": self.config.input_path,
                "position": self._input.position if self._input else 0,
                "duration": self._input.duration if self._input else None,
            } if self._input else None,
        }
