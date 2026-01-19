"""Audio visualizer logical source - outputs audio visualizations from shared context."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from libltp import EnumControl, EnumOption, NumberControl

from ltp_media_source.audio.analyzer import AudioAnalyzer
from ltp_media_source.audio.beat_detector import BeatDetector
from ltp_media_source.sources.base import LogicalSource, LogicalSourceConfig
from ltp_media_source.visualizers import (
    Visualizer,
    create_visualizer,
    get_visualizer_names,
    VISUALIZERS,
    ColorMode,
)

if TYPE_CHECKING:
    from ltp_media_source.shared_context import SharedMediaContext

logger = logging.getLogger(__name__)


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
    visualizer_type: str = "spectrum"
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
            visualizer: Visualizer to use (created from config if not provided)
        """
        if config is None:
            config = AudioVisualizerSourceConfig()

        super().__init__(context, config)

        self._viz_config = config
        self._visualizer_type = config.visualizer_type

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

        # Visualizer (create from config if not provided)
        if visualizer is None:
            visualizer = self._create_visualizer(config.visualizer_type)
        self._visualizer = visualizer

    def _create_visualizer(self, visualizer_type: str) -> Visualizer:
        """Create a visualizer by type name.

        Args:
            visualizer_type: Visualizer type name

        Returns:
            Visualizer instance configured for this source's dimensions
        """
        # Parse color mode from config
        try:
            color_mode = ColorMode(self._viz_config.color_mode)
        except ValueError:
            color_mode = ColorMode.RAINBOW

        # Check if requested visualizer is compatible with dimensions
        # If not, substitute an appropriate one
        viz_class = VISUALIZERS.get(visualizer_type.lower())
        if viz_class is not None:
            from ltp_media_source.visualizers.base import MatrixVisualizer as MatViz, LinearVisualizer as LinViz
            is_matrix_viz = issubclass(viz_class, MatViz)
            needs_matrix = self._height >= 2

            if needs_matrix and not is_matrix_viz:
                # Need matrix but got linear - substitute matrix equivalent
                matrix_equivalents = {
                    "spectrum": "spectrum_matrix",
                    "spectrum_bars": "spectrum_matrix",
                    "vu": "vu_bar",
                    "vu_meter": "vu_bar",
                    "waveform": "waveform_scope",
                    "beat": "ripples",
                    "beat_pulse": "ripples",
                }
                visualizer_type = matrix_equivalents.get(visualizer_type.lower(), "spectrum_matrix")
            elif not needs_matrix and is_matrix_viz:
                # Need linear but got matrix - substitute linear equivalent
                linear_equivalents = {
                    "spectrum_matrix": "spectrum",
                    "spectrogram": "spectrum",
                    "ripples": "beat",
                    "beat_ripples": "beat",
                    "heatmap": "spectrum",
                    "frequency_heatmap": "spectrum",
                    "vu_bar": "vu",
                    "scope": "waveform",
                    "waveform_scope": "waveform",
                    "plasma": "beat",
                }
                visualizer_type = linear_equivalents.get(visualizer_type.lower(), "spectrum")

        return create_visualizer(
            visualizer_type=visualizer_type,
            width=self._width,
            height=self._height,
            color_mode=color_mode,
            smoothing=self._viz_config.smoothing,
            gain=self._viz_config.gain,
        )

    def _setup_controls(self) -> None:
        """Set up audio visualizer controls."""
        super()._setup_controls()

        # Visualizer type control
        visualizer_options = [
            EnumOption(value=name, label=name.replace("_", " ").title())
            for name in get_visualizer_names()
        ]
        self._controls.register(
            EnumControl(
                id="visualizer_type",
                name="Visualizer",
                description="Visualization type",
                options=visualizer_options,
                value=self._visualizer_type if hasattr(self, "_visualizer_type") else "spectrum",
                group="visualizer",
            )
        )

        # Color mode control
        color_options = [
            EnumOption(value=mode.value, label=mode.value.title())
            for mode in ColorMode
        ]
        self._controls.register(
            EnumControl(
                id="color_mode",
                name="Color Mode",
                description="Color scheme",
                options=color_options,
                value=self._viz_config.color_mode if hasattr(self, "_viz_config") else "rainbow",
                group="visualizer",
            )
        )

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
                elif control_id == "visualizer_type":
                    # Change visualizer type
                    try:
                        new_visualizer = self._create_visualizer(str(value))
                        self._visualizer = new_visualizer
                        self._visualizer_type = str(value)
                        self._controls.set_value(control_id, value)
                        applied[control_id] = value
                    except ValueError as e:
                        errors[control_id] = str(e)
                elif control_id == "color_mode":
                    # Change color mode
                    try:
                        color_mode = ColorMode(str(value))
                        self._visualizer.set_color_mode(color_mode)
                        self._viz_config.color_mode = str(value)
                        self._controls.set_value(control_id, value)
                        applied[control_id] = value
                    except ValueError as e:
                        errors[control_id] = str(e)
                elif control_id == "gain":
                    self._analyzer.gain = float(value)
                    self._visualizer.gain = float(value)
                    self._controls.set_value(control_id, value)
                    applied[control_id] = value
                elif control_id == "smoothing":
                    self._analyzer.smoothing = float(value)
                    self._visualizer.smoothing = float(value)
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

    def set_visualizer_type(self, visualizer_type: str) -> None:
        """Change the visualizer by type name.

        Args:
            visualizer_type: Visualizer type name (see get_visualizer_names())

        Raises:
            ValueError: If visualizer_type is unknown
        """
        self._visualizer = self._create_visualizer(visualizer_type)
        self._visualizer_type = visualizer_type

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

    @property
    def visualizer_type(self) -> str:
        """The current visualizer type name."""
        return self._visualizer_type

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
            "visualizer_type": self._visualizer_type,
        }
