"""Tests for MediaSourceGroup (Phase 5)."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import numpy as np
import pytest
import yaml

from ltp_media_source.source_group import (
    MediaSourceGroup,
    SourceGroupConfig,
    SourceDefinition,
)
from ltp_media_source.sources.video_source import VideoLogicalSource
from ltp_media_source.sources.audio_visualizer import AudioVisualizerSource


class TestSourceDefinition:
    """Tests for SourceDefinition configuration."""

    def test_video_definition(self):
        """Test video source definition."""
        defn = SourceDefinition(
            name="Test Video",
            type="video",
            dimensions=[64, 32],
            fit_mode="cover",
        )
        assert defn.name == "Test Video"
        assert defn.type == "video"
        assert defn.dimensions == [64, 32]
        assert defn.fit_mode == "cover"

    def test_audio_visualizer_definition(self):
        """Test audio visualizer source definition."""
        defn = SourceDefinition(
            name="Spectrum",
            type="audio_visualizer",
            dimensions=[60],
            visualizer_type="spectrum",
            color_mode="fire",
        )
        assert defn.name == "Spectrum"
        assert defn.type == "audio_visualizer"
        assert defn.dimensions == [60]
        assert defn.visualizer_type == "spectrum"
        assert defn.color_mode == "fire"

    def test_default_values(self):
        """Test default values for source definition."""
        defn = SourceDefinition(name="Test", type="video")
        assert defn.dimensions == [16, 16]
        assert defn.rate == 30
        assert defn.fit_mode == "contain"
        assert defn.gamma == 1.0
        assert defn.visualizer_type == "spectrum"
        assert defn.color_mode == "rainbow"


class TestSourceGroupConfig:
    """Tests for SourceGroupConfig."""

    def test_basic_config(self):
        """Test basic configuration."""
        config = SourceGroupConfig(
            media_path="/path/to/video.mp4",
            sources=[
                SourceDefinition(name="Video", type="video", dimensions=[64, 32]),
            ]
        )
        assert config.media_path == "/path/to/video.mp4"
        assert len(config.sources) == 1
        assert config.sources[0].name == "Video"

    def test_default_values(self):
        """Test default configuration values."""
        config = SourceGroupConfig(media_path="/test.mp4")
        assert config.loop is True
        assert config.speed == 1.0
        assert config.audio_sample_rate == 44100
        assert config.audio_channels == 2
        assert config.audio_buffer_duration == 2.0
        assert config.sources == []

    def test_multi_source_config(self):
        """Test configuration with multiple sources."""
        config = SourceGroupConfig(
            media_path="/test.mp4",
            sources=[
                SourceDefinition(name="Video", type="video", dimensions=[64, 32]),
                SourceDefinition(name="Spectrum", type="audio_visualizer", dimensions=[60]),
                SourceDefinition(name="Matrix", type="audio_visualizer", dimensions=[16, 16]),
            ]
        )
        assert len(config.sources) == 3
        assert config.sources[0].type == "video"
        assert config.sources[1].type == "audio_visualizer"
        assert config.sources[2].dimensions == [16, 16]


class TestMediaSourceGroupFromArgs:
    """Tests for MediaSourceGroup.from_args()."""

    def test_single_video(self):
        """Test creating group with single video source."""
        group = MediaSourceGroup.from_args(
            media_path="/test.mp4",
            video_specs=["TestVideo:64x32"],
        )
        assert group.config.media_path == "/test.mp4"
        assert len(group.config.sources) == 1
        assert group.config.sources[0].name == "TestVideo"
        assert group.config.sources[0].dimensions == [64, 32]

    def test_video_with_fit_mode(self):
        """Test video source with fit mode."""
        group = MediaSourceGroup.from_args(
            media_path="/test.mp4",
            video_specs=["Video:32x16:cover"],
        )
        assert group.config.sources[0].fit_mode == "cover"

    def test_audio_visualizer(self):
        """Test creating group with audio visualizer."""
        group = MediaSourceGroup.from_args(
            media_path="/test.mp4",
            audio_specs=["Spectrum:spectrum:60"],
        )
        assert len(group.config.sources) == 1
        assert group.config.sources[0].type == "audio_visualizer"
        assert group.config.sources[0].visualizer_type == "spectrum"
        assert group.config.sources[0].dimensions == [60]

    def test_audio_visualizer_with_color_mode(self):
        """Test audio visualizer with color mode."""
        group = MediaSourceGroup.from_args(
            media_path="/test.mp4",
            audio_specs=["VU:vu:60:fire"],
        )
        assert group.config.sources[0].color_mode == "fire"

    def test_matrix_audio_visualizer(self):
        """Test matrix audio visualizer."""
        group = MediaSourceGroup.from_args(
            media_path="/test.mp4",
            audio_specs=["Spectrogram:spectrogram:16x16"],
        )
        assert group.config.sources[0].dimensions == [16, 16]

    def test_mixed_sources(self):
        """Test mixed video and audio sources."""
        group = MediaSourceGroup.from_args(
            media_path="/test.mp4",
            video_specs=["Video:64x32"],
            audio_specs=["Spectrum:spectrum:60", "VU:vu_bar:16x8"],
        )
        assert len(group.config.sources) == 3
        types = [s.type for s in group.config.sources]
        assert types.count("video") == 1
        assert types.count("audio_visualizer") == 2

    def test_loop_flag(self):
        """Test loop flag is passed correctly."""
        group_loop = MediaSourceGroup.from_args("/test.mp4", video_specs=["V:16"], loop=True)
        group_no_loop = MediaSourceGroup.from_args("/test.mp4", video_specs=["V:16"], loop=False)
        assert group_loop.config.loop is True
        assert group_no_loop.config.loop is False


class TestMediaSourceGroupFromYaml:
    """Tests for MediaSourceGroup.from_yaml()."""

    def test_load_yaml_config(self):
        """Test loading configuration from YAML file."""
        yaml_content = """
