"""Unit tests for SharedMediaContext."""

import asyncio
from unittest.mock import MagicMock, AsyncMock, PropertyMock

import numpy as np
import pytest

from ltp_media_source.shared_context import SharedMediaContext
from ltp_media_source.inputs.base import MediaInput


class MockMediaInput(MediaInput):
    """Mock media input for testing."""

    input_type = "mock"

    def __init__(self):
        super().__init__(path="/mock/video.mp4", loop=True, speed=1.0)
        self._width = 640
        self._height = 480
        self._fps = 30.0
        self._duration_sec = 10.0
        self._frame_data = np.zeros((480, 640, 3), dtype=np.uint8)

    def open(self):
        self._opened = True

    def close(self):
        self._opened = False

    def read_frame(self):
        if not self._opened:
            return None
        self._position += 1.0 / self._fps
        self._frame_index += 1
        return self._frame_data.copy()

    def seek(self, position):
        self._position = position
        self._frame_index = int(position * self._fps)
        return True

    @property
    def frame_rate(self):
        return self._fps

    @property
    def duration(self):
        return self._duration_sec

    @property
    def native_dimensions(self):
        return (self._width, self._height)


class TestSharedMediaContext:
    """Tests for SharedMediaContext class."""

    def test_init(self):
        """Test initialization."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        assert ctx.media_path == "/mock/video.mp4"
        assert ctx.audio_sample_rate == 44100
        assert ctx.audio_channels == 2
        assert ctx.playing is False
        assert ctx.paused is False
        assert ctx.loop is True
        assert ctx.speed == 1.0

    def test_init_custom_audio(self):
        """Test initialization with custom audio settings."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(
            mock_input,
            audio_sample_rate=48000,
            audio_channels=1,
            audio_buffer_duration=1.0,
        )

        assert ctx.audio_sample_rate == 48000
        assert ctx.audio_channels == 1

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test start and stop lifecycle."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        await ctx.start()
        assert ctx.playing is True
        assert mock_input.is_opened is True

        await ctx.stop()
        assert ctx.playing is False
        assert mock_input.is_opened is False

    @pytest.mark.asyncio
    async def test_play_pause(self):
        """Test play/pause functionality."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        await ctx.start()
        assert ctx.playing is True
        assert ctx.paused is False

        await ctx.pause()
        assert ctx.paused is True
        assert ctx.playing is False

        await ctx.play()
        assert ctx.paused is False
        assert ctx.playing is True

        await ctx.toggle_pause()
        assert ctx.paused is True

        await ctx.stop()

    @pytest.mark.asyncio
    async def test_seek(self):
        """Test seeking."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        await ctx.start()

        result = await ctx.seek(5.0)
        assert result is True
        assert ctx.position == 5.0

        await ctx.stop()

    @pytest.mark.asyncio
    async def test_read_frame(self):
        """Test reading video frames."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        await ctx.start()

        frame = await ctx.read_frame()
        assert frame is not None
        assert frame.shape == (480, 640, 3)

        await ctx.stop()

    @pytest.mark.asyncio
    async def test_get_video_frame(self):
        """Test getting current video frame without advancing."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        await ctx.start()

        # Initially no frame
        frame = await ctx.get_video_frame()
        assert frame is None

        # Read a frame
        await ctx.read_frame()

        # Now should have frame
        frame = await ctx.get_video_frame()
        assert frame is not None

        await ctx.stop()

    @pytest.mark.asyncio
    async def test_read_frame_when_paused(self):
        """Test that read_frame returns cached frame when paused."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        await ctx.start()

        # Read first frame
        frame1 = await ctx.read_frame()

        # Pause
        await ctx.pause()

        # Read again - should return same frame
        frame2 = await ctx.read_frame()
        np.testing.assert_array_equal(frame1, frame2)

        await ctx.stop()

    def test_write_audio_samples(self):
        """Test writing audio samples to buffer."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        samples = np.random.randn(1024, 2).astype(np.float32)
        ctx.write_audio_samples(samples)

        assert ctx.audio_buffer.total_written == 1024

    @pytest.mark.asyncio
    async def test_get_audio_samples(self):
        """Test getting audio samples."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        # Write some samples
        samples = np.random.randn(2048, 2).astype(np.float32)
        ctx.write_audio_samples(samples)

        # Read back
        result = await ctx.get_audio_samples(1024)
        assert result.shape == (1024, 2)

    @pytest.mark.asyncio
    async def test_get_audio_mono(self):
        """Test getting mono audio samples."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        # Write stereo samples
        samples = np.random.randn(2048, 2).astype(np.float32)
        ctx.write_audio_samples(samples)

        # Get mono
        mono = await ctx.get_audio_mono(1024)
        assert mono.shape == (1024,)

    @pytest.mark.asyncio
    async def test_handle_control(self):
        """Test control command handling."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        await ctx.start()

        # Test pause control
        result = await ctx.handle_control("pause", None)
        assert result is True
        assert ctx.paused is True

        # Test play control
        result = await ctx.handle_control("play", None)
        assert result is True
        assert ctx.paused is False

        # Test paused control (boolean)
        result = await ctx.handle_control("paused", True)
        assert result is True
        assert ctx.paused is True

        # Test seek control
        result = await ctx.handle_control("seek", 3.0)
        assert result is True
        assert ctx.position == 3.0

        # Test loop control
        result = await ctx.handle_control("loop", False)
        assert result is True
        assert ctx.loop is False

        # Test speed control
        result = await ctx.handle_control("speed", 2.0)
        assert result is True
        assert ctx.speed == 2.0

        # Test unknown control
        result = await ctx.handle_control("unknown", None)
        assert result is False

        await ctx.stop()

    def test_get_state(self):
        """Test getting state dictionary."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        state = ctx.get_state()

        assert "position" in state
        assert "duration" in state
        assert "playing" in state
        assert "paused" in state
        assert "loop" in state
        assert "speed" in state
        assert "frame_number" in state
        assert "has_audio" in state

    def test_properties(self):
        """Test various property accessors."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        assert ctx.frame_rate == 30.0
        assert ctx.duration == 10.0
        assert ctx.native_dimensions == (640, 480)
        assert ctx.is_live is False
        assert ctx.has_audio is False  # MockMediaInput doesn't have has_audio

    def test_speed_clamping(self):
        """Test that speed is clamped to valid range."""
        mock_input = MockMediaInput()
        ctx = SharedMediaContext(mock_input)

        ctx.speed = 0.01  # Below minimum
        assert ctx.speed == 0.1

        ctx.speed = 10.0  # Above maximum
        assert ctx.speed == 4.0

        ctx.speed = 2.0  # Valid
        assert ctx.speed == 2.0
