"""Shared media context for multiple logical sources.

Provides synchronized access to a single media decoder for multiple
logical sources (video, audio visualizers, etc.).
"""

import asyncio
import logging
from typing import Any, Callable, Protocol

import numpy as np

from ltp_media_source.audio_buffer import AudioRingBuffer
from ltp_media_source.inputs.base import MediaInput

logger = logging.getLogger(__name__)


class StateChangeCallback(Protocol):
    """Protocol for state change callback functions."""

    def __call__(self, state: dict[str, Any]) -> None:
        """Called when shared state changes.

        Args:
            state: Dictionary with changed state values
        """
        ...


class SharedMediaContext:
    """Shared state for multiple logical sources from the same media.

    This class wraps a single MediaInput and provides thread-safe access
    to video frames and audio samples. Multiple LogicalSource instances
    can share this context to ensure synchronized playback.

    The context manages:
    - Single media decoder instance
    - Shared audio buffer for analysis
    - Playback state (position, playing, paused, loop, speed)
    - Synchronization primitives for async access
    """

    def __init__(
        self,
        media_input: MediaInput,
        audio_sample_rate: int = 44100,
        audio_channels: int = 2,
        audio_buffer_duration: float = 2.0,
    ):
        """Initialize shared media context.

        Args:
            media_input: The media input to wrap (VideoInput, etc.)
            audio_sample_rate: Sample rate for audio buffer
            audio_channels: Number of audio channels (1=mono, 2=stereo)
            audio_buffer_duration: Seconds of audio to buffer
        """
        self._input = media_input
        self._audio_sample_rate = audio_sample_rate
        self._audio_channels = audio_channels

        # Audio buffer for visualization
        self._audio_buffer = AudioRingBuffer(
            duration_sec=audio_buffer_duration,
            sample_rate=audio_sample_rate,
            channels=audio_channels,
        )

        # Current video frame (updated by read loop)
        self._current_frame: np.ndarray | None = None
        self._frame_number: int = 0

        # Playback state
        self._playing: bool = False
        self._paused: bool = False
        self._loop: bool = media_input.loop
        self._speed: float = media_input.speed
        self._position: float = 0.0

        # Synchronization
        self._lock = asyncio.Lock()
        self._frame_event = asyncio.Event()
        self._state_changed = asyncio.Event()

        # Running state
        self._running: bool = False

        # State change observers (for control coordination)
        self._state_observers: list[StateChangeCallback] = []

    @property
    def media_path(self) -> str | None:
        """Path to the media file."""
        return self._input.path

    @property
    def audio_buffer(self) -> AudioRingBuffer:
        """Audio ring buffer for visualization."""
        return self._audio_buffer

    @property
    def audio_sample_rate(self) -> int:
        """Audio sample rate in Hz."""
        return self._audio_sample_rate

    @property
    def audio_channels(self) -> int:
        """Number of audio channels."""
        return self._audio_channels

    @property
    def position(self) -> float:
        """Current playback position in seconds."""
        return self._position

    @property
    def duration(self) -> float | None:
        """Total duration in seconds, or None for live sources."""
        return self._input.duration

    @property
    def playing(self) -> bool:
        """True if playback is active (not paused)."""
        return self._playing and not self._paused

    @property
    def paused(self) -> bool:
        """True if playback is paused."""
        return self._paused

    @property
    def loop(self) -> bool:
        """True if looping is enabled."""
        return self._loop

    @loop.setter
    def loop(self, value: bool) -> None:
        """Set loop mode."""
        self._loop = value
        self._input.loop = value
        self._notify_state_change(loop=value)

    @property
    def speed(self) -> float:
        """Playback speed multiplier."""
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        """Set playback speed."""
        self._speed = max(0.1, min(4.0, value))
        self._input.speed = self._speed
        self._notify_state_change(speed=self._speed)

    @property
    def frame_rate(self) -> float:
        """Frame rate of the media."""
        return self._input.frame_rate

    @property
    def native_dimensions(self) -> tuple[int, int]:
        """Native dimensions (width, height) of the media."""
        return self._input.native_dimensions

    @property
    def is_live(self) -> bool:
        """True if this is a live source (camera, stream)."""
        return self._input.is_live

    @property
    def has_audio(self) -> bool:
        """True if audio extraction is available.

        This is determined by checking if the input supports audio extraction.
        """
        return hasattr(self._input, "has_audio") and self._input.has_audio

    # =========================================================================
    # State Change Observers (for control coordination)
    # =========================================================================

    def add_state_observer(self, callback: StateChangeCallback) -> None:
        """Register a callback to be notified of state changes.

        When shared playback state changes (play/pause/seek/loop/speed),
        all registered observers are notified with the changed values.
        This enables control coordination across multiple logical sources.

        Args:
            callback: Function to call with state change dictionary
        """
        if callback not in self._state_observers:
            self._state_observers.append(callback)
            logger.debug(f"Added state observer: {callback}")

    def remove_state_observer(self, callback: StateChangeCallback) -> None:
        """Unregister a state change callback.

        Args:
            callback: Previously registered callback to remove
        """
        if callback in self._state_observers:
            self._state_observers.remove(callback)
            logger.debug(f"Removed state observer: {callback}")

    def _notify_state_change(self, **changes: Any) -> None:
        """Notify all observers of state changes.

        Args:
            **changes: Key-value pairs of changed state
        """
        if not changes or not self._state_observers:
            return

        logger.debug(f"Notifying {len(self._state_observers)} observers of: {changes}")

        for callback in self._state_observers:
            try:
                callback(changes)
            except Exception as e:
                logger.error(f"State observer callback failed: {e}")

    # =========================================================================
    # Lifecycle Methods
    # =========================================================================

    async def start(self) -> None:
        """Start the media context.

        Opens the media input and prepares for playback.
        """
        async with self._lock:
            if self._running:
                return

            self._input.open()
            self._running = True
            self._playing = True
            self._paused = False
            self._position = self._input.position

            logger.info(f"SharedMediaContext started: {self.media_path}")

    async def stop(self) -> None:
        """Stop the media context.

        Closes the media input and releases resources.
        """
        async with self._lock:
            if not self._running:
                return

            self._running = False
            self._playing = False
            self._input.close()
            self._audio_buffer.clear()
            self._current_frame = None

            logger.info(f"SharedMediaContext stopped: {self.media_path}")

    async def play(self) -> None:
        """Start or resume playback."""
        async with self._lock:
            self._playing = True
            self._paused = False
            self._state_changed.set()
        # Notify observers outside lock to avoid deadlock
        self._notify_state_change(paused=False, playing=True)

    async def pause(self) -> None:
        """Pause playback."""
        async with self._lock:
            self._paused = True
            self._state_changed.set()
        # Notify observers outside lock
        self._notify_state_change(paused=True, playing=False)

    async def toggle_pause(self) -> None:
        """Toggle pause state."""
        async with self._lock:
            self._paused = not self._paused
            paused = self._paused
            self._state_changed.set()
        # Notify observers outside lock
        self._notify_state_change(paused=paused, playing=not paused)

    async def seek(self, position: float) -> bool:
        """Seek to a specific position.

        Args:
            position: Target position in seconds

        Returns:
            True if seek succeeded
        """
        async with self._lock:
            if self._input.seek(position):
                self._position = position
                # Clear audio buffer on seek (old samples are invalid)
                self._audio_buffer.clear()
                self._state_changed.set()
                # Notify observers outside lock
                self._notify_state_change(position=position)
                return True
            return False

    async def read_frame(self) -> np.ndarray | None:
        """Read the next video frame from the media.

        This method should be called by the main render loop.
        It reads a frame, updates the position, and signals waiting sources.

        Returns:
            Video frame as RGB uint8 array (H, W, 3), or None if no frame.
        """
        if not self._running or self._paused:
            return self._current_frame

        async with self._lock:
            frame = self._input.read_frame()

            if frame is not None:
                self._current_frame = frame
                self._position = self._input.position
                self._frame_number += 1

                # Signal that a new frame is available
                self._frame_event.set()
                self._frame_event.clear()

            return frame

    async def get_video_frame(self) -> np.ndarray | None:
        """Get the current video frame.

        This does NOT read a new frame - it returns the last frame
        that was read by read_frame(). Use this from logical sources
        that need the current frame without advancing playback.

        Returns:
            Current video frame, or None if no frame available.
        """
        return self._current_frame

    async def wait_for_frame(self, timeout: float | None = None) -> bool:
        """Wait for a new frame to be available.

        Args:
            timeout: Maximum time to wait in seconds, or None for no timeout

        Returns:
            True if a new frame is available, False on timeout
        """
        try:
            await asyncio.wait_for(self._frame_event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def write_audio_samples(self, samples: np.ndarray) -> None:
        """Write audio samples to the shared buffer.

        This is called by the media input (VideoInput) when it extracts
        audio samples during playback.

        Args:
            samples: Audio samples, shape (num_samples, channels) or (num_samples,)
                    Values should be float32 in range [-1.0, 1.0].
        """
        self._audio_buffer.write(samples)

    async def get_audio_samples(self, num_samples: int) -> np.ndarray:
        """Get recent audio samples for analysis.

        Args:
            num_samples: Number of samples to retrieve

        Returns:
            Audio samples, shape (num_samples, channels), float32.
        """
        return self._audio_buffer.read_recent(num_samples)

    async def get_audio_mono(self, num_samples: int) -> np.ndarray:
        """Get recent audio samples as mono.

        Args:
            num_samples: Number of samples to retrieve

        Returns:
            Mono audio samples, shape (num_samples,), float32.
        """
        samples = self._audio_buffer.read_recent(num_samples)
        return self._audio_buffer.get_mono(samples)

    async def handle_control(self, control_id: str, value: Any) -> bool:
        """Handle a shared control command.

        These controls affect all logical sources sharing this context.

        Args:
            control_id: Control identifier
            value: Control value

        Returns:
            True if control was handled
        """
        if control_id == "play":
            await self.play()
            return True
        elif control_id == "pause":
            await self.pause()
            return True
        elif control_id == "paused":
            if value:
                await self.pause()
            else:
                await self.play()
            return True
        elif control_id == "position" or control_id == "seek":
            return await self.seek(float(value))
        elif control_id == "loop":
            self.loop = bool(value)
            return True
        elif control_id == "speed":
            self.speed = float(value)
            return True

        return False

    def get_state(self) -> dict:
        """Get current playback state.

        Returns:
            Dictionary with current state values.
        """
        return {
            "position": self._position,
            "duration": self.duration,
            "playing": self.playing,
            "paused": self._paused,
            "loop": self._loop,
            "speed": self._speed,
            "frame_number": self._frame_number,
            "has_audio": self.has_audio,
        }
