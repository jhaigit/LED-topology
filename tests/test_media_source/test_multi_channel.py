"""Tests for Phase 7: Multi-Channel Protocol.

Tests for the MultiChannelSource class that provides multiple output channels
under a single network identity.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import numpy as np
import pytest
import yaml

from libltp import Message, MessageType, ColorFormat

from ltp_media_source.multi_channel import (
    MultiChannelSource,
    MultiChannelConfig,
    ChannelConfig,
    Channel,
    run_multi_channel_source,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_media_input():
    """Create a mock media input."""
    input_mock = MagicMock()
    input_mock.path = "/test/video.mp4"
    input_mock.duration = 60.0
    input_mock.frame_rate = 30.0
    input_mock.native_dimensions = (1920, 1080)
    input_mock.is_live = False
    input_mock.loop = True
    input_mock.speed = 1.0
    input_mock.position = 0.0
    input_mock.has_audio = True
    input_mock.open = MagicMock()
    input_mock.close = MagicMock()
    input_mock.read_frame = MagicMock(return_value=np.zeros((1080, 1920, 3), dtype=np.uint8))
    input_mock.seek = MagicMock(return_value=True)
    return input_mock


@pytest.fixture
def basic_config():
    """Create a basic multi-channel configuration."""
    return MultiChannelConfig(
        name="Test Multi-Channel",
        media_path="/test/video.mp4",
        channels=[
            ChannelConfig(
                id="video",
                name="Video",
                channel_type="video",
                dimensions=[64, 32],
            ),
            ChannelConfig(
                id="spectrum",
                name="Spectrum",
                channel_type="audio_visualizer",
                dimensions=[60],
                visualizer_type="spectrum",
            ),
        ],
        default_channel="video",
    )


@pytest.fixture
def mock_shared_context(mock_media_input):
    """Create a mock SharedMediaContext."""
    from ltp_media_source.shared_context import SharedMediaContext
    context = SharedMediaContext(mock_media_input)
    return context


# =============================================================================
# ChannelConfig Tests
# =============================================================================


class TestChannelConfig:
    """Tests for ChannelConfig."""

    def test_video_channel_config(self):
        """Test video channel configuration."""
        config = ChannelConfig(
            id="video",
            name="Video Output",
            channel_type="video",
            dimensions=[64, 32],
            fit_mode="cover",
        )
        assert config.id == "video"
        assert config.name == "Video Output"
        assert config.channel_type == "video"
        assert config.dimensions == [64, 32]
        assert config.fit_mode == "cover"

    def test_audio_channel_config(self):
        """Test audio visualizer channel configuration."""
        config = ChannelConfig(
            id="spectrum",
            name="Spectrum",
            channel_type="audio_visualizer",
            dimensions=[60],
            visualizer_type="spectrum",
            color_mode="fire",
        )
        assert config.id == "spectrum"
        assert config.channel_type == "audio_visualizer"
        assert config.visualizer_type == "spectrum"
        assert config.color_mode == "fire"

    def test_default_values(self):
        """Test default configuration values."""
        config = ChannelConfig(
            id="test",
            name="Test",
            channel_type="video",
        )
        assert config.dimensions == [16, 16]
        assert config.rate == 30
        assert config.fit_mode == "contain"
        assert config.color_format == ColorFormat.RGB


# =============================================================================
# MultiChannelConfig Tests
# =============================================================================


class TestMultiChannelConfig:
    """Tests for MultiChannelConfig."""

    def test_basic_config(self):
        """Test basic configuration."""
        config = MultiChannelConfig(
            name="Test",
            media_path="/test.mp4",
            channels=[
                ChannelConfig(id="ch1", name="Ch1", channel_type="video"),
            ],
        )
        assert config.name == "Test"
        assert config.media_path == "/test.mp4"
        assert len(config.channels) == 1

    def test_default_values(self):
        """Test default configuration values."""
        config = MultiChannelConfig(media_path="/test.mp4")
        assert config.name == "Multi-Channel Media"
        assert config.loop is True
        assert config.speed == 1.0
        assert config.audio_sample_rate == 44100
        assert config.channels == []
        assert config.default_channel is None

    def test_multi_channel_config(self):
        """Test configuration with multiple channels."""
        config = MultiChannelConfig(
            media_path="/test.mp4",
            channels=[
                ChannelConfig(id="video", name="Video", channel_type="video"),
                ChannelConfig(id="audio", name="Audio", channel_type="audio_visualizer"),
            ],
            default_channel="video",
        )
        assert len(config.channels) == 2
        assert config.default_channel == "video"


# =============================================================================
# Channel Tests
# =============================================================================


class TestChannel:
    """Tests for Channel class."""

    @pytest.fixture
    def mock_context(self, mock_media_input):
        """Create a mock context for channel tests."""
        from ltp_media_source.shared_context import SharedMediaContext
        context = MagicMock(spec=SharedMediaContext)
        context.audio_sample_rate = 44100
        context.get_video_frame = AsyncMock(return_value=np.zeros((1080, 1920, 3), dtype=np.uint8))
        context.get_audio_mono = AsyncMock(return_value=np.zeros(2048, dtype=np.float32))
        return context

    def test_video_channel_creation(self, mock_context):
        """Test creating a video channel."""
        config = ChannelConfig(
            id="video",
            name="Video",
            channel_type="video",
            dimensions=[64, 32],
        )
        channel = Channel(config, mock_context)

        assert channel.id == "video"
        assert channel.name == "Video"
        assert channel.width == 64
        assert channel.height == 32
        assert channel.channel_type == "video"
        assert channel._scaler is not None

    def test_audio_channel_creation(self, mock_context):
        """Test creating an audio visualizer channel."""
        config = ChannelConfig(
            id="spectrum",
            name="Spectrum",
            channel_type="audio_visualizer",
            dimensions=[60],
            visualizer_type="spectrum",
        )
        channel = Channel(config, mock_context)

        assert channel.id == "spectrum"
        assert channel.width == 60
        assert channel.height == 1
        assert channel.channel_type == "audio_visualizer"
        assert channel._analyzer is not None
        assert channel._visualizer is not None

    def test_linear_dimensions(self, mock_context):
        """Test linear (1D) channel dimensions."""
        config = ChannelConfig(
            id="linear",
            name="Linear",
            channel_type="audio_visualizer",
            dimensions=[100],
        )
        channel = Channel(config, mock_context)

        assert channel.width == 100
        assert channel.height == 1
        assert channel.dimensions == [100]

    def test_matrix_dimensions(self, mock_context):
        """Test matrix (2D) channel dimensions."""
        config = ChannelConfig(
            id="matrix",
            name="Matrix",
            channel_type="video",
            dimensions=[32, 16],
        )
        channel = Channel(config, mock_context)

        assert channel.width == 32
        assert channel.height == 16
        assert channel.dimensions == [32, 16]

    def test_get_metadata(self, mock_context):
        """Test channel metadata generation."""
        config = ChannelConfig(
            id="test",
            name="Test Channel",
            description="A test channel",
            channel_type="video",
            dimensions=[64, 32],
        )
        channel = Channel(config, mock_context)

        metadata = channel.get_metadata()
        assert metadata["id"] == "test"
        assert metadata["name"] == "Test Channel"
        assert metadata["description"] == "A test channel"
        assert metadata["type"] == "video"
        assert metadata["dimensions"] == [64, 32]
        assert "controls" in metadata

    def test_get_stats(self, mock_context):
        """Test channel statistics."""
        config = ChannelConfig(
            id="test",
            name="Test",
            channel_type="video",
            dimensions=[16, 16],
        )
        channel = Channel(config, mock_context)

        stats = channel.get_stats()
        assert stats["id"] == "test"
        assert stats["name"] == "Test"
        assert stats["type"] == "video"
        assert stats["frame_count"] == 0
        assert stats["subscribers"] == 0

    def test_control_get_set(self, mock_context):
        """Test channel control get/set."""
        config = ChannelConfig(
            id="test",
            name="Test",
            channel_type="video",
        )
        channel = Channel(config, mock_context)

        # Get default brightness
        values = channel.handle_control_get(["brightness"])
        assert "brightness" in values
        assert values["brightness"] == 1.0

        # Set brightness
        applied, errors = channel.handle_control_set({"brightness": 0.5})
        assert applied["brightness"] == 0.5
        assert not errors

        # Verify changed
        values = channel.handle_control_get(["brightness"])
        assert values["brightness"] == 0.5


# =============================================================================
# MultiChannelSource Tests
# =============================================================================


class TestMultiChannelSource:
    """Tests for MultiChannelSource."""

    def test_init(self, basic_config):
        """Test MultiChannelSource initialization."""
        source = MultiChannelSource(basic_config)

        assert source.config is basic_config
        assert source.context is None
        assert source.channels == {}
        assert source.is_running is False

    def test_from_args_video_only(self):
        """Test creating from args with video only."""
        source = MultiChannelSource.from_args(
            media_path="/test.mp4",
            name="Test",
            video_specs=["video:Video:64x32"],
        )

        assert source.config.media_path == "/test.mp4"
        assert source.config.name == "Test"
        assert len(source.config.channels) == 1
        assert source.config.channels[0].id == "video"
        assert source.config.channels[0].dimensions == [64, 32]
        assert source.config.default_channel == "video"

    def test_from_args_audio_only(self):
        """Test creating from args with audio only."""
        source = MultiChannelSource.from_args(
            media_path="/test.mp4",
            audio_specs=["spectrum:Spectrum:spectrum:60:fire"],
        )

        assert len(source.config.channels) == 1
        ch = source.config.channels[0]
        assert ch.id == "spectrum"
        assert ch.channel_type == "audio_visualizer"
        assert ch.visualizer_type == "spectrum"
        assert ch.dimensions == [60]
        assert ch.color_mode == "fire"

    def test_from_args_mixed(self):
        """Test creating from args with mixed channels."""
        source = MultiChannelSource.from_args(
            media_path="/test.mp4",
            video_specs=["video:Video:64x32:cover"],
            audio_specs=[
                "spectrum:Spectrum:spectrum:60",
                "spectro:Spectrogram:spectrogram:16x16:fire",
            ],
            default_channel="video",
        )

        assert len(source.config.channels) == 3
        types = [ch.channel_type for ch in source.config.channels]
        assert types.count("video") == 1
        assert types.count("audio_visualizer") == 2
        assert source.config.default_channel == "video"

    def test_from_yaml(self):
        """Test creating from YAML file."""
        yaml_content = """
