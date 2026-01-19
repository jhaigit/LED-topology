"""Base class for audio visualizations.

Provides the abstract interface for all visualizers and common utilities.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Tuple, Optional

import numpy as np

from ltp_media_source.visualizers.colors import ColorMode, generate_rainbow_colors

if TYPE_CHECKING:
    from ltp_media_source.audio.analyzer import AudioAnalyzer
    from ltp_media_source.audio.beat_detector import BeatDetector


class Visualizer(ABC):
    """Base class for audio visualizations.

    Subclasses implement render() to produce visual output from audio analysis.
    All visualizers share common parameters for gain, smoothing, and color.
    """

    def __init__(
        self,
        width: int,
        height: int = 1,
        color_mode: ColorMode = ColorMode.RAINBOW,
        base_color: Tuple[int, int, int] = (255, 255, 255),
        smoothing: float = 0.3,
        gain: float = 1.0,
        min_freq: float = 20.0,
        max_freq: float = 20000.0,
    ):
        """Initialize visualizer.

        Args:
            width: Output width in pixels
            height: Output height in pixels (1 for linear strips)
            color_mode: Color mode for rendering
            base_color: Base color for solid mode
            smoothing: Temporal smoothing factor (0-1)
            gain: Input gain multiplier
            min_freq: Minimum frequency to visualize (Hz)
            max_freq: Maximum frequency to visualize (Hz)
        """
        self.width = width
        self.height = height
        self.color_mode = color_mode
        self.base_color = base_color
        self.smoothing = max(0.0, min(1.0, smoothing))
        self.gain = max(0.0, gain)
        self.min_freq = min_freq
        self.max_freq = max_freq

        # Pre-generate colors for common modes
        self._colors = self._generate_colors()

        # Frame buffer for smoothing
        self._last_frame: Optional[np.ndarray] = None

    @property
    def dimensions(self) -> Tuple[int, int]:
        """Output dimensions (width, height)."""
        return (self.width, self.height)

    @property
    def is_linear(self) -> bool:
        """True if this is a 1D linear visualization."""
        return self.height == 1

    @property
    def pixel_count(self) -> int:
        """Total number of output pixels."""
        return self.width * self.height

    def _generate_colors(self) -> np.ndarray:
        """Generate color array based on current mode."""
        return generate_rainbow_colors(self.width)

    def _apply_smoothing(self, frame: np.ndarray) -> np.ndarray:
        """Apply temporal smoothing to a frame.

        Args:
            frame: Current frame

        Returns:
            Smoothed frame
        """
        if self._last_frame is None or self._last_frame.shape != frame.shape:
            self._last_frame = frame.copy()
            return frame

        smoothed = (
            self._last_frame.astype(np.float32) * self.smoothing
            + frame.astype(np.float32) * (1 - self.smoothing)
        ).astype(np.uint8)

        self._last_frame = smoothed.copy()
        return smoothed

    def _create_frame(self) -> np.ndarray:
        """Create an empty output frame.

        Returns:
            Black frame of correct dimensions
        """
        return np.zeros((self.height, self.width, 3), dtype=np.uint8)

    def _apply_gain(self, values: np.ndarray) -> np.ndarray:
        """Apply gain to values.

        Args:
            values: Input values

        Returns:
            Gained values (clipped to 0-1 range)
        """
        return np.clip(values * self.gain, 0, 1)

    @abstractmethod
    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render visualization frame from current audio analysis.

        Args:
            analyzer: AudioAnalyzer with current analysis results
            beat_detector: Optional BeatDetector for beat-reactive effects

        Returns:
            Frame as RGB uint8 array (height, width, 3)
        """
        pass

    def reset(self) -> None:
        """Reset visualizer state."""
        self._last_frame = None

    def set_color_mode(self, mode: ColorMode) -> None:
        """Change the color mode.

        Args:
            mode: New color mode
        """
        self.color_mode = mode
        self._colors = self._generate_colors()

    def set_base_color(self, color: Tuple[int, int, int]) -> None:
        """Change the base color.

        Args:
            color: New base color (R, G, B)
        """
        self.base_color = color


class LinearVisualizer(Visualizer):
    """Base class for 1D linear visualizations."""

    def __init__(
        self,
        width: int,
        **kwargs,
    ):
        """Initialize linear visualizer.

        Args:
            width: Number of pixels
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, height=1, **kwargs)


class MatrixVisualizer(Visualizer):
    """Base class for 2D matrix visualizations."""

    def __init__(
        self,
        width: int,
        height: int,
        **kwargs,
    ):
        """Initialize matrix visualizer.

        Args:
            width: Matrix width
            height: Matrix height
            **kwargs: Additional visualizer parameters
        """
        if height < 2:
            raise ValueError("Matrix visualizer requires height >= 2")
        super().__init__(width=width, height=height, **kwargs)

    @property
    def center(self) -> Tuple[int, int]:
        """Center point of the matrix."""
        return (self.width // 2, self.height // 2)

    def _distance_from_center(self, x: int, y: int) -> float:
        """Calculate distance from center point.

        Args:
            x, y: Pixel coordinates

        Returns:
            Distance from center
        """
        cx, cy = self.center
        return np.sqrt((x - cx) ** 2 + (y - cy) ** 2)

    def _max_distance(self) -> float:
        """Maximum distance from center to any corner."""
        cx, cy = self.center
        return np.sqrt(cx ** 2 + cy ** 2)
