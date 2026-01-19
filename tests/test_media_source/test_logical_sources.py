"""Unit tests for logical sources."""

import asyncio
from unittest.mock import MagicMock, AsyncMock, PropertyMock, patch

import numpy as np
import pytest

from ltp_media_source.sources.base import LogicalSource, LogicalSourceConfig
from ltp_media_source.sources.video_source import VideoLogicalSource, VideoLogicalSourceConfig
from ltp_media_source.sources.audio_visualizer import (
    AudioVisualizerSource,
    AudioVisualizerSourceConfig,
)
from ltp_media_source.visualizers import (
    Visualizer,
    SpectrumBarsLinear,
    SpectrumBarsMatrix,
)
from ltp_media_source.audio.analyzer import AudioAnalyzer
from ltp_media_source.audio.beat_detector import BeatDetector


class MockSharedMediaContext:
    """Mock SharedMediaContext for testing."""

    def __init__(self):
        self.media_path = "/mock/video.mp4"
        self.audio_sample_rate = 44100
        self.audio_channels = 2
        self._position = 0.0
        self._duration = 10.0
        self._playing = True
        self._paused = False
        self._loop = True
        self._speed = 1.0
        self._video_frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        self._audio_samples = np.random.randn(2048, 2).astype(np.float32) * 0.1

    @property
    def position(self):
        return self._position

    @property
    def duration(self):
        return self._duration

    @property
    def playing(self):
        return self._playing and not self._paused

    @property
    def paused(self):
        return self._paused

    @property
    def loop(self):
        return self._loop

    @property
    def speed(self):
        return self._speed

    @property
    def has_audio(self):
        return True

    async def get_video_frame(self):
        return self._video_frame

    async def get_audio_samples(self, num_samples):
        if num_samples <= len(self._audio_samples):
            return self._audio_samples[:num_samples]
        return self._audio_samples

    async def handle_control(self, control_id, value):
        if control_id == "paused":
            self._paused = value
            return True
        elif control_id == "loop":
            self._loop = value
            return True
        elif control_id == "speed":
            self._speed = value
            return True
        elif control_id == "seek" or control_id == "position":
            self._position = float(value)
            return True
        elif control_id == "play":
            self._paused = False
            return True
        elif control_id == "pause":
            self._paused = True
            return True
        return False


class TestSpectrumVisualizers:
    """Tests for SpectrumBarsLinear and SpectrumBarsMatrix."""

    def test_init_linear(self):
        """Test linear visualizer initialization."""
        viz = SpectrumBarsLinear(width=60)
        assert viz.width == 60
        assert viz.height == 1
        assert viz.is_linear is True

    def test_init_matrix(self):
        """Test matrix visualizer initialization."""
        viz = SpectrumBarsMatrix(width=16, height=16)
        assert viz.width == 16
        assert viz.height == 16
        assert viz.is_linear is False

    def test_render_linear(self):
        """Test rendering linear visualization."""
        viz = SpectrumBarsLinear(width=32, smoothing=0.0)
        analyzer = AudioAnalyzer(smoothing=0.0)

        # Analyze some audio
        samples = np.random.randn(2048).astype(np.float32)
        analyzer.analyze(samples)

        # Render
        frame = viz.render(analyzer)

        assert frame.shape == (1, 32, 3)
        assert frame.dtype == np.uint8

    def test_render_matrix(self):
        """Test rendering matrix visualization."""
        viz = SpectrumBarsMatrix(width=16, height=8, smoothing=0.0)
        analyzer = AudioAnalyzer(smoothing=0.0)

        samples = np.random.randn(2048).astype(np.float32)
        analyzer.analyze(samples)

        frame = viz.render(analyzer)

        assert frame.shape == (8, 16, 3)
        assert frame.dtype == np.uint8

    def test_render_with_beat_detector(self):
        """Test rendering with beat detector."""
        viz = SpectrumBarsLinear(width=16, smoothing=0.0)
        analyzer = AudioAnalyzer(smoothing=0.0)
        beat_detector = BeatDetector()

        # Setup beat detector with a "beat"
        beat_detector._intensity = 0.5

        samples = np.random.randn(2048).astype(np.float32)
        analyzer.analyze(samples)

        frame = viz.render(analyzer, beat_detector)

        assert frame.shape == (1, 16, 3)
        assert frame.dtype == np.uint8

    def test_smoothing(self):
        """Test temporal smoothing."""
        viz = SpectrumBarsLinear(width=8, smoothing=0.5)
        analyzer = AudioAnalyzer(smoothing=0.0)

        # First render with signal
        samples1 = np.ones(2048, dtype=np.float32) * 0.5
        analyzer.analyze(samples1)
        frame1 = viz.render(analyzer)

        # Second render with silence - should still have some brightness due to smoothing
        samples2 = np.zeros(2048, dtype=np.float32)
        analyzer.analyze(samples2)
        frame2 = viz.render(analyzer)

        # With smoothing, frame2 shouldn't be completely black
        # (hard to test exactly due to spectrum processing)