media_path: /path/to/video.mp4
loop: true
speed: 1.0

sources:
  - name: Video
    type: video
    dimensions: [64, 32]
    fit_mode: contain

  - name: Spectrum
    type: audio_visualizer
    dimensions: [60]
    visualizer_type: spectrum
    color_mode: rainbow
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = f.name

        try:
            group = MediaSourceGroup.from_yaml(yaml_path)
            assert group.config.media_path == "/path/to/video.mp4"
            assert group.config.loop is True
            assert len(group.config.sources) == 2
            assert group.config.sources[0].name == "Video"
            assert group.config.sources[0].type == "video"
            assert group.config.sources[1].name == "Spectrum"
            assert group.config.sources[1].visualizer_type == "spectrum"
        finally:
            Path(yaml_path).unlink()

    def test_load_yaml_with_audio_settings(self):
        """Test loading YAML with audio settings."""
        yaml_content = """
media_path: /test.mp4
audio_sample_rate: 48000
audio_buffer_duration: 3.0
sources:
  - name: Test
    type: video
    dimensions: [16, 16]
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = f.name

        try:
            group = MediaSourceGroup.from_yaml(yaml_path)
            assert group.config.audio_sample_rate == 48000
            assert group.config.audio_buffer_duration == 3.0
        finally:
            Path(yaml_path).unlink()

    def test_file_not_found(self):
        """Test error when YAML file not found."""
        with pytest.raises(FileNotFoundError):
            MediaSourceGroup.from_yaml("/nonexistent/path/config.yaml")


class TestMediaSourceGroupProperties:
    """Tests for MediaSourceGroup properties."""

    def test_config_property(self):
        """Test config property."""
        config = SourceGroupConfig(media_path="/test.mp4")
        group = MediaSourceGroup(config)
        assert group.config is config

    def test_sources_property_empty(self):
        """Test sources property when not started."""
        config = SourceGroupConfig(media_path="/test.mp4")
        group = MediaSourceGroup(config)
        assert group.sources == []

    def test_is_running_property(self):
        """Test is_running property."""
        config = SourceGroupConfig(media_path="/test.mp4")
        group = MediaSourceGroup(config)
        assert group.is_running is False

    def test_context_property_before_start(self):
        """Test context is None before start."""
        config = SourceGroupConfig(media_path="/test.mp4")
        group = MediaSourceGroup(config)
        assert group.context is None


class TestMediaSourceGroupSourceCreation:
    """Tests for source creation from definitions."""

    @pytest.fixture
    def mock_context(self):
        """Create a mock SharedMediaContext."""
        context = MagicMock()
        context.media_path = "/test.mp4"
        context.audio_sample_rate = 44100
        context.audio_channels = 2
        context.duration = 10.0
        context.has_audio = True
        context.frame_rate = 30.0
        context.position = 0.0
        context.playing = True
        context.paused = False
        context.loop = True
        context.start = AsyncMock()
        context.stop = AsyncMock()
        context.read_frame = AsyncMock(return_value=np.zeros((480, 640, 3), dtype=np.uint8))
        context.get_audio_samples = AsyncMock(return_value=np.zeros((2048,), dtype=np.float32))
        return context

    def test_create_video_source(self, mock_context):
        """Test creating a video source from definition."""
        config = SourceGroupConfig(
            media_path="/test.mp4",
            sources=[SourceDefinition(name="Video", type="video", dimensions=[64, 32])],
        )
        group = MediaSourceGroup(config)
        group._context = mock_context

        sources = group._create_sources()
        assert len(sources) == 1
        assert isinstance(sources[0], VideoLogicalSource)
        assert sources[0].config.name == "Video"
        assert sources[0].width == 64
        assert sources[0].height == 32

    def test_create_audio_visualizer_source(self, mock_context):
        """Test creating an audio visualizer source from definition."""
        config = SourceGroupConfig(
            media_path="/test.mp4",
            sources=[SourceDefinition(
                name="Spectrum",
                type="audio_visualizer",
                dimensions=[60],
                visualizer_type="spectrum",
            )],
        )
        group = MediaSourceGroup(config)
        group._context = mock_context

        sources = group._create_sources()
        assert len(sources) == 1
        assert isinstance(sources[0], AudioVisualizerSource)
        assert sources[0].config.name == "Spectrum"
        assert sources[0].width == 60
        assert sources[0].height == 1

    def test_create_multiple_sources(self, mock_context):
        """Test creating multiple sources."""
        config = SourceGroupConfig(
            media_path="/test.mp4",
            sources=[
                SourceDefinition(name="Video", type="video", dimensions=[64, 32]),
                SourceDefinition(name="Spectrum", type="audio_visualizer", dimensions=[60]),
                SourceDefinition(name="VU", type="audio_visualizer", dimensions=[16, 8]),
            ],
        )
        group = MediaSourceGroup(config)
        group._context = mock_context

        sources = group._create_sources()
        assert len(sources) == 3
        assert isinstance(sources[0], VideoLogicalSource)
        assert isinstance(sources[1], AudioVisualizerSource)
        assert isinstance(sources[2], AudioVisualizerSource)

    def test_unknown_source_type_skipped(self, mock_context):
        """Test that unknown source types are skipped."""
        config = SourceGroupConfig(
            media_path="/test.mp4",
            sources=[
                SourceDefinition(name="Unknown", type="unknown_type", dimensions=[16]),
                SourceDefinition(name="Video", type="video", dimensions=[16]),
            ],
        )
        group = MediaSourceGroup(config)
        group._context = mock_context

        sources = group._create_sources()
        assert len(sources) == 1
        assert sources[0].config.name == "Video"


class TestMediaSourceGroupStats:
    """Tests for MediaSourceGroup.get_stats()."""

    def test_get_stats_before_start(self):
        """Test get_stats when not started."""
        config = SourceGroupConfig(media_path="/test.mp4")
        group = MediaSourceGroup(config)

        stats = group.get_stats()
        assert stats["media_path"] == "/test.mp4"
        assert stats["running"] is False
        assert stats["context"] is None
        assert stats["sources"] == []


class TestMediaSourceGroupLookup:
    """Tests for source lookup methods."""

    @pytest.fixture
    def mock_context(self):
        """Create a mock SharedMediaContext."""
        context = MagicMock()
        context.media_path = "/test.mp4"
        context.audio_sample_rate = 44100
        context.audio_channels = 2
        context.duration = 10.0
        context.has_audio = True
        context.frame_rate = 30.0
        return context

    def test_get_source_by_name(self, mock_context):
        """Test finding a source by name."""
        config = SourceGroupConfig(
            media_path="/test.mp4",
            sources=[
                SourceDefinition(name="Video", type="video", dimensions=[16]),
                SourceDefinition(name="Spectrum", type="audio_visualizer", dimensions=[60]),
            ],
        )
        group = MediaSourceGroup(config)
        group._context = mock_context
        group._sources = group._create_sources()

        video = group.get_source_by_name("Video")
        spectrum = group.get_source_by_name("Spectrum")
        unknown = group.get_source_by_name("Unknown")

        assert video is not None
        assert video.config.name == "Video"
        assert spectrum is not None
        assert spectrum.config.name == "Spectrum"
        assert unknown is None
