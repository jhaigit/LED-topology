"""Tests for visualizers module (Phase 4)."""

import pytest
import numpy as np

from ltp_media_source.visualizers import (
    # Base classes
    Visualizer,
    LinearVisualizer,
    MatrixVisualizer,
    # Color utilities
    ColorMode,
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
    PALETTE_FIRE,
    PALETTE_ICE,
    PALETTE_OCEAN,
    PALETTE_RAINBOW,
    # Linear visualizers
    SpectrumBarsLinear,
    VUMeterLinear,
    WaveformLinear,
    BeatPulseLinear,
    BassFillLinear,
    ChasingDotsLinear,
    LevelMeterLinear,
    # Matrix visualizers
    SpectrumBarsMatrix,
    SpectrogramMatrix,
    BeatRipplesMatrix,
    FrequencyHeatmapMatrix,
    VUMeterBarMatrix,
    WaveformScopeMatrix,
    PlasmaMatrix,
    # Factory
    create_visualizer,
    get_visualizer_names,
    VISUALIZERS,
)
from ltp_media_source.audio.analyzer import AudioAnalyzer
from ltp_media_source.audio.beat_detector import BeatDetector


# =============================================================================
# Color Utilities Tests
# =============================================================================


class TestColorConversions:
    """Test HSV <-> RGB conversions."""

    def test_hsv_to_rgb_red(self):
        """Red at hue 0."""
        r, g, b = hsv_to_rgb(0, 1.0, 1.0)
        assert r == 255
        assert g == 0
        assert b == 0

    def test_hsv_to_rgb_green(self):
        """Green at hue 120."""
        r, g, b = hsv_to_rgb(120, 1.0, 1.0)
        assert r == 0
        assert g == 255
        assert b == 0

    def test_hsv_to_rgb_blue(self):
        """Blue at hue 240."""
        r, g, b = hsv_to_rgb(240, 1.0, 1.0)
        assert r == 0
        assert g == 0
        assert b == 255

    def test_hsv_to_rgb_white(self):
        """White at full value, no saturation."""
        r, g, b = hsv_to_rgb(0, 0, 1.0)
        assert r == 255
        assert g == 255
        assert b == 255

    def test_hsv_to_rgb_black(self):
        """Black at zero value."""
        r, g, b = hsv_to_rgb(0, 1.0, 0)
        assert r == 0
        assert g == 0
        assert b == 0

    def test_rgb_to_hsv_red(self):
        """RGB red to HSV."""
        h, s, v = rgb_to_hsv(255, 0, 0)
        assert h == 0
        assert s == 1.0
        assert v == 1.0

    def test_rgb_to_hsv_green(self):
        """RGB green to HSV."""
        h, s, v = rgb_to_hsv(0, 255, 0)
        assert h == 120
        assert s == 1.0
        assert v == 1.0

    def test_rgb_to_hsv_blue(self):
        """RGB blue to HSV."""
        h, s, v = rgb_to_hsv(0, 0, 255)
        assert h == 240
        assert s == 1.0
        assert v == 1.0

    def test_roundtrip_hsv_rgb(self):
        """HSV -> RGB -> HSV should be lossless for pure colors."""
        for hue in [0, 60, 120, 180, 240, 300]:
            r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
            h, s, v = rgb_to_hsv(r, g, b)
            assert abs(h - hue) < 1  # Allow small rounding error
            assert abs(s - 1.0) < 0.01
            assert abs(v - 1.0) < 0.01


class TestColorInterpolation:
    """Test color interpolation functions."""

    def test_interpolate_at_start(self):
        """Interpolation at t=0 returns first color."""
        color = interpolate_color((255, 0, 0), (0, 0, 255), 0.0)
        assert color == (255, 0, 0)

    def test_interpolate_at_end(self):
        """Interpolation at t=1 returns second color."""
        color = interpolate_color((255, 0, 0), (0, 0, 255), 1.0)
        assert color == (0, 0, 255)

    def test_interpolate_at_midpoint(self):
        """Interpolation at t=0.5 returns midpoint."""
        color = interpolate_color((0, 0, 0), (100, 200, 100), 0.5)
        assert color == (50, 100, 50)

    def test_interpolate_clamps_t(self):
        """Interpolation clamps t to [0, 1]."""
        color1 = interpolate_color((0, 0, 0), (100, 100, 100), -0.5)
        assert color1 == (0, 0, 0)

        color2 = interpolate_color((0, 0, 0), (100, 100, 100), 1.5)
        assert color2 == (100, 100, 100)

    def test_get_gradient_color_from_palette(self):
        """Get gradient color from palette."""
        palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]

        # Start
        assert get_gradient_color(palette, 0.0) == (255, 0, 0)
        # End
        assert get_gradient_color(palette, 1.0) == (0, 0, 255)
        # Midpoint of first segment
        color_mid = get_gradient_color(palette, 0.25)
        assert color_mid[1] > 0  # Some green mixed in

    def test_gradient_empty_palette(self):
        """Empty palette returns black."""
        assert get_gradient_color([], 0.5) == (0, 0, 0)

    def test_gradient_single_color(self):
        """Single color palette returns that color."""
        assert get_gradient_color([(128, 64, 32)], 0.5) == (128, 64, 32)