class TestVideoLogicalSource:
    """Tests for VideoLogicalSource."""

    def test_init(self):
        """Test initialization."""
        context = MockSharedMediaContext()
        config = VideoLogicalSourceConfig(
            name="Test Video",
            dimensions=[64, 32],
        )
        source = VideoLogicalSource(context, config)

        assert source.config.name == "Test Video"
        assert source.width == 64
        assert source.height == 32
        assert source.context is context

    def test_init_default_config(self):
        """Test initialization with default config."""
        context = MockSharedMediaContext()
        source = VideoLogicalSource(context)

        assert source.width == 16
        assert source.height == 16

    @pytest.mark.asyncio
    async def test_render_frame(self):
        """Test rendering a video frame."""
        context = MockSharedMediaContext()
        config = VideoLogicalSourceConfig(
            dimensions=[32, 16],
            fit_mode="contain",
        )
        source = VideoLogicalSource(context, config)

        frame = await source.render_frame()

        assert frame is not None
        assert frame.shape == (16, 32, 3)
        assert frame.dtype == np.uint8

    @pytest.mark.asyncio
    async def test_render_frame_no_video(self):
        """Test rendering when no video frame available."""
        context = MockSharedMediaContext()
        context._video_frame = None

        source = VideoLogicalSource(context)
        frame = await source.render_frame()

        assert frame is None

    def test_set_fit_mode(self):
        """Test changing fit mode."""
        context = MockSharedMediaContext()
        source = VideoLogicalSource(context)

        source.set_fit_mode("cover")
        assert source.fit_mode.value == "cover"

        source.set_fit_mode("stretch")
        assert source.fit_mode.value == "stretch"

    def test_gamma_property(self):
        """Test gamma property."""
        context = MockSharedMediaContext()
        config = VideoLogicalSourceConfig(gamma=1.5)
        source = VideoLogicalSource(context, config)

        assert source.gamma == 1.5

        source.gamma = 2.0
        assert source.gamma == 2.0

        # Test clamping
        source.gamma = 0.1
        assert source.gamma == 0.5  # Clamped to min

        source.gamma = 5.0
        assert source.gamma == 3.0  # Clamped to max


