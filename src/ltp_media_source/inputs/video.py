"""Video file input using OpenCV with optional audio extraction via PyAV."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, TYPE_CHECKING

import numpy as np

from ltp_media_source.inputs.base import MediaInput, FitMode

if TYPE_CHECKING:
    from ltp_media_source.shared_context import SharedMediaContext

logger = logging.getLogger(__name__)

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False
    logger.warning("OpenCV not available - video input disabled")

try:
    import av
    HAS_PYAV = True
except ImportError:
    HAS_PYAV = False
    logger.debug("PyAV not available - audio extraction disabled")


class VideoInput(MediaInput):
    """Video file input (MP4, AVI, MOV, MKV, WebM)."""

    input_type = "video"

    def __init__(
        self,
        path: str,
        fit_mode: FitMode = FitMode.CONTAIN,
        loop: bool = True,
        speed: float = 1.0,
        start_time: float = 0.0,
        end_time: float | None = None,
        extract_audio: bool = True,
        audio_sample_rate: int = 44100,
        **kwargs: Any,
    ):
        """Initialize video input.

        Args:
            path: Path to video file or URL
            fit_mode: How to fit frames to target dimensions
            loop: Whether to loop playback
            speed: Playback speed multiplier
            start_time: Start position in seconds
            end_time: End position in seconds (None = end of file)
            extract_audio: Whether to extract audio for visualization
            audio_sample_rate: Target sample rate for audio extraction
        """
        if not HAS_OPENCV:
            raise RuntimeError("OpenCV required for video input. Install opencv-python-headless")

        super().__init__(path=path, fit_mode=fit_mode, loop=loop, speed=speed, **kwargs)

        self.start_time = start_time
        self.end_time = end_time
        self._extract_audio = extract_audio and HAS_PYAV
        self._audio_sample_rate = audio_sample_rate

        self._cap: Any = None  # cv2.VideoCapture
        self._width = 0
        self._height = 0
        self._fps = 30.0
        self._total_frames = 0
        self._duration_sec = 0.0

        # Audio extraction state
        self._audio_container: Any = None  # av.container.InputContainer
        self._audio_stream: Any = None  # av.audio.stream.AudioStream
        self._audio_resampler: Any = None  # av.audio.resampler.AudioResampler
        self._has_audio = False
        self._shared_context: SharedMediaContext | None = None
        self._audio_samples_per_frame = 0
        self._last_audio_pts: int = 0
        self._audio_time_base: float = 0.0
        self._audio_frame_buffer: list = []  # Buffer for decoded audio frames
        self._audio_lock = threading.Lock()

    def open(self) -> None:
        """Open the video file."""
        if self._opened:
            return

        if not self.path:
            raise ValueError("No path specified")

        # Check if it's a file (not a URL)
        if not self.path.startswith(("http://", "https://", "rtsp://")):
            path = Path(self.path)
            if not path.exists():
                raise FileNotFoundError(f"Video not found: {self.path}")

        try:
            self._cap = cv2.VideoCapture(self.path)

            if not self._cap.isOpened():
                raise RuntimeError(f"Failed to open video: {self.path}")

            # Get video properties
            self._width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self._height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
            self._total_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self._duration_sec = self._total_frames / self._fps if self._fps > 0 else 0

            # Calculate audio samples per video frame
            self._audio_samples_per_frame = int(self._audio_sample_rate / self._fps)

            # Open audio stream if requested
            if self._extract_audio:
                self._open_audio_stream()

            # Seek to start position
            if self.start_time > 0:
                self.seek(self.start_time)

            self._opened = True
            audio_info = ", with audio" if self._has_audio else ", no audio"
            logger.info(
                f"Opened video: {self.path} ({self._width}x{self._height}, "
                f"{self._fps:.1f}fps, {self._duration_sec:.1f}s{audio_info})"
            )

        except Exception as e:
            if self._cap:
                self._cap.release()
                self._cap = None
            self._close_audio_stream()
            raise RuntimeError(f"Failed to load video: {e}") from e

    def _open_audio_stream(self) -> None:
        """Open audio stream using PyAV for extraction."""
        if not HAS_PYAV:
            return

        try:
            self._audio_container = av.open(self.path)

            # Find audio stream
            audio_streams = [s for s in self._audio_container.streams if s.type == "audio"]
            if not audio_streams:
                logger.debug(f"No audio stream found in: {self.path}")
                self._audio_container.close()
                self._audio_container = None
                return

            self._audio_stream = audio_streams[0]
            self._audio_time_base = float(self._audio_stream.time_base)

            # Create resampler to convert to target format
            # Output: stereo, float32 (planar), target sample rate
            self._audio_resampler = av.AudioResampler(
                format="fltp",  # float32 planar
                layout="stereo",
                rate=self._audio_sample_rate,
            )

            self._has_audio = True
            logger.debug(
                f"Opened audio stream: {self._audio_stream.rate}Hz "
                f"{self._audio_stream.channels}ch -> {self._audio_sample_rate}Hz stereo"
            )

        except Exception as e:
            logger.warning(f"Failed to open audio stream: {e}")
            self._close_audio_stream()

    def _close_audio_stream(self) -> None:
        """Close audio stream."""
        with self._audio_lock:
            self._audio_frame_buffer.clear()
            if self._audio_container:
                try:
                    self._audio_container.close()
                except Exception:
                    pass
                self._audio_container = None
            self._audio_stream = None
            self._audio_resampler = None
            self._has_audio = False

    def read_frame(self) -> np.ndarray | None:
        """Read next frame from video."""
        if not self._opened or self._cap is None:
            return None

        ret, frame = self._cap.read()

        if not ret:
            # End of video
            if self.loop:
                # Reset to start
                start_frame = int(self.start_time * self._fps) if self.start_time > 0 else 0
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
                self._position = self.start_time
                self._frame_index = 0

                # Reset audio stream too
                self._seek_audio(self.start_time)

                ret, frame = self._cap.read()
                if not ret:
                    return None
            else:
                return None

        # Check end time
        if self.end_time is not None:
            current_time = self._cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if current_time >= self.end_time:
                if self.loop:
                    start_frame = int(self.start_time * self._fps) if self.start_time > 0 else 0
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
                    self._seek_audio(self.start_time)
                    ret, frame = self._cap.read()
                    if not ret:
                        return None
                else:
                    return None

        # Convert BGR to RGB
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Update position
        self._position = self._cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        self._frame_index = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES))

        # Extract audio samples for this frame's time window
        if self._has_audio and self._shared_context is not None:
            self._extract_audio_for_frame(self._position)

        return frame_rgb

    def _extract_audio_for_frame(self, video_time: float) -> None:
        """Extract audio samples corresponding to the current video frame.

        Args:
            video_time: Current video position in seconds
        """
        if not self._has_audio or self._audio_container is None:
            return

        try:
            # Decode audio frames up to the current video time
            samples_needed = self._audio_samples_per_frame
            collected_samples = []

            with self._audio_lock:
                # First, try to use buffered audio frames
                while self._audio_frame_buffer and len(collected_samples) < samples_needed:
                    audio_frame = self._audio_frame_buffer[0]
                    frame_time = float(audio_frame.pts * self._audio_time_base) if audio_frame.pts else 0

                    # If this frame is past our video time, stop
                    if frame_time > video_time + (1.0 / self._fps):
                        break

                    self._audio_frame_buffer.pop(0)

                    # Resample to target format
                    resampled = self._audio_resampler.resample(audio_frame)
                    if resampled:
                        for r_frame in resampled:
                            # Convert to numpy: shape will be (channels, samples) for planar
                            arr = r_frame.to_ndarray()
                            # Transpose to (samples, channels)
                            if arr.ndim == 2:
                                arr = arr.T
                            collected_samples.append(arr)

                # Decode more frames if needed
                if len(collected_samples) < samples_needed // self._audio_samples_per_frame:
                    try:
                        for packet in self._audio_container.demux(self._audio_stream):
                            for audio_frame in packet.decode():
                                frame_time = float(audio_frame.pts * self._audio_time_base) if audio_frame.pts else 0

                                # If frame is past our target, buffer it for later
                                if frame_time > video_time + (1.0 / self._fps):
                                    self._audio_frame_buffer.append(audio_frame)
                                    break

                                # Resample and collect
                                resampled = self._audio_resampler.resample(audio_frame)
                                if resampled:
                                    for r_frame in resampled:
                                        arr = r_frame.to_ndarray()
                                        if arr.ndim == 2:
                                            arr = arr.T
                                        collected_samples.append(arr)

                            # Check if we have enough or buffered a future frame
                            if self._audio_frame_buffer:
                                break

                    except av.error.EOFError:
                        pass
                    except StopIteration:
                        pass

            # Concatenate and send to shared context
            if collected_samples:
                all_samples = np.concatenate(collected_samples, axis=0)
                self._shared_context.write_audio_samples(all_samples)

        except Exception as e:
            logger.debug(f"Audio extraction error: {e}")

    def _seek_audio(self, position: float) -> None:
        """Seek audio stream to position."""
        if not self._has_audio or self._audio_container is None:
            return

        with self._audio_lock:
            try:
                # Clear buffered frames
                self._audio_frame_buffer.clear()

                # Seek audio container
                # Convert position to stream time base
                timestamp = int(position / self._audio_time_base)
                self._audio_container.seek(timestamp, stream=self._audio_stream)

            except Exception as e:
                logger.debug(f"Audio seek error: {e}")

    def close(self) -> None:
        """Release video resources."""
        if self._cap:
            self._cap.release()
            self._cap = None
        self._close_audio_stream()
        self._opened = False
        logger.debug(f"Closed video: {self.path}")

    def seek(self, position: float) -> bool:
        """Seek to position in seconds."""
        if not self._opened or self._cap is None:
            return False

        # Clamp to valid range
        position = max(0, min(position, self._duration_sec))

        # Seek by frame number (more reliable than time)
        frame_num = int(position * self._fps)
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)

        self._position = position
        self._frame_index = frame_num

        # Seek audio stream too
        self._seek_audio(position)

        return True

    def reset(self) -> None:
        """Reset to start of video."""
        if self._cap:
            start_frame = int(self.start_time * self._fps) if self.start_time > 0 else 0
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        self._position = self.start_time
        self._frame_index = 0
        self._seek_audio(self.start_time)

    @property
    def frame_rate(self) -> float:
        """Video frame rate."""
        return self._fps

    @property
    def duration(self) -> float | None:
        """Video duration in seconds."""
        if self._duration_sec > 0:
            effective_end = self.end_time if self.end_time else self._duration_sec
            return effective_end - self.start_time
        return None

    @property
    def native_dimensions(self) -> tuple[int, int]:
        """Video dimensions."""
        return (self._width, self._height)

    @property
    def total_frames(self) -> int:
        """Total frame count."""
        return self._total_frames

    @property
    def has_audio(self) -> bool:
        """True if audio extraction is available for this video."""
        return self._has_audio

    @property
    def audio_sample_rate(self) -> int:
        """Audio sample rate in Hz."""
        return self._audio_sample_rate

    def set_shared_context(self, context: SharedMediaContext | None) -> None:
        """Set the shared media context for audio extraction.

        When a shared context is set, audio samples will be written to
        its audio buffer during video playback.

        Args:
            context: SharedMediaContext to receive audio samples, or None to disable
        """
        self._shared_context = context