name: Test Multi-Channel
media_path: /path/to/video.mp4
loop: true
default_channel: video

channels:
  - id: video
    name: Video
    channel_type: video
    dimensions: [64, 32]

  - id: spectrum
    name: Spectrum
    channel_type: audio_visualizer
    dimensions: [60]
    visualizer_type: spectrum
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = f.name

        try:
            source = MultiChannelSource.from_yaml(yaml_path)
            assert source.config.name == "Test Multi-Channel"
            assert source.config.media_path == "/path/to/video.mp4"
            assert len(source.config.channels) == 2
            assert source.config.default_channel == "video"
        finally:
            Path(yaml_path).unlink()

    def test_from_yaml_not_found(self):
        """Test error when YAML file not found."""
        with pytest.raises(FileNotFoundError):
            MultiChannelSource.from_yaml("/nonexistent/config.yaml")


# =============================================================================
# Capability Response Tests
# =============================================================================


class TestCapabilityResponse:
    """Tests for capability response with channel information."""

    @pytest.fixture
    def source_with_context(self, basic_config, mock_media_input):
        """Create source with mocked context."""
        source = MultiChannelSource(basic_config)

        # Mock context
        from ltp_media_source.shared_context import SharedMediaContext
        context = MagicMock(spec=SharedMediaContext)
        context.media_path = "/test/video.mp4"
        context.duration = 60.0
        context.has_audio = True
        context.frame_rate = 30.0
        context.paused = False
        context.loop = True
        context.speed = 1.0
        context.audio_sample_rate = 44100
        context.get_video_frame = AsyncMock(return_value=np.zeros((1080, 1920, 3), dtype=np.uint8))
        context.get_audio_mono = AsyncMock(return_value=np.zeros(2048, dtype=np.float32))

        source._context = context
        source._create_channels()
        return source

    def test_capability_response_includes_channels(self, source_with_context):
        """Test that capability response includes channels array."""
        message = Message(MessageType.CAPABILITY_REQUEST, seq=1)
        response = source_with_context._handle_capability_request(message)

        assert response.type == MessageType.CAPABILITY_RESPONSE
        # capability_response wraps data in 'device' key
        data = response.data.get("device", response.data)

        # Check channels array exists
        assert "channels" in data
        assert len(data["channels"]) == 2

        # Check video channel
        video_ch = next(ch for ch in data["channels"] if ch["id"] == "video")
        assert video_ch["name"] == "Video"
        assert video_ch["type"] == "video"
        assert video_ch["dimensions"] == [64, 32]

        # Check audio channel
        audio_ch = next(ch for ch in data["channels"] if ch["id"] == "spectrum")
        assert audio_ch["name"] == "Spectrum"
        assert audio_ch["type"] == "audio_visualizer"
        assert audio_ch["dimensions"] == [60]

    def test_capability_response_includes_default_channel(self, source_with_context):
        """Test that capability response includes default channel."""
        message = Message(MessageType.CAPABILITY_REQUEST, seq=1)
        response = source_with_context._handle_capability_request(message)

        data = response.data.get("device", response.data)
        assert data["default_channel"] == "video"

    def test_capability_response_includes_channel_count(self, source_with_context):
        """Test that capability response includes channel count."""
        message = Message(MessageType.CAPABILITY_REQUEST, seq=1)
        response = source_with_context._handle_capability_request(message)

        data = response.data.get("device", response.data)
        assert data["channel_count"] == 2

    def test_capability_response_protocol_version(self, source_with_context):
        """Test that capability response has extended protocol version."""
        message = Message(MessageType.CAPABILITY_REQUEST, seq=1)
        response = source_with_context._handle_capability_request(message)

        data = response.data.get("device", response.data)
        # Protocol version should indicate multi-channel support
        assert "protocol_version" in data


