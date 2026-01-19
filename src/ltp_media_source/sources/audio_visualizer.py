"""Audio visualizer logical source - outputs audio visualizations from shared context."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable

import numpy as np

from libltp import EnumControl, EnumOption, NumberControl

from ltp_media_source.audio.analyzer import AudioAnalyzer
from ltp_media_source.audio.beat_detector import BeatDetector
from ltp_media_source.sources.base import LogicalSource, LogicalSourceConfig

if TYPE_CHECKING:
    from ltp_media_source.shared_context import SharedMediaContext

logger = logging.getLogger(__name__)


class Visualizer(ABC):
    """Base class for audio visualizations.

    Subclasses implement render() to produce visual output from audio analysis.
    """

    def __init__(self, width: int, height: int = 1):
        """Initialize visualizer.

        Args:
            width: Output width in pixels
            height: Output height in pixels (1 for linear strips)
        """
        self.width = width
        self.height = height

    @property
    def dimensions(self) -> tuple[int, int]:
        """Output dimensions (width, height)."""
        return (self.width, self.height)

    @property
    def is_linear(self) -> bool:
        """True if this is a 1D linear visualization."""
        return self.height == 1

    @abstractmethod
    def render(
        self,
        analyzer: AudioAnalyzer,
        beat_detector: BeatDetector | None = None,
    ) -> np.ndarray:
        """Render visualization frame from current audio analysis.

        Args:
            analyzer: AudioAnalyzer with current analysis results
            beat_detector: Optional BeatDetector for beat-reactive effects

        Returns:
            Frame as RGB uint8 array (height, width, 3)
        """
        pass


class SimpleSpectrumVisualizer(Visualizer):
    """Simple spectrum visualizer for default/fallback use.

    Shows frequency bands as colored bars. Works for both linear (1D)
    and matrix (2D) displays.
    """

    def __init__(
        self,
        width: int,
        height: int = 1,
        color_mode: str = "rainbow",
        smoothing: float = 0.3,
    ):
        """Initialize spectrum visualizer.

        Args:
            width: Output width (number of bands for linear, or matrix width)
            height: Output height (1 for linear, or matrix height)
            color_mode: Color mode (rainbow, gradient, solid)
            smoothing: Temporal smoothing for band values
        """
        super().__init__(width, height)
        self.color_mode = color_mode
        self.smoothing = smoothing

        # State for smoothing
        self._smoothed_bands: np.ndarray | None = None

        # Pre-compute colors for rainbow mode
        self._colors = self._generate_colors(width)

    def _generate_colors(self, num_colors: int) -> np.ndarray:
        """Generate rainbow colors for spectrum bands."""
        colors = np.zeros((num_colors, 3), dtype=np.uint8)

        for i in range(num_colors):
            # HSV to RGB, hue varies from 0 (red) to 240 (blue)
            hue = (i / num_colors) * 240
            colors[i] = self._hsv_to_rgb(hue, 1.0, 1.0)

        return colors

    def _hsv_to_rgb(self, h: float, s: float, v: float) -> np.ndarray:
        """Convert HSV to RGB."""
        h = h / 60.0
        i = int(h) % 6
        f = h - int(h)
        p = v * (1 - s)
        q = v * (1 - f * s)
        t = v * (1 - (1 - f) * s)

        if i == 0:
            r, g, b = v, t, p
        elif i == 1:
            r, g, b = q, v, p
        elif i == 2:
            r, g, b = p, v, t
        elif i == 3:
            r, g, b = p, q, v
        elif i == 4:
            r, g, b = t, p, v
        else:
            r, g, b = v, p, q

        return np.array([int(r * 255), int(g * 255), int(b * 255)], dtype=np.uint8)

    def render(
        self,
        analyzer: AudioAnalyzer,
        beat_detector: BeatDetector | None = None,
    ) -> np.ndarray:
        """Render spectrum visualization."""
        # Get spectrum bands
        bands = analyzer.get_spectrum_bands(self.width, log_scale=True)

        # Apply smoothing
        if self._smoothed_bands is None:
            self._smoothed_bands = bands.copy()
        else:
            self._smoothed_bands = (
                self._smoothed_bands * self.smoothing
                + bands * (1 - self.smoothing)
            )

        # Normalize bands (0-1 range with some headroom)
        max_val = max(np.max(self._smoothed_bands), 0.001)
        normalized = np.clip(self._smoothed_bands / max_val, 0, 1)

        if self.is_linear:
            # Linear (1D) output: each pixel is one band
            frame = np.zeros((1, self.width, 3), dtype=np.uint8)

            for i in range(self.width):
                intensity = normalized[i]
                color = self._colors[i]
                frame[0, i] = (color * intensity).astype(np.uint8)

            # Apply beat flash if detector available
            if beat_detector and beat_detector.intensity > 0:
                flash = int(beat_detector.intensity * 50)
                frame = np.clip(frame.astype(np.int16) + flash, 0, 255).astype(np.uint8)

            return frame

        else:
            # Matrix (2D) output: bars grow from bottom
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)

            for x in range(self.width):
                bar_height = int(normalized[x] * self.height)
                color = self._colors[x]

                for y in range(bar_height):
                    # Draw from bottom up
                    frame[self.height - 1 - y, x] = color

            # Apply beat flash
            if beat_detector and beat_detector.intensity > 0:
                flash = int(beat_detector.intensity * 50)
                frame = np.clip(frame.astype(np.int16) + flash, 0, 255).astype(np.uint8)

            return frame


class AudioVisualizerSourceConfig(LogicalSourceConfig):
    """Configuration for audio visualizer source."""

    # Audio analysis settings
    fft_size: int = 2048
    smoothing: float = 0.3
    gain: float = 1.0

    # Beat detection settings
    beat_sensitivity: float = 1.5
    beat_decay: float = 0.95

    # Visualizer settings
    visualizer_type: str = "spectrum"  # For future use
    color_mode: str = "rainbow"

    # Source type
    source_type: str = "audio_visualizer"


class AudioVisualizerSource(LogicalSource):
    """Logical source that outputs audio visualizations.

    This source reads audio samples from a SharedMediaContext, analyzes
    them using AudioAnalyzer and BeatDetector, and renders visualizations
    using a Visualizer instance.
    """

    def __init__(
        self,
        context: SharedMediaContext,
        config: AudioVisualizerSourceConfig | None = None,
        visualizer: Visualizer | None = None,
    ):
        """Initialize the audio visualizer source.

        Args:
            context: SharedMediaContext to get audio samples from
            config: Source configuration
            visualizer: Visualizer to use (defaults to SimpleSpectrumVisualizer)
        """
        if config is None:
            config = AudioVisualizerSourceConfig()

        super().__init__(context, config)

        self._viz_config = config

        # Audio analyzer
        self._analyzer = AudioAnalyzer(
            sample_rate=context.audio_sample_rate,
            fft_size=config.fft_size,
            smoothing=config.smoothing,
            gain=config.gain,
        )

        # Beat detector
        self._beat_detector = BeatDetector(
            sensitivity=config.beat_sensitivity,
            decay=config.beat_decay,
        )

        # Visualizer (default if not provided)
        if visualizer is None:
            visualizer = SimpleSpectrumVisualizer(
                width=self._width,
                height=self._height,
                color_mode=config.color_mode,
            )
        self._visualizer = visualizer

    def _setup_controls(self) -> None:
        """Set up audio visualizer controls."""
        super()._setup_controls()

        # Gain control
        self._controls.register(
            NumberControl(
                id="gain",
                name="Gain",
                description="Audio input gain",
                value=self._viz_config.gain if hasattr(self, "_viz_config") else 1.0,
                min=0.0,
                max=5.0,
                step=0.1,
                group="audio",
            )
        )

        # Smoothing control
        self._controls.register(
            NumberControl(
                id="smoothing",
                name="Smoothing",
                description="Temporal smoothing",
                value=self._viz_config.smoothing if hasattr(self, "_viz_config") else 0.3,
                min=0.0,
                max=0.95,
                step=0.05,
                group="audio",
            )
        )

        # Beat sensitivity
        self._controls.register(
            NumberControl(
                id="beat_sensitivity",
                name="Beat Sensitivity",
                description="Beat detection sensitivity",
                value=self._viz_config.beat_sensitivity if hasattr(self, "_viz_config") else 1.5,
                min=1.0,
                max=3.0,
                step=0.1,
                group="audio",
            )
        )

    async def _handle_control_set(self, message):
        """Handle control set with audio-specific controls."""
        from libltp import control_set_response

        values = message.data.get("values", {})
        applied = {}
        errors = {}

        # Shared controls
        shared_controls = {"paused", "loop", "speed", "seek", "position", "play", "pause"}

        for control_id, value in values.items():
            try:
                if control_id in shared_controls:
                    # Forward to shared context
                    handled = await self._context.handle_control(control_id, value)
                    if handled:
                        applied[control_id] = value
                        if control_id in ("paused", "loop", "speed"):
                            self._controls.set_value(control_id, value)
                    else:
                        errors[control_id] = "Control not handled"
                elif control_id == "gain":
                    self._analyzer.gain = float(value)
                    self._controls.set_value(control_id, value)
                    applied[control_id] = value
                elif control_id == "smoothing":
                    self._analyzer.smoothing = float(value)
                    self._controls.set_value(control_id, value)
                    applied[control_id] = value
                elif control_id == "beat_sensitivity":
                    self._beat_detector.sensitivity = float(value)
                    self._controls.set_value(control_id, value)
                    applied[control_id] = value
                else:
                    # Local control
                    self._controls.set_value(control_id, value)
                    applied[control_id] = self._controls.get_value(control_id)
            except Exception as e:
                errors[control_id] = str(e)

        status = "ok" if not errors else "partial"
        return control_set_response(message.seq, status, applied, errors or None)

    async def render_frame(self) -> np.ndarray | None:
        """Render an audio visualization frame.

        Returns:
            Frame as RGB uint8 array (height, width, 3), or None.
        """
        # Get audio samples from shared context
        samples = await self._context.get_audio_samples(self._analyzer.fft_size)

        if samples is None or len(samples) == 0:
            # No audio - render black frame
            return np.zeros((self._height, self._width, 3), dtype=np.uint8)

        # Analyze audio
        self._analyzer.analyze(samples)

        # Update beat detector
        self._beat_detector.update_from_analyzer(self._analyzer)

        # Render visualization
        frame = self._visualizer.render(self._analyzer, self._beat_detector)

        return frame

    def set_visualizer(self, visualizer: Visualizer) -> None:
        """Change the active visualizer.

        Args:
            visualizer: New visualizer to use
        """
        self._visualizer = visualizer

    @property
    def analyzer(self) -> AudioAnalyzer:
        """The audio analyzer."""
        return self._analyzer

    @property
    def beat_detector(self) -> BeatDetector:
        """The beat detector."""
        return self._beat_detector

    @property
    def visualizer(self) -> Visualizer:
        """The current visualizer."""
        return self._visualizer

    def get_analysis_state(self) -> dict:
        """Get current audio analysis state.

        Returns:
            Dict with analysis values (for debugging/monitoring).
        """
        return {
            "rms": self._analyzer.rms,
            "peak": self._analyzer.peak,
            "bass": self._analyzer.get_bass(),
            "mids": self._analyzer.get_mids(),
            "highs": self._analyzer.get_highs(),
            "beat": self._beat_detector.beat,
            "beat_intensity": self._beat_detector.intensity,
            "beat_count": self._beat_detector.beat_count,
        }