class TestColorGeneration:
    """Test color array generation functions."""

    def test_generate_rainbow_colors(self):
        """Generate rainbow color array."""
        colors = generate_rainbow_colors(10)
        assert colors.shape == (10, 3)
        assert colors.dtype == np.uint8
        # First color should be reddish
        assert colors[0, 0] > 200  # High red

    def test_generate_gradient_colors(self):
        """Generate gradient between two colors."""
        colors = generate_gradient_colors(5, (255, 0, 0), (0, 0, 255))
        assert colors.shape == (5, 3)
        # First is red
        assert colors[0, 0] == 255
        assert colors[0, 2] == 0
        # Last is blue
        assert colors[-1, 0] == 0
        assert colors[-1, 2] == 255

    def test_generate_palette_colors(self):
        """Generate colors from palette."""
        colors = generate_palette_colors(6, PALETTE_FIRE)
        assert colors.shape == (6, 3)
        assert colors.dtype == np.uint8


class TestColorUtilities:
    """Test color utility functions."""

    def test_apply_intensity(self):
        """Apply intensity to colors."""
        colors = np.array([[100, 200, 50]], dtype=np.uint8)
        result = apply_intensity(colors, 0.5)
        assert result[0, 0] == 50
        assert result[0, 1] == 100
        assert result[0, 2] == 25

    def test_apply_intensity_clamps(self):
        """Intensity is clamped to [0, 1]."""
        colors = np.array([[100, 100, 100]], dtype=np.uint8)
        result1 = apply_intensity(colors, -0.5)
        result2 = apply_intensity(colors, 1.5)
        assert np.all(result1 == 0)
        assert np.all(result2 == colors)

    def test_blend_colors(self):
        """Blend two color arrays."""
        c1 = np.array([[100, 100, 100]], dtype=np.uint8)
        c2 = np.array([[200, 200, 200]], dtype=np.uint8)

        # 50% blend
        result = blend_colors(c1, c2, 0.5)
        assert np.all(result == 150)

    def test_get_color_for_mode_solid(self):
        """Get color for solid mode."""
        color = get_color_for_mode(ColorMode.SOLID, 0.5, 1.0, (128, 64, 32))
        assert color == (128, 64, 32)

    def test_get_color_for_mode_rainbow(self):
        """Get color for rainbow mode."""
        color = get_color_for_mode(ColorMode.RAINBOW, 0.0, 1.0)
        # At position 0, should be red
        assert color[0] > 200

    def test_get_color_for_mode_with_intensity(self):
        """Intensity affects color brightness."""
        full = get_color_for_mode(ColorMode.SOLID, 0.5, 1.0, (200, 200, 200))
        half = get_color_for_mode(ColorMode.SOLID, 0.5, 0.5, (200, 200, 200))
        assert full == (200, 200, 200)
        assert half == (100, 100, 100)


# =============================================================================
# Base Visualizer Tests
# =============================================================================


class TestVisualizerBase:
    """Test base visualizer classes."""

    @pytest.fixture
    def analyzer(self):
        """Create an audio analyzer with test data."""
        analyzer = AudioAnalyzer(sample_rate=44100, fft_size=1024)
        # Feed some test audio
        samples = np.sin(np.linspace(0, 10 * np.pi, 1024)).astype(np.float32)
        analyzer.analyze(samples)
        return analyzer

    @pytest.fixture
    def beat_detector(self, analyzer):
        """Create a beat detector."""
        detector = BeatDetector()
        detector.update_from_analyzer(analyzer)
        return detector


