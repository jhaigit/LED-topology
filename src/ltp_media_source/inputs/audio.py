"""Audio file input using PyAV.

Supports standalone audio files (MP3, WAV, FLAC, OGG, etc.) for visualization
without requiring a video file.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

import numpy as np

from ltp_media_source.inputs.base import MediaInput, FitMode

if TYPE_CHECKING:
    from ltp_media_source.shared_context import SharedMediaContext

logger = logging.getLogger(__name__)

try:
    import av
    HAS_PYAV = True
except ImportError:
    HAS_PYAV = False
    logger.warning("PyAV not available - audio file input disabled")


class AudioFileInput(MediaInput):
    """Audio file input (MP3, WAV, FLAC, OGG, AAC, etc.).

    This input type loads audio files for visualization. Since there's no video,
    it generates blank frames at the specified rate while providing audio samples
    for analysis and visualization.
    """

    input_type = "audio_file"

    def __init__(
        self,
        path: str,
        loop: bool = True,
        speed: float = 1.0,
        start_time: float = 0.0,
        end_time: float | None = None,
        sample_rate: int = 44100,
        frame_rate: float = 30.0,
        **kwargs: Any,
    ):
        """Initialize audio file input.

        Args:
            path: Path to audio file
            loop: Whether to loop playback
            speed: Playback speed multiplier
            start_time: Start position in seconds
            end_time: End position in seconds (None = end of file)
            sample_rate: Target sample rate for audio
            frame_rate: Frame rate for visualization updates
        """
        if not HAS_PYAV:
            raise RuntimeError("PyAV required for audio file input. Install av package.")

        super().__init__(path=path, fit_mode=FitMode.CONTAIN, loop=loop, speed=speed, **kwargs)

        self.start_time = start_time
        self.end_time = end_time
        self._sample_rate = sample_rate
        self._frame_rate = frame_rate

        # Audio state
        self._container: Any = None  # av.container.InputContainer
        self._audio_stream: Any = None  # av.audio.stream.AudioStream
        self._resampler: Any = None  # av.audio.resampler.AudioResampler
        self._duration_sec = 0.0
        self._native_sample_rate = 0
        self._native_channels = 0

        # Playback state
        self._shared_context: SharedMediaContext | None = None
        self._samples_per_frame = 0
        self._audio_buffer: np.ndarray | None = None
        self._buffer_position = 0
        self._playback_time = 0.0
        self._last_frame_time = 0.0
        self._lock = threading.Lock()

        # Pre-decoded audio (for small files)
        self._decoded_audio: np.ndarray | None = None
        self._decode_all = True  # Decode entire file into memory

    @property
    def has_audio(self) -> bool:
        """Always True for audio files."""
        return True

    @property
    def frame_rate(self) -> float:
        """Frame rate for visualization."""
        return self._frame_rate * self._speed

    @property
    def duration(self) -> float | None:
        """Audio duration in seconds."""
        if self._duration_sec > 0:
            effective_end = self.end_time if self.end_time else self._duration_sec
            return (effective_end - self.start_time) / self._speed
        return None

    @property
    def native_dimensions(self) -> tuple[int, int]:
        """No video dimensions for audio-only input."""
        return (0, 0)

    @property
    def is_live(self) -> bool:
        """Audio files are not live sources."""
        return False

    @property
    def sample_rate(self) -> int:
        """Audio sample rate."""
        return self._sample_rate

    def set_shared_context(self, context: SharedMediaContext) -> None:
        """Set the shared context for audio buffer access."""
        self._shared_context = context

    def open(self) -> None:
        """Open the audio file."""
        if self._opened:
            return

        if not self.path:
            raise ValueError("No path specified")

        path = Path(self.path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {self.path}")

        try:
            self._container = av.open(self.path)

            # Find audio stream
            audio_streams = [s for s in self._container.streams if s.type == "audio"]
            if not audio_streams:
                raise RuntimeError(f"No audio stream found in: {self.path}")

            self._audio_stream = audio_streams[0]

            # Get audio properties
            self._native_sample_rate = self._audio_stream.rate
            self._native_channels = self._audio_stream.channels
            self._duration_sec = float(self._audio_stream.duration * self._audio_stream.time_base)

            # Create resampler to convert to target format (stereo float32)
            self._resampler = av.AudioResampler(
                format="fltp",  # float32 planar
                layout="stereo",
                rate=self._sample_rate,
            )

            # Calculate samples per visualization frame
            self._samples_per_frame = int(self._sample_rate / self._frame_rate)

            # Pre-decode entire file if small enough (< 10 minutes)
            if self._decode_all and self._duration_sec < 600:
                self._decode_entire_file()

            # Seek to start position if needed
            if self.start_time > 0:
                self.seek(self.start_time)

            self._playback_time = self.start_time
            self._last_frame_time = time.monotonic()
            self._opened = True

            logger.info(
                f"Opened audio file: {self.path} "
                f"({self._native_sample_rate}Hz {self._native_channels}ch, "
                f"{self._duration_sec:.1f}s)"
            )

        except Exception as e:
            self.close()
            raise RuntimeError(f"Failed to load audio file: {e}") from e

    def _decode_entire_file(self) -> None:
        """Decode entire audio file into memory for faster access."""
        try:
            all_samples = []

            for packet in self._container.demux(self._audio_stream):
                for frame in packet.decode():
                    # Resample to target format
                    resampled = self._resampler.resample(frame)
                    for resampled_frame in resampled:
                        # Convert to interleaved stereo
                        left = np.frombuffer(resampled_frame.planes[0], dtype=np.float32)
                        right = np.frombuffer(resampled_frame.planes[1], dtype=np.float32)
                        interleaved = np.column_stack((left, right))
                        all_samples.append(interleaved)

            if all_samples:
                self._decoded_audio = np.vstack(all_samples)
                logger.debug(
                    f"Pre-decoded {len(self._decoded_audio)} samples "
                    f"({len(self._decoded_audio) / self._sample_rate:.1f}s)"
                )

            # Reset stream position
            self._container.seek(0)

        except Exception as e:
            logger.warning(f"Failed to pre-decode audio: {e}")
            self._decoded_audio = None

    def read_frame(self) -> np.ndarray | None:
        """Read next 'frame' - actually reads audio samples for the frame duration.

        Since this is audio-only, we return None for the video frame but
        write audio samples to the shared context.
        """
        if not self._opened:
            return None

        # Calculate elapsed time since last frame
        now = time.monotonic()
        elapsed = (now - self._last_frame_time) * self._speed
        self._last_frame_time = now

        # Update playback position
        self._playback_time += elapsed
        self._position = self._playback_time

        # Check for end of file / looping
        effective_end = self.end_time if self.end_time else self._duration_sec
        if self._playback_time >= effective_end:
            if self.loop:
                self._playback_time = self.start_time
                self._position = self.start_time
                self._buffer_position = int(self.start_time * self._sample_rate)
            else:
                return None

        # Extract audio samples for this frame
        if self._shared_context is not None:
            samples = self._get_audio_samples(self._samples_per_frame)
            if samples is not None and len(samples) > 0:
                self._shared_context.write_audio_samples(samples)

        # Return None since there's no video frame
        # The caller should check if this is an audio-only source
        return None

    def _get_audio_samples(self, num_samples: int) -> np.ndarray | None:
        """Get audio samples from current position."""
        with self._lock:
            if self._decoded_audio is not None:
                # Use pre-decoded audio
                start_idx = int(self._playback_time * self._sample_rate)
                end_idx = start_idx + num_samples

                if start_idx >= len(self._decoded_audio):
                    return None

                end_idx = min(end_idx, len(self._decoded_audio))
                samples = self._decoded_audio[start_idx:end_idx]

                # Pad if needed
                if len(samples) < num_samples:
                    padding = np.zeros((num_samples - len(samples), 2), dtype=np.float32)
                    samples = np.vstack([samples, padding])

                return samples.astype(np.float32)

            # Fall back to streaming decode (not implemented for simplicity)
            return None

    def seek(self, position: float) -> bool:
        """Seek to position in seconds."""
        if not self._opened:
            return False

        try:
            # Clamp to valid range
            effective_end = self.end_time if self.end_time else self._duration_sec
            position = max(self.start_time, min(position, effective_end))

            self._playback_time = position
            self._position = position
            self._buffer_position = int(position * self._sample_rate)

            if self._container and not self._decoded_audio:
                # Seek in container for streaming mode
                timestamp = int(position / self._audio_stream.time_base)
                self._container.seek(timestamp, stream=self._audio_stream)

            return True

        except Exception as e:
            logger.warning(f"Seek failed: {e}")
            return False

    def close(self) -> None:
        """Close the audio file."""
        with self._lock:
            if self._container:
                try:
                    self._container.close()
                except Exception:
                    pass
                self._container = None

            self._audio_stream = None
            self._resampler = None
            self._decoded_audio = None
            self._opened = False

        logger.debug(f"Closed audio file: {self.path}")


class AudioOnlyContext:
    """Minimal context for audio-only playback without SharedMediaContext.

    This allows running audio visualizations without the full media source
    infrastructure.
    """

    def __init__(
        self,
        audio_input: AudioFileInput,
        buffer_duration: float = 2.0,
    ):
        from ltp_media_source.audio_buffer import AudioRingBuffer

        self._input = audio_input
        self._buffer = AudioRingBuffer(
            duration_sec=buffer_duration,
            sample_rate=audio_input.sample_rate,
            channels=2,
        )

        # Set ourselves as the audio write target
        audio_input._shared_context = self

    def write_audio_samples(self, samples: np.ndarray) -> None:
        """Write samples to the audio buffer."""
        self._buffer.write(samples)

    def get_audio_samples(self, num_samples: int) -> np.ndarray:
        """Get recent audio samples."""
        return self._buffer.read_recent(num_samples)

    def get_audio_mono(self, num_samples: int) -> np.ndarray:
        """Get recent audio samples as mono."""
        samples = self._buffer.read_recent(num_samples)
        return self._buffer.get_mono(samples)

    @property
    def audio_buffer(self):
        """The audio ring buffer."""
        return self._buffer
