"""Multi-channel source - single network identity with multiple output channels.

This module provides the MultiChannelSource class that presents multiple
output channels (video, audio visualizers) under a single mDNS advertisement
and control port. This is more efficient than MediaSourceGroup when the
multiple-advertisement overhead is a concern.

Protocol extension:
- CAPABILITY_RESPONSE includes a 'channels' array with channel metadata
- SUBSCRIBE accepts a 'channel' field to select which channel to subscribe to
- mDNS TXT records include channel count and metadata
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import UUID, uuid4

import numpy as np
import yaml
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

from ltp_media_source.inputs.base import FitMode
from ltp_media_source.inputs.video import VideoInput
from ltp_media_source.shared_context import SharedMediaContext
from ltp_media_source.processing.scaler import FrameScaler
from ltp_media_source.audio.analyzer import AudioAnalyzer
from ltp_media_source.audio.beat_detector import BeatDetector
from ltp_media_source.visualizers import create_visualizer, ColorMode

logger = logging.getLogger(__name__)


class ChannelConfig(BaseModel):
    """Configuration for a single channel."""

    id: str  # Unique channel identifier
    name: str  # Human-readable name
    description: str = ""
    channel_type: str  # "video" or "audio_visualizer"
    dimensions: list[int] = Field(default_factory=lambda: [16, 16])
    color_format: ColorFormat = ColorFormat.RGB
    rate: int = 30

    # Video-specific
    fit_mode: str = "contain"
    gamma: float = 1.0

    # Audio visualizer-specific
    visualizer_type: str = "spectrum"
    color_mode: str = "rainbow"
    fft_size: int = 2048
    smoothing: float = 0.3
    gain: float = 1.0
    beat_sensitivity: float = 1.5

    model_config = {"arbitrary_types_allowed": True, "extra": "allow"}


class MultiChannelConfig(BaseModel):
    """Configuration for a multi-channel source."""

    device_id: UUID = Field(default_factory=uuid4)
    name: str = "Multi-Channel Media"
    description: str = ""
    control_port: int = 0  # 0 = auto

    media_path: str
    loop: bool = True
    speed: float = 1.0
    audio_sample_rate: int = 44100
    audio_channels: int = 2
    audio_buffer_duration: float = 2.0

    channels: list[ChannelConfig] = Field(default_factory=list)
    default_channel: str | None = None  # Default channel for backward compatibility

    model_config = {"extra": "allow"}


class Channel:
    """A single output channel within a MultiChannelSource.

    Each channel has its own:
    - Render function (video scaling or audio visualization)
    - Stream manager and data senders
    - Local controls (brightness, visualizer settings)

    Channels share:
    - Media context (video frames, audio buffer)
    - Network identity (via parent MultiChannelSource)
    """

    def __init__(
        self,
        config: ChannelConfig,
        context: SharedMediaContext,
    ):
        self.config = config
        self._context = context

        # Parse dimensions
        if len(config.dimensions) == 1:
            self._width = config.dimensions[0]
            self._height = 1
        else:
            self._width = config.dimensions[0]
            self._height = config.dimensions[1]

        self._pixel_count = self._width * self._height

        # Controls (local to this channel)
        self._controls = ControlRegistry()
        self._setup_controls()

        # Stream management (each channel has its own streams)
        self._stream_manager = StreamManager()
        self._data_senders: dict[str, DataSender] = {}

        # Rendering components (initialized based on type)
        self._scaler: FrameScaler | None = None
        self._analyzer: AudioAnalyzer | None = None
        self._beat_detector: BeatDetector | None = None
        self._visualizer: Any = None  # Visualizer instance

        self._setup_renderer()

        # State
        self._current_frame: np.ndarray | None = None
        self._frame_count = 0

    @property
    def id(self) -> str:
        """Channel ID."""
        return self.config.id

    @property
    def name(self) -> str:
        """Channel name."""
        return self.config.name

    @property
    def width(self) -> int:
        """Output width."""
        return self._width

    @property
    def height(self) -> int:
        """Output height."""
        return self._height

    @property
    def dimensions(self) -> list[int]:
        """Output dimensions."""
        return [self._width] if self._height == 1 else [self._width, self._height]

    @property
    def channel_type(self) -> str:
        """Channel type (video or audio_visualizer)."""
        return self.config.channel_type

    def _setup_controls(self) -> None:
        """Set up channel-specific controls."""
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

        if self.config.channel_type == "audio_visualizer":
            self._controls.register(
                NumberControl(
                    id="gain",
                    name="Gain",
                    description="Audio gain",
                    value=self.config.gain,
                    min=0.1,
                    max=5.0,
                    step=0.1,
                    group="audio",
                )
            )
            self._controls.register(
                NumberControl(
                    id="smoothing",
                    name="Smoothing",
                    description="Temporal smoothing",
                    value=self.config.smoothing,
                    min=0.0,
                    max=0.99,
                    step=0.05,
                    group="audio",
                )
            )

    def _setup_renderer(self) -> None:
        """Set up the renderer based on channel type."""
        if self.config.channel_type == "video":
            self._scaler = FrameScaler(
                target_width=self._width,
                target_height=self._height,
                fit_mode=FitMode(self.config.fit_mode),
            )
        elif self.config.channel_type == "audio_visualizer":
            self._analyzer = AudioAnalyzer(
                sample_rate=self._context.audio_sample_rate,
                fft_size=self.config.fft_size,
                smoothing=self.config.smoothing,
                gain=self.config.gain,
            )
            self._beat_detector = BeatDetector(
                sensitivity=self.config.beat_sensitivity,
            )

            # Create visualizer
            try:
                color_mode = ColorMode[self.config.color_mode.upper()]
            except KeyError:
                color_mode = ColorMode.RAINBOW

            self._visualizer = create_visualizer(
                visualizer_type=self.config.visualizer_type,
                width=self._width,
                height=self._height,
                color_mode=color_mode,
            )

    async def render_frame(self) -> np.ndarray | None:
        """Render the next output frame."""
        if self.config.channel_type == "video":
            return await self._render_video_frame()
        elif self.config.channel_type == "audio_visualizer":
            return await self._render_audio_frame()
        return None

    async def _render_video_frame(self) -> np.ndarray | None:
        """Render a video frame."""
        frame = await self._context.get_video_frame()
        if frame is None:
            return None
        return self._scaler.scale(frame)

    async def _render_audio_frame(self) -> np.ndarray | None:
        """Render an audio visualization frame."""
        # Get audio samples
        samples = await self._context.get_audio_mono(self._analyzer.fft_size)
        if samples is None or len(samples) == 0:
            return np.zeros((self._height, self._width, 3), dtype=np.uint8)

        # Update gain/smoothing from controls
        self._analyzer.gain = self._controls.get_value("gain")
        self._analyzer.smoothing = self._controls.get_value("smoothing")

        # Analyze audio
        self._analyzer.analyze(samples)

        # Update beat detector
        self._beat_detector.update(spectrum=self._analyzer.spectrum)

        # Render visualization
        frame = self._visualizer.render(self._analyzer, self._beat_detector)

        # Ensure correct shape
        if frame.ndim == 2:
            # Linear (1D) output - reshape to (1, width, 3)
            frame = frame.reshape(1, -1, 3)

        return frame

    async def handle_subscribe(self, message: Message) -> Message:
        """Handle subscription request for this channel."""
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
            f"[{self.name}] Subscription created: "
            f"stream {stream_id} -> {callback_host}:{callback_port}"
        )

        return subscribe_response(
            message.seq,
            status="ok",
            actual={
                "channel": self.id,
                "dimensions": self.dimensions,
                "color_format": self.config.color_format.name.lower(),
                "rate": self.config.rate,
            },
            stream_id=stream_id,
        )

    async def handle_stream_control(self, message: Message) -> Message:
        """Handle stream control for this channel."""
        stream_id = message.data.get("stream_id")
        action_str = message.data.get("action", "start")
        action = StreamAction(action_str)

        logger.info(f"[{self.name}] Stream control: {stream_id} -> {action.value}")

        if action == StreamAction.STOP:
            self._stream_manager.stop_stream(stream_id)
            if stream_id in self._data_senders:
                sender = self._data_senders.pop(stream_id)
                await sender.stop()
            logger.info(f"[{self.name}] Stopped stream: {stream_id}")

        elif action == StreamAction.START:
            self._stream_manager.start_stream(stream_id)

        return Message(
            MessageType.STREAM_CONTROL_RESPONSE,
            message.seq,
            status="ok",
            stream_id=stream_id,
        )

    def handle_control_get(self, ids: list[str] | None = None) -> dict[str, Any]:
        """Get control values for this channel."""
        return self._controls.get_values(ids)

    def handle_control_set(self, values: dict[str, Any]) -> tuple[dict, dict]:
        """Set control values for this channel."""
        applied = {}
        errors = {}

        for control_id, value in values.items():
            try:
                self._controls.set_value(control_id, value)
                applied[control_id] = self._controls.get_value(control_id)
            except Exception as e:
                errors[control_id] = str(e)

        return applied, errors

    async def send_frame(self, frame: np.ndarray) -> None:
        """Send frame to all subscribers of this channel."""
        if not self._stream_manager.active_streams:
            return

        # Apply brightness
        brightness = self._controls.get_value("brightness")
        if brightness < 1.0:
            frame = (frame * brightness).astype(np.uint8)

        self._current_frame = frame
        self._frame_count += 1

        # Flatten for transmission
        output = frame.reshape(-1, 3)

        for stream_id in list(self._stream_manager.active_streams):
            sender = self._data_senders.get(stream_id)
            if sender:
                try:
                    sender.send(output, self.config.color_format)
                except Exception as e:
                    logger.error(f"[{self.name}] Failed to send to stream {stream_id}: {e}")

    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        # Stop all senders
        for sender in self._data_senders.values():
            await sender.stop()
        self._data_senders.clear()

    def get_metadata(self) -> dict[str, Any]:
        """Get channel metadata for capability response."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.config.description,
            "type": self.channel_type,
            "dimensions": self.dimensions,
            "color_format": self.config.color_format.name.lower(),
            "rate": self.config.rate,
            "controls": self._controls.to_list(),
        }

    def get_stats(self) -> dict[str, Any]:
        """Get channel statistics."""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.channel_type,
            "dimensions": self.dimensions,
            "frame_count": self._frame_count,
            "subscribers": len(self._stream_manager.active_streams),
        }