# =============================================================================
# Subscribe Routing Tests
# =============================================================================


class TestSubscribeRouting:
    """Tests for SUBSCRIBE message routing to channels."""

    @pytest.fixture
    def source_with_context(self, basic_config, mock_media_input):
        """Create source with mocked context."""
        source = MultiChannelSource(basic_config)

        from ltp_media_source.shared_context import SharedMediaContext
        context = MagicMock(spec=SharedMediaContext)
        context.media_path = "/test/video.mp4"
        context.duration = 60.0
        context.has_audio = True
        context.frame_rate = 30.0
        context.paused = False
        context.loop = True
        context.speed = 1.0
        context.audio_sample_rate = 44100
        context.get_video_frame = AsyncMock(return_value=np.zeros((1080, 1920, 3), dtype=np.uint8))
        context.get_audio_mono = AsyncMock(return_value=np.zeros(2048, dtype=np.float32))

        source._context = context
        source._create_channels()
        return source

    @pytest.mark.asyncio
    async def test_subscribe_with_channel(self, source_with_context):
        """Test subscribing to specific channel."""
        message = Message(
            MessageType.SUBSCRIBE,
            seq=1,
            channel="spectrum",
            callback={"host": "192.168.1.100", "port": 5000},
        )

        response = await source_with_context._handle_subscribe(message)

        assert response.data.get("status") == "ok"
        assert response.data.get("actual", {}).get("channel") == "spectrum"
        assert response.data.get("actual", {}).get("dimensions") == [60]

    @pytest.mark.asyncio
    async def test_subscribe_default_channel(self, source_with_context):
        """Test subscribing without channel uses default."""
        message = Message(
            MessageType.SUBSCRIBE,
            seq=1,
            callback={"host": "192.168.1.100", "port": 5000},
        )

        response = await source_with_context._handle_subscribe(message)

        assert response.data.get("status") == "ok"
        # Should use default channel (video)
        assert response.data.get("actual", {}).get("channel") == "video"

    @pytest.mark.asyncio
    async def test_subscribe_unknown_channel(self, source_with_context):
        """Test subscribing to unknown channel returns error."""
        message = Message(
            MessageType.SUBSCRIBE,
            seq=1,
            channel="nonexistent",
            callback={"host": "192.168.1.100", "port": 5000},
        )

        response = await source_with_context._handle_subscribe(message)

        assert response.data.get("status") == "error"
        assert "Unknown channel" in response.data.get("actual", {}).get("error", "")

    @pytest.mark.asyncio
    async def test_subscribe_missing_callback(self, source_with_context):
        """Test subscribing without callback returns error."""
        message = Message(
            MessageType.SUBSCRIBE,
            seq=1,
            channel="video",
        )

        response = await source_with_context._handle_subscribe(message)

        assert response.data.get("status") == "error"


