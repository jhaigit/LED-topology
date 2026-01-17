"""Video file input using OpenCV."""

import logging
from pathlib import Path
from typing import Any

import numpy as np

from ltp_media_source.inputs.base import MediaInput, FitMode

logger = logging.getLogger(__name__)

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False
    logger.warning("OpenCV not available - video input disabled")


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
        """
        if not HAS_OPENCV:
            raise RuntimeError("OpenCV required for video input. Install opencv-python-headless")

        super().__init__(path=path, fit_mode=fit_mode, loop=loop, speed=speed, **kwargs)

        self.start_time = start_time
        self.end_time = end_time

        self._cap: Any = None  # cv2.VideoCapture
        self._width = 0
        self._height = 0
        self._fps = 30.0
        self._total_frames = 0
        self._duration_sec = 0.0

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

            # Seek to start position
            if self.start_time > 0:
                self.seek(self.start_time)

            self._opened = True
            logger.info(
                f"Opened video: {self.path} ({self._width}x{self._height}, "
                f"{self._fps:.1f}fps, {self._duration_sec:.1f}s)"
            )

        except Exception as e:
            if self._cap:
                self._cap.release()
                self._cap = None
            raise RuntimeError(f"Failed to load video: {e}") from e

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

        return frame_rgb

    def close(self) -> None:
        """Release video resources."""
        if self._cap:
            self._cap.release()
            self._cap = None
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

        return True

    def reset(self) -> None:
        """Reset to start of video."""
        if self._cap:
            start_frame = int(self.start_time * self._fps) if self.start_time > 0 else 0
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        self._position = self.start_time
        self._frame_index = 0

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
