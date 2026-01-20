"""Media source group - coordinates multiple logical sources from shared media.

This module provides the MediaSourceGroup class that manages multiple
LogicalSource instances (video, audio visualizers) from a single media file.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from typing import Any, Sequence

import yaml
from pydantic import BaseModel, Field

from ltp_media_source.inputs.base import FitMode, MediaInput
from ltp_media_source.inputs.video import VideoInput
from ltp_media_source.shared_context import SharedMediaContext

# Optional audio inputs
try:
    from ltp_media_source.inputs.audio import AudioFileInput
    from ltp_media_source.inputs.microphone import MicrophoneInput
    HAS_AUDIO_INPUTS = True
except ImportError:
    HAS_AUDIO_INPUTS = False
    AudioFileInput = None  # type: ignore
    MicrophoneInput = None  # type: ignore

# Optional audio playback
try:
    from ltp_media_source.audio_playback import AudioPlayback
    HAS_AUDIO_PLAYBACK = True
except ImportError:
    HAS_AUDIO_PLAYBACK = False
    AudioPlayback = None  # type: ignore
from ltp_media_source.sources.base import LogicalSource, LogicalSourceConfig
from ltp_media_source.sources.video_source import VideoLogicalSource, VideoLogicalSourceConfig
from ltp_media_source.sources.audio_visualizer import (
    AudioVisualizerSource,
    AudioVisualizerSourceConfig,
)

logger = logging.getLogger(__name__)


class SourceDefinition(BaseModel):
    """Definition of a single source in a source group."""

    name: str
    type: str  # "video" or "audio_visualizer"
    dimensions: list[int] = Field(default_factory=lambda: [16, 16])
    rate: int = 30
    description: str = ""

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

    model_config = {"extra": "allow"}


class SourceGroupConfig(BaseModel):
    """Configuration for a media source group."""

    media_path: str | None = None  # Path for video or audio file
    input_type: str = "video"  # "video", "audio_file", or "microphone"
    input_device: str | int | None = None  # Device for microphone input
    loop: bool = True
    speed: float = 1.0
    audio_sample_rate: int = 44100
    audio_channels: int = 2
    audio_buffer_duration: float = 2.0
    audio_output: bool = False  # Play audio through speakers
    audio_output_device: str | int | None = None  # Speaker output device
    volume: float = 1.0  # Audio output volume
    sources: list[SourceDefinition] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class MediaSourceGroup:
    """Manages multiple logical sources from shared media.

    This class coordinates multiple LogicalSource instances that share
    a single media decoder through SharedMediaContext. Each source has
    its own network identity (mDNS, control port) but shares playback
    state and media data.

    Example usage:
        config = SourceGroupConfig(
            media_path="/path/to/video.mp4",
            sources=[
                SourceDefinition(name="Video", type="video", dimensions=[64, 32]),
                SourceDefinition(name="Spectrum", type="audio_visualizer", dimensions=[60]),
            ]
        )
        group = MediaSourceGroup(config)
        await group.start()
    """

    def __init__(self, config: SourceGroupConfig):
        """Initialize the source group.

        Args:
            config: Configuration for the source group
        """
        self._config = config
        self._context: SharedMediaContext | None = None
        self._sources: list[LogicalSource] = []
        self._running = False
        self._frame_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> "MediaSourceGroup":
        """Create a MediaSourceGroup from a YAML configuration file.

        Args:
            yaml_path: Path to the YAML configuration file

        Returns:
            MediaSourceGroup instance

        Raises:
            FileNotFoundError: If the config file doesn't exist
            ValueError: If the config is invalid
        """
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {yaml_path}")

        with open(path) as f:
            data = yaml.safe_load(f)

        config = SourceGroupConfig(**data)
        return cls(config)

    @classmethod
    def from_args(
        cls,
        media_path: str,
        video_specs: Sequence[str] | None = None,
        audio_specs: Sequence[str] | None = None,
        loop: bool = True,
    ) -> "MediaSourceGroup":
        """Create a MediaSourceGroup from command-line style arguments.

        Args:
            media_path: Path to the media file
            video_specs: Video source specs like "Name:64x32" or "Name:64x32:cover"
            audio_specs: Audio visualizer specs like "Name:spectrum:60" or "Name:spectrogram:16x16"
            loop: Enable looping

        Returns:
            MediaSourceGroup instance
        """
        sources = []

        # Parse video specs
        if video_specs:
            for spec in video_specs:
                parts = spec.split(":")
                name = parts[0] if len(parts) > 0 else "Video"

                # Parse dimensions (e.g., "64x32" or "64")
                dims = [16, 16]
                if len(parts) > 1:
                    dim_parts = parts[1].lower().split("x")
                    dims = [int(d) for d in dim_parts]
                    if len(dims) == 1:
                        dims = [dims[0], dims[0]]  # Square if single value

                fit_mode = parts[2] if len(parts) > 2 else "contain"

                sources.append(SourceDefinition(
                    name=name,
                    type="video",
                    dimensions=dims,
                    fit_mode=fit_mode,
                ))

        # Parse audio visualizer specs
        if audio_specs:
            for spec in audio_specs:
                parts = spec.split(":")
                name = parts[0] if len(parts) > 0 else "Audio"
                viz_type = parts[1] if len(parts) > 1 else "spectrum"

                # Parse dimensions
                dims = [60]
                if len(parts) > 2:
                    dim_parts = parts[2].lower().split("x")
                    dims = [int(d) for d in dim_parts]

                color_mode = parts[3] if len(parts) > 3 else "rainbow"

                sources.append(SourceDefinition(
                    name=name,
                    type="audio_visualizer",
                    dimensions=dims,
                    visualizer_type=viz_type,
                    color_mode=color_mode,
                ))

        config = SourceGroupConfig(
            media_path=media_path,
            loop=loop,
            sources=sources,
        )
        return cls(config)

    @property
    def config(self) -> SourceGroupConfig:
        """The source group configuration."""
        return self._config

    @property
    def context(self) -> SharedMediaContext | None:
        """The shared media context."""
        return self._context

    @property
    def sources(self) -> list[LogicalSource]:
        """List of logical sources."""
        return self._sources.copy()

    @property
    def is_running(self) -> bool:
        """True if the source group is running."""
        return self._running

    def _create_input(self) -> MediaInput:
        """Create the media input based on configuration."""
        input_type = self._config.input_type

        if input_type == "video":
            if not self._config.media_path:
                raise ValueError("media_path required for video input")
            return VideoInput(
                path=self._config.media_path,
                loop=self._config.loop,
                speed=self._config.speed,
                extract_audio=True,  # Enable audio extraction
            )

        elif input_type == "audio_file":
            if not HAS_AUDIO_INPUTS or AudioFileInput is None:
                raise RuntimeError(
                    "Audio file input requires sounddevice. "
                    "Install with: pip install 'ltp[media]'"
                )
            if not self._config.media_path:
                raise ValueError("media_path required for audio file input")
            return AudioFileInput(
                path=self._config.media_path,
                loop=self._config.loop,
                speed=self._config.speed,
                sample_rate=self._config.audio_sample_rate,
            )

        elif input_type == "microphone":
            if not HAS_AUDIO_INPUTS or MicrophoneInput is None:
                raise RuntimeError(
                    "Microphone input requires sounddevice. "
                    "Install with: pip install 'ltp[media]'"
                )
            return MicrophoneInput(
                device=self._config.input_device,
                sample_rate=self._config.audio_sample_rate,
                channels=self._config.audio_channels,
            )

        else:
            raise ValueError(f"Unknown input type: {input_type}")

    def _create_audio_playback(self) -> AudioPlayback | None:
        """Create audio playback if configured."""
        if not self._config.audio_output:
            return None

        if not HAS_AUDIO_PLAYBACK or AudioPlayback is None:
            logger.warning(
                "Audio playback not available (sounddevice not installed). "
                "Install with: pip install 'ltp[media]'"
            )
            return None

        playback = AudioPlayback(
            sample_rate=self._config.audio_sample_rate,
            channels=self._config.audio_channels,
            device=self._config.audio_output_device,
        )
        playback.volume = self._config.volume
        return playback

    def _create_context(self) -> SharedMediaContext:
        """Create the shared media context."""
        # Create the media input
        media_input = self._create_input()

        # Create optional audio playback
        audio_playback = self._create_audio_playback()

        # Create shared context
        return SharedMediaContext(
            media_input=media_input,
            audio_sample_rate=self._config.audio_sample_rate,
            audio_channels=self._config.audio_channels,
            audio_buffer_duration=self._config.audio_buffer_duration,
            audio_playback=audio_playback,
        )

    def _create_sources(self) -> list[LogicalSource]:
        """Create all logical sources from configuration."""
        sources = []

        for source_def in self._config.sources:
            source = self._create_source(source_def)
            if source:
                sources.append(source)

        return sources

    def _create_source(self, source_def: SourceDefinition) -> LogicalSource | None:
        """Create a single logical source from its definition.

        Args:
            source_def: Source definition

        Returns:
            LogicalSource instance, or None if type is unknown
        """
        if source_def.type == "video":
            return self._create_video_source(source_def)
        elif source_def.type == "audio_visualizer":
            return self._create_audio_visualizer_source(source_def)
        else:
            logger.warning(f"Unknown source type: {source_def.type}")
            return None

    def _create_video_source(self, source_def: SourceDefinition) -> VideoLogicalSource:
        """Create a video logical source."""
        config = VideoLogicalSourceConfig(
            name=source_def.name,
            description=source_def.description,
            dimensions=source_def.dimensions,
            rate=source_def.rate,
            fit_mode=source_def.fit_mode,
            gamma=source_def.gamma,
        )
        return VideoLogicalSource(self._context, config)

    def _create_audio_visualizer_source(
        self, source_def: SourceDefinition
    ) -> AudioVisualizerSource:
        """Create an audio visualizer logical source."""
        config = AudioVisualizerSourceConfig(
            name=source_def.name,
            description=source_def.description,
            dimensions=source_def.dimensions,
            rate=source_def.rate,
            visualizer_type=source_def.visualizer_type,
            color_mode=source_def.color_mode,
            fft_size=source_def.fft_size,
            smoothing=source_def.smoothing,
            gain=source_def.gain,
            beat_sensitivity=source_def.beat_sensitivity,
        )
        return AudioVisualizerSource(self._context, config)

    async def _frame_loop(self) -> None:
        """Main frame reading loop - reads frames from media and updates audio buffer."""
        frame_interval = 1.0 / self._context.frame_rate
        last_frame_time = asyncio.get_event_loop().time()

        logger.info(
            f"Frame loop started for {self._config.media_path} "
            f"at {self._context.frame_rate:.1f} fps"
        )

        while self._running:
            now = asyncio.get_event_loop().time()
            elapsed = now - last_frame_time

            if elapsed < frame_interval:
                await asyncio.sleep(frame_interval - elapsed)
                continue

            last_frame_time = now

            # Read next frame (updates context and audio buffer)
            try:
                frame = await self._context.read_frame()
                if frame is None and not self._context.loop and not self._context.is_live:
                    # End of media, non-looping (but not live sources like microphone)
                    logger.info("End of media reached")
                    break
            except Exception as e:
                logger.error(f"Error reading frame: {e}")
                await asyncio.sleep(0.1)

    async def start(self) -> None:
        """Start the source group.

        This starts the shared context and all logical sources.
        """
        if self._running:
            return

        logger.info(f"Starting MediaSourceGroup with {len(self._config.sources)} sources")
        logger.info(f"Media: {self._config.media_path}")

        # Create shared context
        self._context = self._create_context()
        await self._context.start()

        # Create and start sources
        self._sources = self._create_sources()
        for source in self._sources:
            try:
                await source.start()
            except Exception as e:
                logger.error(f"Failed to start source {source.config.name}: {e}")

        self._running = True

        # Start frame reading loop
        self._frame_task = asyncio.create_task(self._frame_loop())

        logger.info(f"MediaSourceGroup started with {len(self._sources)} sources:")
        for source in self._sources:
            logger.info(
                f"  - {source.config.name}: {source.width}x{source.height} "
                f"@ {source.config.rate}fps ({source.config.source_type})"
            )

    async def stop(self) -> None:
        """Stop the source group.

        This stops all logical sources and the shared context.
        """
        if not self._running:
            return

        logger.info("Stopping MediaSourceGroup")
        self._running = False

        # Cancel frame loop
        if self._frame_task:
            self._frame_task.cancel()
            try:
                await self._frame_task
            except asyncio.CancelledError:
                pass

        # Stop all sources
        for source in self._sources:
            try:
                await source.stop()
            except Exception as e:
                logger.error(f"Error stopping source {source.config.name}: {e}")

        # Stop context
        if self._context:
            await self._context.stop()

        self._sources.clear()
        self._context = None
        self._shutdown_event.set()

        logger.info("MediaSourceGroup stopped")

    async def run_forever(self) -> None:
        """Run until interrupted.

        Starts the source group and waits until stop() is called
        or a signal is received.
        """
        await self.start()

        # Set up signal handlers
        loop = asyncio.get_event_loop()

        def signal_handler():
            logger.info("Received shutdown signal")
            asyncio.create_task(self.stop())

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        # Wait for shutdown
        await self._shutdown_event.wait()

    def get_stats(self) -> dict[str, Any]:
        """Get statistics for the source group.

        Returns:
            Dictionary with group and source statistics
        """
        return {
            "media_path": self._config.media_path,
            "running": self._running,
            "context": self._context.get_state() if self._context else None,
            "sources": [source.get_stats() for source in self._sources],
        }

    def get_source_by_name(self, name: str) -> LogicalSource | None:
        """Get a source by its name.

        Args:
            name: Source name

        Returns:
            LogicalSource if found, None otherwise
        """
        for source in self._sources:
            if source.config.name == name:
                return source
        return None


async def run_source_group(config: SourceGroupConfig) -> None:
    """Run a source group until interrupted.

    Args:
        config: Source group configuration
    """
    group = MediaSourceGroup(config)
    await group.run_forever()


async def run_source_group_from_yaml(yaml_path: str | Path) -> None:
    """Run a source group from a YAML configuration file.

    Args:
        yaml_path: Path to the YAML configuration file
    """
    group = MediaSourceGroup.from_yaml(yaml_path)
    await group.run_forever()