class MultiChannelSource:
    """A media source with multiple output channels under single network identity.

    This class provides the Multi-Channel Protocol extension:
    - Single mDNS advertisement with channel metadata
    - Single TCP control port
    - CAPABILITY_RESPONSE includes channels array
    - SUBSCRIBE accepts channel field for routing

    Example usage:
        config = MultiChannelConfig(
            name="My Media",
            media_path="/path/to/video.mp4",
            channels=[
                ChannelConfig(id="video", name="Video", channel_type="video", dimensions=[64, 32]),
                ChannelConfig(id="spectrum", name="Spectrum", channel_type="audio_visualizer", dimensions=[60]),
            ],
            default_channel="video",
        )
        source = MultiChannelSource(config)
        await source.start()
    """

    def __init__(self, config: MultiChannelConfig):
        self._config = config
        self._context: SharedMediaContext | None = None
        self._channels: dict[str, Channel] = {}
        self._channel_order: list[str] = []  # Preserve order for iteration

        # Network components (single for all channels)
        self._advertiser: SourceAdvertiser | None = None
        self._control_server: ControlServer | None = None

        # Shared controls (affect all channels)
        self._controls = ControlRegistry()
        self._setup_shared_controls()

        # State
        self._running = False
        self._frame_task: asyncio.Task | None = None
        self._render_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()

        # Track which stream belongs to which channel
        self._stream_to_channel: dict[str, str] = {}

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> "MultiChannelSource":
        """Create from YAML configuration file."""
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {yaml_path}")

        with open(path) as f:
            data = yaml.safe_load(f)

        config = MultiChannelConfig(**data)
        return cls(config)

    @classmethod
    def from_args(
        cls,
        media_path: str,
        name: str = "Multi-Channel Media",
        video_specs: Sequence[str] | None = None,
        audio_specs: Sequence[str] | None = None,
        loop: bool = True,
        default_channel: str | None = None,
    ) -> "MultiChannelSource":
        """Create from command-line style arguments.

        Args:
            media_path: Path to media file
            name: Source name
            video_specs: Video channel specs like "id:name:64x32" or "id:name:64x32:cover"
            audio_specs: Audio specs like "id:name:spectrum:60" or "id:name:spectrogram:16x16:fire"
            loop: Enable looping
            default_channel: Default channel ID for backward compatibility
        """
        channels = []

        if video_specs:
            for spec in video_specs:
                parts = spec.split(":")
                channel_id = parts[0] if len(parts) > 0 else "video"
                channel_name = parts[1] if len(parts) > 1 else channel_id.title()

                dims = [16, 16]
                if len(parts) > 2:
                    dim_parts = parts[2].lower().split("x")
                    dims = [int(d) for d in dim_parts]
                    if len(dims) == 1:
                        dims = [dims[0], dims[0]]

                fit_mode = parts[3] if len(parts) > 3 else "contain"

                channels.append(ChannelConfig(
                    id=channel_id,
                    name=channel_name,
                    channel_type="video",
                    dimensions=dims,
                    fit_mode=fit_mode,
                ))

        if audio_specs:
            for spec in audio_specs:
                parts = spec.split(":")
                channel_id = parts[0] if len(parts) > 0 else "audio"
                channel_name = parts[1] if len(parts) > 1 else channel_id.title()
                viz_type = parts[2] if len(parts) > 2 else "spectrum"

                dims = [60]
                if len(parts) > 3:
                    dim_parts = parts[3].lower().split("x")
                    dims = [int(d) for d in dim_parts]

                color_mode = parts[4] if len(parts) > 4 else "rainbow"

                channels.append(ChannelConfig(
                    id=channel_id,
                    name=channel_name,
                    channel_type="audio_visualizer",
                    dimensions=dims,
                    visualizer_type=viz_type,
                    color_mode=color_mode,
                ))

        # Set default channel
        if not default_channel and channels:
            default_channel = channels[0].id

        config = MultiChannelConfig(
            name=name,
            media_path=media_path,
            loop=loop,
            channels=channels,
            default_channel=default_channel,
        )
        return cls(config)

    @property
    def config(self) -> MultiChannelConfig:
        """Source configuration."""
        return self._config

    @property
    def context(self) -> SharedMediaContext | None:
        """Shared media context."""
        return self._context

    @property
    def channels(self) -> dict[str, Channel]:
        """Channel dictionary by ID."""
        return self._channels.copy()

    @property
    def is_running(self) -> bool:
        """True if source is running."""
        return self._running

    def _setup_shared_controls(self) -> None:
        """Set up shared playback controls."""
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
                value=self._config.loop,
                group="playback",
            )
        )
        self._controls.register(
            NumberControl(
                id="speed",
                name="Speed",
                description="Playback speed",
                value=self._config.speed,
                min=0.1,
                max=4.0,
                step=0.1,
                group="playback",
            )
        )

    def _create_context(self) -> SharedMediaContext:
        """Create shared media context."""
        video_input = VideoInput(
            path=self._config.media_path,
            loop=self._config.loop,
            speed=self._config.speed,
            extract_audio=True,
        )

        return SharedMediaContext(
            media_input=video_input,
            audio_sample_rate=self._config.audio_sample_rate,
            audio_channels=self._config.audio_channels,
            audio_buffer_duration=self._config.audio_buffer_duration,
        )

    def _create_channels(self) -> None:
        """Create all channels from configuration."""
        for channel_config in self._config.channels:
            channel = Channel(channel_config, self._context)
            self._channels[channel.id] = channel
            self._channel_order.append(channel.id)

        logger.info(f"Created {len(self._channels)} channels")

    async def _handle_message(self, message: Message) -> Message | None:
        """Handle control channel messages."""
        logger.debug(f"Handling message: {message.type}")

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
        """Return device capabilities with channel information."""
        # Build channels array for protocol extension
        channels_info = []
        for channel_id in self._channel_order:
            channel = self._channels[channel_id]
            channels_info.append(channel.get_metadata())

        # Determine primary output dimensions (from default channel)
        default_channel_id = self._config.default_channel or (
            self._channel_order[0] if self._channel_order else None
        )
        default_dims = [16, 16]
        if default_channel_id and default_channel_id in self._channels:
            default_dims = self._channels[default_channel_id].dimensions

        device_info = {
            "id": str(self._config.device_id),
            "name": self._config.name,
            "description": self._config.description,
            "output_dimensions": default_dims,
            "color_format": "rgb",
            "rate": 30,
            "mode": SourceMode.STREAM.value,
            "source_type": "multi_channel_media",
            "protocol_version": "0.2",  # Extended protocol
            "controls": self._controls.to_list(),
            # Multi-channel protocol extension
            "channels": channels_info,
            "default_channel": default_channel_id,
            "channel_count": len(self._channels),
            # Shared context info
            "shared_context": {
                "media_path": self._context.media_path if self._context else None,
                "duration": self._context.duration if self._context else None,
                "has_audio": self._context.has_audio if self._context else False,
            },
        }

        return capability_response(message.seq, device_info)

    async def _handle_subscribe(self, message: Message) -> Message:
        """Handle subscription request with channel routing."""
        data = message.data

        # Get requested channel (protocol extension)
        channel_id = data.get("channel")

        # Fall back to default channel for backward compatibility
        if not channel_id:
            channel_id = self._config.default_channel
            if not channel_id and self._channel_order:
                channel_id = self._channel_order[0]

        # Validate channel exists
        if not channel_id or channel_id not in self._channels:
            available = list(self._channels.keys())
            return subscribe_response(
                message.seq,
                status="error",
                actual={"error": f"Unknown channel: {channel_id}. Available: {available}"},
                stream_id="",
            )

        channel = self._channels[channel_id]

        # Delegate to channel
        response = await channel.handle_subscribe(message)

        # Track stream->channel mapping
        if response.data.get("status") == "ok":
            stream_id = response.data.get("stream_id")
            if stream_id:
                self._stream_to_channel[stream_id] = channel_id

        return response

    async def _handle_stream_control(self, message: Message) -> Message:
        """Handle stream control with channel routing."""
        stream_id = message.data.get("stream_id")

        # Find which channel owns this stream
        channel_id = self._stream_to_channel.get(stream_id)
        if not channel_id or channel_id not in self._channels:
            return Message(
                MessageType.STREAM_CONTROL_RESPONSE,
                message.seq,
                status="error",
                error=f"Unknown stream: {stream_id}",
            )

        channel = self._channels[channel_id]
        response = await channel.handle_stream_control(message)

        # Clean up mapping if stream stopped
        action = message.data.get("action")
        if action == "stop" and stream_id in self._stream_to_channel:
            del self._stream_to_channel[stream_id]

        return response

    def _handle_control_get(self, message: Message) -> Message:
        """Get control values."""
        ids = message.data.get("ids")
        channel_id = message.data.get("channel")

        # Get shared controls
        values = self._controls.get_values(ids)

        # Add context values
        if self._context:
            values["position"] = self._context.position
            values["duration"] = self._context.duration
            values["playing"] = self._context.playing

        # If specific channel requested, include its controls
        if channel_id and channel_id in self._channels:
            channel_values = self._channels[channel_id].handle_control_get(ids)
            values.update({f"{channel_id}.{k}": v for k, v in channel_values.items()})

        return control_get_response(message.seq, "ok", values)

    async def _handle_control_set(self, message: Message) -> Message:
        """Set control values."""
        values = message.data.get("values", {})
        applied = {}
        errors = {}

        # Shared controls that affect context
        shared_controls = {"paused", "loop", "speed", "seek", "position", "play", "pause"}

        for control_id, value in values.items():
            # Check if it's a channel-specific control (format: "channel_id.control_id")
            if "." in control_id:
                channel_id, local_id = control_id.split(".", 1)
                if channel_id in self._channels:
                    ch_applied, ch_errors = self._channels[channel_id].handle_control_set(
                        {local_id: value}
                    )
                    applied.update({f"{channel_id}.{k}": v for k, v in ch_applied.items()})
                    errors.update({f"{channel_id}.{k}": v for k, v in ch_errors.items()})
                else:
                    errors[control_id] = f"Unknown channel: {channel_id}"
                continue

            # Shared control
            try:
                if control_id in shared_controls and self._context:
                    handled = await self._context.handle_control(control_id, value)
                    if handled:
                        applied[control_id] = value
                        if control_id in ("paused", "loop", "speed"):
                            self._controls.set_value(control_id, value)
                    else:
                        errors[control_id] = "Control not handled"
                else:
                    self._controls.set_value(control_id, value)
                    applied[control_id] = self._controls.get_value(control_id)
            except Exception as e:
                errors[control_id] = str(e)

        status = "ok" if not errors else "partial"
        return control_set_response(message.seq, status, applied, errors or None)

    async def _frame_loop(self) -> None:
        """Main frame reading loop."""
        frame_interval = 1.0 / self._context.frame_rate
        last_frame_time = asyncio.get_event_loop().time()

        logger.info(f"Frame loop started at {self._context.frame_rate:.1f} fps")

        while self._running:
            now = asyncio.get_event_loop().time()
            elapsed = now - last_frame_time

            if elapsed < frame_interval:
                await asyncio.sleep(frame_interval - elapsed)
                continue

            last_frame_time = now

            try:
                frame = await self._context.read_frame()
                if frame is None and not self._context.loop:
                    logger.info("End of media reached")
                    break
            except Exception as e:
                logger.error(f"Error reading frame: {e}")
                await asyncio.sleep(0.1)

    async def _render_loop(self) -> None:
        """Render and send frames for all channels."""
        # Determine render interval from fastest channel
        min_interval = min(
            1.0 / ch.config.rate for ch in self._channels.values()
        ) if self._channels else 1.0 / 30

        last_render_time = asyncio.get_event_loop().time()

        logger.info(f"Render loop started at {1.0/min_interval:.1f} fps")

        while self._running:
            now = asyncio.get_event_loop().time()
            elapsed = now - last_render_time

            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
                continue

            last_render_time = now

            # Skip if paused
            if self._context and self._context.paused:
                await asyncio.sleep(0.01)
                continue

            # Render all channels
            for channel in self._channels.values():
                try:
                    frame = await channel.render_frame()
                    if frame is not None:
                        await channel.send_frame(frame)
                except Exception as e:
                    logger.error(f"Error rendering channel {channel.id}: {e}")

    async def start(self) -> None:
        """Start the multi-channel source."""
        if self._running:
            return

        logger.info(f"Starting MultiChannelSource: {self._config.name}")
        logger.info(f"Media: {self._config.media_path}")

        # Create context and channels
        self._context = self._create_context()
        await self._context.start()
        self._create_channels()

        # Start control server
        self._control_server = ControlServer(
            port=self._config.control_port,
            handler=self._handle_message,
        )
        await self._control_server.start()

        # Build mDNS TXT properties for channels
        # Include channel count and summary in TXT records
        channel_summary = ",".join(
            f"{ch.id}:{ch.channel_type}:{':'.join(str(d) for d in ch.dimensions)}"
            for ch in self._channels.values()
        )

        # Get default channel dimensions for mDNS
        default_channel_id = self._config.default_channel or (
            self._channel_order[0] if self._channel_order else None
        )
        default_dims = [16, 16]
        if default_channel_id and default_channel_id in self._channels:
            default_dims = self._channels[default_channel_id].dimensions

        # Start mDNS advertisement
        self._advertiser = SourceAdvertiser(
            name=self._config.name.lower().replace(" ", "-"),
            port=self._control_server.actual_port,
            device_id=self._config.device_id,
            display_name=self._config.name,
            description=self._config.description,
            dimensions=default_dims,
            color_format=ColorFormat.RGB,
            rate=30,
            mode=SourceMode.STREAM,
            has_controls=True,
        )
        await self._advertiser.start()

        self._running = True

        # Start loops
        self._frame_task = asyncio.create_task(self._frame_loop())
        self._render_task = asyncio.create_task(self._render_loop())

        logger.info(
            f"MultiChannelSource started on port {self._control_server.actual_port}"
        )
        logger.info(f"Channels ({len(self._channels)}):")
        for channel in self._channels.values():
            logger.info(
                f"  - {channel.id}: {channel.name} ({channel.channel_type}, "
                f"{channel.width}x{channel.height})"
            )
        if default_channel_id:
            logger.info(f"Default channel: {default_channel_id}")

    async def stop(self) -> None:
        """Stop the multi-channel source."""
        if not self._running:
            return

        logger.info("Stopping MultiChannelSource")
        self._running = False

        # Cancel tasks
        for task in [self._frame_task, self._render_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Stop all channels
        for channel in self._channels.values():
            await channel.stop()

        # Stop advertiser
        if self._advertiser:
            await self._advertiser.stop()

        # Stop control server
        if self._control_server:
            await self._control_server.stop()

        # Stop context
        if self._context:
            await self._context.stop()

        self._channels.clear()
        self._channel_order.clear()
        self._stream_to_channel.clear()
        self._context = None
        self._shutdown_event.set()

        logger.info("MultiChannelSource stopped")

    async def run_forever(self) -> None:
        """Run until interrupted."""
        await self.start()

        loop = asyncio.get_event_loop()

        def signal_handler():
            logger.info("Received shutdown signal")
            asyncio.create_task(self.stop())

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except NotImplementedError:
                pass

        await self._shutdown_event.wait()

    def get_channel(self, channel_id: str) -> Channel | None:
        """Get a channel by ID."""
        return self._channels.get(channel_id)

    def get_stats(self) -> dict[str, Any]:
        """Get source statistics."""
        return {
            "name": self._config.name,
            "media_path": self._config.media_path,
            "running": self._running,
            "channel_count": len(self._channels),
            "default_channel": self._config.default_channel,
            "context": self._context.get_state() if self._context else None,
            "channels": {
                channel_id: channel.get_stats()
                for channel_id, channel in self._channels.items()
            },
        }


async def run_multi_channel_source(config: MultiChannelConfig) -> None:
    """Run a multi-channel source until interrupted."""
    source = MultiChannelSource(config)
    await source.run_forever()


async def run_multi_channel_from_yaml(yaml_path: str | Path) -> None:
    """Run a multi-channel source from YAML configuration."""
    source = MultiChannelSource.from_yaml(yaml_path)
    await source.run_forever()
