"""Animated GIF input."""

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ltp_media_source.inputs.base import MediaInput, FitMode

logger = logging.getLogger(__name__)


class GifInput(MediaInput):
    """Animated GIF input with frame timing."""

    input_type = "gif"

    def __init__(
        self,
        path: str,
        fit_mode: FitMode = FitMode.CONTAIN,
        loop: bool = True,
        speed: float = 1.0,
        **kwargs: Any,
    ):
        """Initialize GIF input.

        Args:
            path: Path to GIF file
            fit_mode: How to fit frames to target dimensions
            loop: Whether to loop the animation
            speed: Playback speed multiplier
        """
        super().__init__(path=path, fit_mode=fit_mode, loop=loop, speed=speed, **kwargs)

        self._frames: list[np.ndarray] = []
        self._frame_durations: list[float] = []  # Duration of each frame in seconds
        self._width = 0
        self._height = 0
        self._total_duration = 0.0
        self._last_frame_time = 0.0
        self._current_frame_idx = 0

    def open(self) -> None:
        """Load all frames from the GIF."""
        if self._opened:
            return

        if not self.path:
            raise ValueError("No path specified")

        path = Path(self.path)
        if not path.exists():
            raise FileNotFoundError(f"GIF not found: {self.path}")

        try:
            with Image.open(path) as gif:
                self._width, self._height = gif.size

                # Extract all frames
                self._frames = []
                self._frame_durations = []

                try:
                    while True:
                        # Convert frame to RGB
                        frame = gif.convert("RGB")
                        self._frames.append(np.array(frame))

                        # Get frame duration (in milliseconds)
                        duration_ms = gif.info.get("duration", 100)
                        self._frame_durations.append(duration_ms / 1000.0)

                        gif.seek(gif.tell() + 1)
                except EOFError:
                    pass  # End of frames

                self._total_duration = sum(self._frame_durations)

            self._opened = True
            self._last_frame_time = time.monotonic()
            self._current_frame_idx = 0

            logger.info(
                f"Opened GIF: {self.path} ({self._width}x{self._height}, "
                f"{len(self._frames)} frames, {self._total_duration:.2f}s)"
            )

        except Exception as e:
            raise RuntimeError(f"Failed to load GIF: {e}") from e

    def read_frame(self) -> np.ndarray | None:
        """Return the current frame based on timing."""
        if not self._opened or not self._frames:
            return None

        now = time.monotonic()
        elapsed = (now - self._last_frame_time) * self.speed

        # Check if we need to advance frames
        frame_duration = self._frame_durations[self._current_frame_idx]
        if elapsed >= frame_duration:
            self._last_frame_time = now
            self._current_frame_idx += 1
            self._frame_index += 1

            if self._current_frame_idx >= len(self._frames):
                if self.loop:
                    self._current_frame_idx = 0
                    self._position = 0.0
                else:
                    self._current_frame_idx = len(self._frames) - 1

            # Update position
            self._position = sum(self._frame_durations[: self._current_frame_idx])

        return self._frames[self._current_frame_idx].copy()

    def close(self) -> None:
        """Release GIF resources."""
        self._frames = []
        self._frame_durations = []
        self._opened = False
        logger.debug(f"Closed GIF: {self.path}")

    def seek(self, position: float) -> bool:
        """Seek to position in seconds."""
        if not self._opened or not self._frames:
            return False

        # Find frame at this position
        elapsed = 0.0
        for i, duration in enumerate(self._frame_durations):
            if elapsed + duration > position:
                self._current_frame_idx = i
                self._position = position
                self._last_frame_time = time.monotonic()
                return True
            elapsed += duration

        # Past end, go to last frame
        self._current_frame_idx = len(self._frames) - 1
        self._position = self._total_duration
        return True

    def reset(self) -> None:
        """Reset to first frame."""
        self._current_frame_idx = 0
        self._position = 0.0
        self._frame_index = 0
        self._last_frame_time = time.monotonic()

    @property
    def frame_rate(self) -> float:
        """Average frame rate of the GIF."""
        if not self._frame_durations:
            return 10.0

        avg_duration = sum(self._frame_durations) / len(self._frame_durations)
        return 1.0 / avg_duration if avg_duration > 0 else 10.0

    @property
    def duration(self) -> float | None:
        """Total duration of the GIF in seconds."""
        return self._total_duration if self._total_duration > 0 else None

    @property
    def native_dimensions(self) -> tuple[int, int]:
        """Return GIF dimensions."""
        return (self._width, self._height)

    @property
    def frame_count(self) -> int:
        """Total number of frames."""
        return len(self._frames)