# =============================================================================
# Control Handling Tests
# =============================================================================


class TestControlHandling:
    """Tests for control handling with channels."""

    @pytest.fixture
    def source_with_context(self, basic_config, mock_media_input):
        """Create source with mocked context."""
        source = MultiChannelSource(basic_config)

        from ltp_media_source.shared_context import SharedMediaContext
        context = MagicMock(spec=SharedMediaContext)
        context.media_path = "/test/video.mp4"
        context.duration = 60.0
        context.has_audio = True
        context.frame_rate = 30.0
        context.paused = False
        context.loop = True
        context.speed = 1.0
        context.position = 0.0
        context.playing = True
        context.audio_sample_rate = 44100
        context.handle_control = AsyncMock(return_value=True)
        context.get_video_frame = AsyncMock(return_value=np.zeros((1080, 1920, 3), dtype=np.uint8))
        context.get_audio_mono = AsyncMock(return_value=np.zeros(2048, dtype=np.float32))

        source._context = context
        source._create_channels()
        return source

    def test_control_get_shared(self, source_with_context):
        """Test getting shared control values."""
        message = Message(MessageType.CONTROL_GET, seq=1)
        response = source_with_context._handle_control_get(message)

        assert response.data.get("status") == "ok"
        values = response.data.get("values", {})
        assert "position" in values
        assert "duration" in values
        assert "playing" in values

    def test_control_get_channel_specific(self, source_with_context):
        """Test getting channel-specific control values."""
        message = Message(MessageType.CONTROL_GET, seq=1, channel="video")
        response = source_with_context._handle_control_get(message)

        assert response.data.get("status") == "ok"
        values = response.data.get("values", {})
        # Should include channel-prefixed values
        assert "video.brightness" in values

    @pytest.mark.asyncio
    async def test_control_set_shared(self, source_with_context):
        """Test setting shared control values."""
        message = Message(
            MessageType.CONTROL_SET,
            seq=1,
            values={"paused": True},
        )
        response = await source_with_context._handle_control_set(message)

        assert response.data.get("status") == "ok"
        source_with_context._context.handle_control.assert_called()

    @pytest.mark.asyncio
    async def test_control_set_channel_specific(self, source_with_context):
        """Test setting channel-specific control values."""
        message = Message(
            MessageType.CONTROL_SET,
            seq=1,
            values={"video.brightness": 0.5},
        )
        response = await source_with_context._handle_control_set(message)

        assert response.data.get("status") == "ok"
        applied = response.data.get("applied", {})
        assert applied.get("video.brightness") == 0.5

        # Verify channel control was updated
        channel = source_with_context.get_channel("video")
        assert channel._controls.get_value("brightness") == 0.5