class TestLinearVisualizers(TestVisualizerBase):
    """Test linear (1D) visualizers."""

    def test_spectrum_bars_linear_dimensions(self, analyzer):
        """SpectrumBarsLinear outputs correct dimensions."""
        viz = SpectrumBarsLinear(width=30)
        frame = viz.render(analyzer)
        assert frame.shape == (1, 30, 3)
        assert frame.dtype == np.uint8

    def test_vu_meter_linear(self, analyzer, beat_detector):
        """VUMeterLinear renders correctly."""
        viz = VUMeterLinear(width=20)
        frame = viz.render(analyzer, beat_detector)
        assert frame.shape == (1, 20, 3)

    def test_waveform_linear(self, analyzer):
        """WaveformLinear renders waveform."""
        viz = WaveformLinear(width=50, color=(0, 255, 0))
        frame = viz.render(analyzer)
        assert frame.shape == (1, 50, 3)

    def test_beat_pulse_linear(self, analyzer, beat_detector):
        """BeatPulseLinear flashes on beat."""
        viz = BeatPulseLinear(width=20, color=(255, 255, 255))
        frame = viz.render(analyzer, beat_detector)
        assert frame.shape == (1, 20, 3)

    def test_bass_fill_linear(self, analyzer):
        """BassFillLinear fills from center."""
        viz = BassFillLinear(width=30, color=(255, 0, 0))
        frame = viz.render(analyzer)
        assert frame.shape == (1, 30, 3)

    def test_chasing_dots_linear(self, analyzer, beat_detector):
        """ChasingDotsLinear moves dots."""
        viz = ChasingDotsLinear(width=30, num_dots=3)
        frame1 = viz.render(analyzer, beat_detector)
        frame2 = viz.render(analyzer, beat_detector)
        assert frame1.shape == (1, 30, 3)
        # Dots should have moved
        assert not np.array_equal(frame1, frame2)

    def test_level_meter_linear(self, analyzer):
        """LevelMeterLinear shows bass/mids/highs."""
        viz = LevelMeterLinear(width=30)
        frame = viz.render(analyzer)
        assert frame.shape == (1, 30, 3)

    def test_linear_is_linear_property(self):
        """LinearVisualizer.is_linear returns True."""
        viz = SpectrumBarsLinear(width=20)
        assert viz.is_linear is True
        assert viz.height == 1


class TestMatrixVisualizers(TestVisualizerBase):
    """Test matrix (2D) visualizers."""

    def test_spectrum_bars_matrix(self, analyzer, beat_detector):
        """SpectrumBarsMatrix outputs correct dimensions."""
        viz = SpectrumBarsMatrix(width=16, height=8)
        frame = viz.render(analyzer, beat_detector)
        assert frame.shape == (8, 16, 3)
        assert frame.dtype == np.uint8

    def test_spectrogram_matrix(self, analyzer):
        """SpectrogramMatrix builds history."""
        viz = SpectrogramMatrix(width=16, height=8)
        for _ in range(5):
            frame = viz.render(analyzer)
        assert frame.shape == (8, 16, 3)

    def test_beat_ripples_matrix(self, analyzer, beat_detector):
        """BeatRipplesMatrix creates ripples."""
        viz = BeatRipplesMatrix(width=16, height=16)
        frame = viz.render(analyzer, beat_detector)
        assert frame.shape == (16, 16, 3)

    def test_frequency_heatmap_matrix(self, analyzer):
        """FrequencyHeatmapMatrix renders heatmap."""
        viz = FrequencyHeatmapMatrix(width=8, height=8)
        frame = viz.render(analyzer)
        assert frame.shape == (8, 8, 3)

    def test_vu_meter_bar_matrix_horizontal(self, analyzer):
        """VUMeterBarMatrix horizontal mode."""
        viz = VUMeterBarMatrix(width=16, height=8, orientation="horizontal")
        frame = viz.render(analyzer)
        assert frame.shape == (8, 16, 3)

    def test_vu_meter_bar_matrix_vertical(self, analyzer):
        """VUMeterBarMatrix vertical mode."""
        viz = VUMeterBarMatrix(width=8, height=16, orientation="vertical")
        frame = viz.render(analyzer)
        assert frame.shape == (16, 8, 3)

    def test_waveform_scope_matrix(self, analyzer):
        """WaveformScopeMatrix renders scope."""
        viz = WaveformScopeMatrix(width=32, height=16)
        frame = viz.render(analyzer)
        assert frame.shape == (16, 32, 3)

    def test_plasma_matrix(self, analyzer, beat_detector):
        """PlasmaMatrix renders audio-reactive plasma."""
        viz = PlasmaMatrix(width=16, height=16)
        frame = viz.render(analyzer, beat_detector)
        assert frame.shape == (16, 16, 3)

    def test_matrix_is_linear_property(self):
        """MatrixVisualizer.is_linear returns False."""
        viz = SpectrumBarsMatrix(width=16, height=8)
        assert viz.is_linear is False
        assert viz.height == 8

    def test_matrix_requires_height_gte_2(self):
        """MatrixVisualizer requires height >= 2."""
        with pytest.raises(ValueError, match="height >= 2"):
            SpectrumBarsMatrix(width=16, height=1)


# =============================================================================
# Visualizer Factory Tests
# =============================================================================