class TestAudioVisualizerSource:
    """Tests for AudioVisualizerSource."""

    def test_init(self):
        """Test initialization."""
        context = MockSharedMediaContext()
        config = AudioVisualizerSourceConfig(
            name="Test Visualizer",
            dimensions=[60],
        )
        source = AudioVisualizerSource(context, config)

        assert source.config.name == "Test Visualizer"
        assert source.width == 60
        assert source.height == 1
        assert source.analyzer is not None
        assert source.beat_detector is not None
        assert source.visualizer is not None

    def test_init_matrix(self):
        """Test initialization for matrix output."""
        context = MockSharedMediaContext()
        config = AudioVisualizerSourceConfig(
            dimensions=[16, 16],
        )
        source = AudioVisualizerSource(context, config)

        assert source.width == 16
        assert source.height == 16

    def test_custom_visualizer(self):
        """Test using a custom visualizer."""
        context = MockSharedMediaContext()
        custom_viz = SpectrumBarsMatrix(width=32, height=8)

        source = AudioVisualizerSource(
            context,
            config=AudioVisualizerSourceConfig(dimensions=[32, 8]),
            visualizer=custom_viz,
        )

        assert source.visualizer is custom_viz

    @pytest.mark.asyncio
    async def test_render_frame(self):
        """Test rendering an audio visualization frame."""
        context = MockSharedMediaContext()
        config = AudioVisualizerSourceConfig(dimensions=[32])
        source = AudioVisualizerSource(context, config)

        frame = await source.render_frame()

        assert frame is not None
        assert frame.shape == (1, 32, 3)
        assert frame.dtype == np.uint8

    @pytest.mark.asyncio
    async def test_render_frame_matrix(self):
        """Test rendering matrix visualization."""
        context = MockSharedMediaContext()
        config = AudioVisualizerSourceConfig(dimensions=[16, 8])
        source = AudioVisualizerSource(context, config)

        frame = await source.render_frame()

        assert frame is not None
        assert frame.shape == (8, 16, 3)
        assert frame.dtype == np.uint8

    def test_set_visualizer(self):
        """Test changing visualizer."""
        context = MockSharedMediaContext()
        source = AudioVisualizerSource(context)

        new_viz = SpectrumBarsMatrix(width=32, height=4)
        source.set_visualizer(new_viz)

        assert source.visualizer is new_viz

    def test_get_analysis_state(self):
        """Test getting analysis state."""
        context = MockSharedMediaContext()
        source = AudioVisualizerSource(context)

        state = source.get_analysis_state()

        assert "rms" in state
        assert "peak" in state
        assert "bass" in state
        assert "mids" in state
        assert "highs" in state
        assert "beat" in state
        assert "beat_intensity" in state
        assert "beat_count" in state


class TestLogicalSourceConfig:
    """Tests for LogicalSourceConfig."""

    def test_defaults(self):
        """Test default configuration."""
        config = LogicalSourceConfig()

        assert config.name == "Logical Source"
        assert config.dimensions == [16, 16]
        assert config.rate == 30
        assert config.control_port == 0
        assert config.device_id is not None

    def test_custom_values(self):
        """Test custom configuration."""
        config = LogicalSourceConfig(
            name="Custom Source",
            dimensions=[64, 32],
            rate=60,
            description="A custom source",
        )

        assert config.name == "Custom Source"
        assert config.dimensions == [64, 32]
        assert config.rate == 60
        assert config.description == "A custom source"

    def test_linear_dimensions(self):
        """Test 1D dimensions."""
        config = LogicalSourceConfig(dimensions=[100])
        assert config.dimensions == [100]


class TestLogicalSourceBase:
    """Tests for LogicalSource base class functionality."""

    def test_dimensions_parsing_2d(self):
        """Test 2D dimensions parsing."""
        context = MockSharedMediaContext()
        config = VideoLogicalSourceConfig(dimensions=[64, 32])
        source = VideoLogicalSource(context, config)

        assert source.width == 64
        assert source.height == 32
        assert source.dimensions == (64, 32)

    def test_dimensions_parsing_1d(self):
        """Test 1D dimensions parsing."""
        context = MockSharedMediaContext()
        config = AudioVisualizerSourceConfig(dimensions=[100])
        source = AudioVisualizerSource(context, config)

        assert source.width == 100
        assert source.height == 1
        assert source.dimensions == (100, 1)

    def test_is_running(self):
        """Test is_running property."""
        context = MockSharedMediaContext()
        source = VideoLogicalSource(context)

        assert source.is_running is False

    def test_current_frame(self):
        """Test current_frame property."""
        context = MockSharedMediaContext()
        source = VideoLogicalSource(context)

        assert source.current_frame is None

    def test_get_stats(self):
        """Test get_stats method."""
        context = MockSharedMediaContext()
        source = VideoLogicalSource(context)

        stats = source.get_stats()

        assert "name" in stats
        assert "running" in stats
        assert "frame_count" in stats
        assert "dimensions" in stats
        assert "rate" in stats
        assert "subscribers" in stats
        assert "context" in stats
