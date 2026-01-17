"""Base class for media inputs."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

import numpy as np


class FitMode(Enum):
    """How to fit source content to target dimensions."""

    CONTAIN = "contain"  # Scale to fit, preserve aspect, letterbox
    COVER = "cover"  # Scale to fill, preserve aspect, crop edges
    STRETCH = "stretch"  # Stretch to exact size, ignore aspect
    TILE = "tile"  # Repeat pattern if smaller
    CENTER = "center"  # No scaling, center in output


class MediaInput(ABC):
    """Base class for all media inputs.

    Subclasses implement specific input types (image, video, camera, etc.)
    """

    input_type: str = "unknown"

    def __init__(
        self,
        path: str | None = None,
        fit_mode: FitMode = FitMode.CONTAIN,
        loop: bool = True,
        speed: float = 1.0,
        **kwargs: Any,
    ):
        """Initialize media input.

        Args:
            path: File path or URL for the input
            fit_mode: How to fit content to target dimensions
            loop: Whether to loop playback
            speed: Playback speed multiplier
            **kwargs: Additional input-specific parameters
        """
        self.path = path
        self.fit_mode = fit_mode
        self.loop = loop
        self.speed = speed
        self.extra_params = kwargs

        self._opened = False
        self._position = 0.0  # Current position in seconds
        self._frame_index = 0

    @abstractmethod
    def open(self) -> None:
        """Open/initialize the input source.

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If input is invalid
            RuntimeError: If input cannot be opened
        """
        pass

    @abstractmethod
    def read_frame(self) -> np.ndarray | None:
        """Read next frame from the input.

        Returns:
            Frame as numpy array (H, W, 3) RGB uint8, or None if no frame available
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Close/cleanup the input source."""
        pass

    @property
    @abstractmethod
    def frame_rate(self) -> float:
        """Native frame rate of the source in Hz."""
        pass

    @property
    @abstractmethod
    def duration(self) -> float | None:
        """Duration in seconds, or None for live sources."""
        pass

    @property
    @abstractmethod
    def native_dimensions(self) -> tuple[int, int]:
        """Native dimensions (width, height) of the source."""
        pass

    def seek(self, position: float) -> bool:
        """Seek to position in seconds.

        Args:
            position: Target position in seconds

        Returns:
            True if seek succeeded, False if not supported
        """
        return False

    def reset(self) -> None:
        """Reset to beginning of input."""
        self.seek(0.0)
        self._position = 0.0
        self._frame_index = 0

    @property
    def is_live(self) -> bool:
        """True for live sources (camera, stream, screen)."""
        return self.duration is None

    @property
    def is_opened(self) -> bool:
        """True if input is currently opened."""
        return self._opened

    @property
    def position(self) -> float:
        """Current playback position in seconds."""
        return self._position

    @property
    def frame_index(self) -> int:
        """Current frame index."""
        return self._frame_index

    def __enter__(self) -> "MediaInput":
        """Context manager entry."""
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(path={self.path!r}, fit_mode={self.fit_mode.value})"