class TestVisualizerFactory:
    """Test visualizer factory functions."""

    def test_get_visualizer_names(self):
        """get_visualizer_names returns list of names."""
        names = get_visualizer_names()
        assert isinstance(names, list)
        assert len(names) > 0
        assert "spectrum" in names
        assert "vu" in names
        assert "spectrogram" in names

    def test_visualizers_registry(self):
        """VISUALIZERS registry contains expected entries."""
        assert "spectrum" in VISUALIZERS
        assert "vu_meter" in VISUALIZERS
        assert "beat_pulse" in VISUALIZERS
        assert "spectrogram" in VISUALIZERS
        assert "ripples" in VISUALIZERS
        assert "plasma" in VISUALIZERS

    def test_create_linear_visualizer(self):
        """create_visualizer creates linear visualizer."""
        viz = create_visualizer("spectrum", width=30)
        assert isinstance(viz, LinearVisualizer)
        assert viz.width == 30
        assert viz.height == 1

    def test_create_matrix_visualizer(self):
        """create_visualizer creates matrix visualizer."""
        viz = create_visualizer("spectrogram", width=16, height=8)
        assert isinstance(viz, MatrixVisualizer)
        assert viz.width == 16
        assert viz.height == 8

    def test_create_visualizer_with_options(self):
        """create_visualizer passes options to visualizer."""
        viz = create_visualizer(
            "spectrum",
            width=30,
            color_mode=ColorMode.FIRE,
            gain=2.0,
        )
        assert viz.color_mode == ColorMode.FIRE
        assert viz.gain == 2.0

    def test_create_visualizer_unknown_type(self):
        """create_visualizer raises on unknown type."""
        with pytest.raises(ValueError, match="Unknown visualizer type"):
            create_visualizer("nonexistent", width=30)

    def test_create_matrix_requires_height(self):
        """Matrix visualizer requires height >= 2."""
        with pytest.raises(ValueError, match="requires height >= 2"):
            create_visualizer("spectrogram", width=16, height=1)

    def test_create_visualizer_case_insensitive(self):
        """Visualizer type is case-insensitive."""
        viz1 = create_visualizer("SPECTRUM", width=30)
        viz2 = create_visualizer("Spectrum", width=30)
        assert type(viz1) == type(viz2)


# =============================================================================
# Visualizer State Tests
# =============================================================================


class TestVisualizerState:
    """Test visualizer state management."""

    @pytest.fixture
    def analyzer(self):
        """Create an audio analyzer with test data."""
        analyzer = AudioAnalyzer(sample_rate=44100, fft_size=1024)
        samples = np.sin(np.linspace(0, 10 * np.pi, 1024)).astype(np.float32)
        analyzer.analyze(samples)
        return analyzer

    def test_set_color_mode(self, analyzer):
        """set_color_mode changes visualization colors."""
        viz = SpectrumBarsLinear(width=10, color_mode=ColorMode.RAINBOW)
        frame1 = viz.render(analyzer)

        viz.set_color_mode(ColorMode.FIRE)
        frame2 = viz.render(analyzer)

        # Frames should differ (different color mode)
        assert not np.array_equal(frame1, frame2)

    def test_set_base_color(self, analyzer):
        """set_base_color changes the base color."""
        viz = SpectrumBarsLinear(width=10, color_mode=ColorMode.SOLID)
        viz.set_base_color((255, 0, 0))
        assert viz.base_color == (255, 0, 0)

    def test_reset_clears_state(self, analyzer):
        """reset() clears internal state."""
        viz = SpectrumBarsLinear(width=10)
        viz.render(analyzer)
        viz.render(analyzer)

        viz.reset()
        assert viz._last_frame is None

    def test_smoothing_affects_output(self):
        """Smoothing parameter affects frame output."""
        viz_fast = SpectrumBarsLinear(width=10, smoothing=0.0)
        viz_slow = SpectrumBarsLinear(width=10, smoothing=0.9)

        analyzer = AudioAnalyzer(sample_rate=44100, fft_size=1024)

        # Render with varying input to show smoothing effect
        differences = []
        for i in range(10):
            # Vary frequency to get different spectrum
            freq = 100 + i * 50
            samples = np.sin(np.linspace(0, freq * np.pi, 1024)).astype(np.float32)
            analyzer.analyze(samples)
            fast_frame = viz_fast.render(analyzer)
            slow_frame = viz_slow.render(analyzer)
            differences.append(not np.array_equal(fast_frame, slow_frame))

        # At least some frames should differ due to smoothing
        assert any(differences), "Smoothing should cause some frames to differ"

    def test_gain_affects_brightness(self, analyzer):
        """Gain parameter affects output brightness."""
        viz_low = SpectrumBarsLinear(width=10, gain=0.5)
        viz_high = SpectrumBarsLinear(width=10, gain=2.0)

        frame_low = viz_low.render(analyzer)
        frame_high = viz_high.render(analyzer)

        # Higher gain should produce brighter output
        assert np.mean(frame_high) >= np.mean(frame_low)
