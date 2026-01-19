"""Audio visualization renderers.

This module provides various visualizers for rendering audio analysis
data to LED displays, both linear (1D) and matrix (2D).
"""

from ltp_media_source.visualizers.base import (
    Visualizer,
    LinearVisualizer,
    MatrixVisualizer,
)
from ltp_media_source.visualizers.colors import (
    ColorMode,
    PALETTE_RAINBOW,
    PALETTE_FIRE,
    PALETTE_ICE,
    PALETTE_OCEAN,
    hsv_to_rgb,
    rgb_to_hsv,
    interpolate_color,
    get_gradient_color,
    generate_rainbow_colors,
    generate_gradient_colors,
    generate_palette_colors,
    apply_intensity,
    blend_colors,
    get_color_for_mode,
)
from ltp_media_source.visualizers.linear import (
    SpectrumBarsLinear,
    VUMeterLinear,
    WaveformLinear,
    BeatPulseLinear,
    BassFillLinear,
    ChasingDotsLinear,
    LevelMeterLinear,
)
from ltp_media_source.visualizers.matrix import (
    SpectrumBarsMatrix,
    SpectrogramMatrix,
    BeatRipplesMatrix,
    FrequencyHeatmapMatrix,
    VUMeterBarMatrix,
    WaveformScopeMatrix,
    PlasmaMatrix,
)

__all__ = [
    # Base classes
    "Visualizer",
    "LinearVisualizer",
    "MatrixVisualizer",
    # Color utilities
    "ColorMode",
    "PALETTE_RAINBOW",
    "PALETTE_FIRE",
    "PALETTE_ICE",
    "PALETTE_OCEAN",
    "hsv_to_rgb",
    "rgb_to_hsv",
    "interpolate_color",
    "get_gradient_color",
    "generate_rainbow_colors",
    "generate_gradient_colors",
    "generate_palette_colors",
    "apply_intensity",
    "blend_colors",
    "get_color_for_mode",
    # Linear visualizers
    "SpectrumBarsLinear",
    "VUMeterLinear",
    "WaveformLinear",
    "BeatPulseLinear",
    "BassFillLinear",
    "ChasingDotsLinear",
    "LevelMeterLinear",
    # Matrix visualizers
    "SpectrumBarsMatrix",
    "SpectrogramMatrix",
    "BeatRipplesMatrix",
    "FrequencyHeatmapMatrix",
    "VUMeterBarMatrix",
    "WaveformScopeMatrix",
    "PlasmaMatrix",
]


# Visualizer registry for easy lookup by name
VISUALIZERS = {
    # Linear
    "spectrum": SpectrumBarsLinear,
    "spectrum_bars": SpectrumBarsLinear,
    "vu": VUMeterLinear,
    "vu_meter": VUMeterLinear,
    "waveform": WaveformLinear,
    "beat": BeatPulseLinear,
    "beat_pulse": BeatPulseLinear,
    "bass_fill": BassFillLinear,
    "chasing_dots": ChasingDotsLinear,
    "level_meter": LevelMeterLinear,
    # Matrix
    "spectrum_matrix": SpectrumBarsMatrix,
    "spectrogram": SpectrogramMatrix,
    "ripples": BeatRipplesMatrix,
    "beat_ripples": BeatRipplesMatrix,
    "heatmap": FrequencyHeatmapMatrix,
    "frequency_heatmap": FrequencyHeatmapMatrix,
    "vu_bar": VUMeterBarMatrix,
    "scope": WaveformScopeMatrix,
    "waveform_scope": WaveformScopeMatrix,
    "plasma": PlasmaMatrix,
}


def create_visualizer(
    visualizer_type: str,
    width: int,
    height: int = 1,
    **kwargs,
) -> Visualizer:
    """Create a visualizer by type name.

    Args:
        visualizer_type: Visualizer type name (see VISUALIZERS)
        width: Output width
        height: Output height (1 for linear)
        **kwargs: Additional visualizer parameters

    Returns:
        Visualizer instance

    Raises:
        ValueError: If visualizer_type is unknown
    """
    visualizer_type = visualizer_type.lower()

    if visualizer_type not in VISUALIZERS:
        raise ValueError(
            f"Unknown visualizer type: {visualizer_type}. "
            f"Available: {list(VISUALIZERS.keys())}"
        )

    cls = VISUALIZERS[visualizer_type]

    # Check if it's a linear or matrix visualizer
    if issubclass(cls, MatrixVisualizer):
        if height < 2:
            raise ValueError(f"Visualizer '{visualizer_type}' requires height >= 2")
        return cls(width=width, height=height, **kwargs)
    else:
        return cls(width=width, **kwargs)


def get_visualizer_names() -> list[str]:
    """Get list of available visualizer names."""
    return sorted(set(VISUALIZERS.keys()))