# =============================================================================
# Statistics Tests
# =============================================================================


class TestStatistics:
    """Tests for source statistics."""

    def test_get_stats_before_start(self, basic_config):
        """Test statistics before source is started."""
        source = MultiChannelSource(basic_config)
        stats = source.get_stats()

        assert stats["name"] == "Test Multi-Channel"
        assert stats["media_path"] == "/test/video.mp4"
        assert stats["running"] is False
        assert stats["channel_count"] == 0
        assert stats["context"] is None

    def test_get_stats_with_channels(self, basic_config, mock_media_input):
        """Test statistics with channels created."""
        source = MultiChannelSource(basic_config)

        from ltp_media_source.shared_context import SharedMediaContext
        context = MagicMock(spec=SharedMediaContext)
        context.get_state = MagicMock(return_value={"position": 0, "playing": True})
        context.audio_sample_rate = 44100
        context.get_video_frame = AsyncMock()
        context.get_audio_mono = AsyncMock()

        source._context = context
        source._create_channels()

        stats = source.get_stats()
        assert stats["channel_count"] == 2
        assert "video" in stats["channels"]
        assert "spectrum" in stats["channels"]


# =============================================================================
# Channel Lookup Tests
# =============================================================================


class TestChannelLookup:
    """Tests for channel lookup methods."""

    def test_get_channel_existing(self, basic_config, mock_media_input):
        """Test getting an existing channel."""
        source = MultiChannelSource(basic_config)

        from ltp_media_source.shared_context import SharedMediaContext
        context = MagicMock(spec=SharedMediaContext)
        context.audio_sample_rate = 44100
        context.get_video_frame = AsyncMock()
        context.get_audio_mono = AsyncMock()

        source._context = context
        source._create_channels()

        video = source.get_channel("video")
        assert video is not None
        assert video.id == "video"

        spectrum = source.get_channel("spectrum")
        assert spectrum is not None
        assert spectrum.id == "spectrum"

    def test_get_channel_nonexistent(self, basic_config, mock_media_input):
        """Test getting a nonexistent channel."""
        source = MultiChannelSource(basic_config)

        from ltp_media_source.shared_context import SharedMediaContext
        context = MagicMock(spec=SharedMediaContext)
        context.audio_sample_rate = 44100
        context.get_video_frame = AsyncMock()
        context.get_audio_mono = AsyncMock()

        source._context = context
        source._create_channels()

        unknown = source.get_channel("unknown")
        assert unknown is None
