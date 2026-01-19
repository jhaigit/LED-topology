"""Tests for Phase 6: Control Coordination.

Tests that playback controls (play/pause/seek/loop/speed) affect all
sources sharing a SharedMediaContext and that state changes are
properly synchronized across sources.
"""

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import numpy as np
import pytest

from ltp_media_source.shared_context import SharedMediaContext, StateChangeCallback
from ltp_media_source.sources.base import LogicalSource, LogicalSourceConfig
from ltp_media_source.sources.video_source import VideoLogicalSource, VideoLogicalSourceConfig
from ltp_media_source.sources.audio_visualizer import (
    AudioVisualizerSource,
    AudioVisualizerSourceConfig,
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
def shared_context(mock_media_input):
    """Create a SharedMediaContext with mock input."""
    return SharedMediaContext(mock_media_input)


# =============================================================================
# State Observer Tests
# =============================================================================


class TestStateObservers:
    """Tests for state observer registration and notification."""

    def test_add_state_observer(self, shared_context):
        """Test registering a state observer."""
        callback = MagicMock()
        shared_context.add_state_observer(callback)
        assert callback in shared_context._state_observers

    def test_add_state_observer_no_duplicates(self, shared_context):
        """Test that duplicate observers are not added."""
        callback = MagicMock()
        shared_context.add_state_observer(callback)
        shared_context.add_state_observer(callback)
        assert shared_context._state_observers.count(callback) == 1

    def test_remove_state_observer(self, shared_context):
        """Test unregistering a state observer."""
        callback = MagicMock()
        shared_context.add_state_observer(callback)
        shared_context.remove_state_observer(callback)
        assert callback not in shared_context._state_observers

    def test_remove_nonexistent_observer(self, shared_context):
        """Test removing an observer that was never added."""
        callback = MagicMock()
        # Should not raise
        shared_context.remove_state_observer(callback)

    def test_notify_state_change(self, shared_context):
        """Test that state changes notify observers."""
        callback = MagicMock()
        shared_context.add_state_observer(callback)

        shared_context._notify_state_change(paused=True, loop=False)

        callback.assert_called_once_with({"paused": True, "loop": False})

    def test_notify_multiple_observers(self, shared_context):
        """Test that all observers are notified."""
        callback1 = MagicMock()
        callback2 = MagicMock()
        shared_context.add_state_observer(callback1)
        shared_context.add_state_observer(callback2)

        shared_context._notify_state_change(speed=2.0)

        callback1.assert_called_once_with({"speed": 2.0})
        callback2.assert_called_once_with({"speed": 2.0})

    def test_notify_empty_changes_no_call(self, shared_context):
        """Test that empty changes don't trigger notification."""
        callback = MagicMock()
        shared_context.add_state_observer(callback)

        shared_context._notify_state_change()

        callback.assert_not_called()

    def test_observer_error_does_not_stop_other_observers(self, shared_context):
        """Test that one observer error doesn't prevent others from being called."""
        failing_callback = MagicMock(side_effect=RuntimeError("Observer failed"))
        working_callback = MagicMock()

        shared_context.add_state_observer(failing_callback)
        shared_context.add_state_observer(working_callback)

        # Should not raise
        shared_context._notify_state_change(paused=True)

        # Both should have been called
        failing_callback.assert_called_once()
        working_callback.assert_called_once()


# =============================================================================
# Playback Control Notification Tests
# =============================================================================


class TestPlaybackControlNotifications:
    """Tests for playback controls triggering state notifications."""

    @pytest.mark.asyncio
    async def test_play_notifies_observers(self, shared_context):
        """Test that play() notifies observers."""
        callback = MagicMock()
        shared_context.add_state_observer(callback)

        await shared_context.start()
        callback.reset_mock()

        await shared_context.play()

        callback.assert_called_once_with({"paused": False, "playing": True})

    @pytest.mark.asyncio
    async def test_pause_notifies_observers(self, shared_context):
        """Test that pause() notifies observers."""
        callback = MagicMock()
        shared_context.add_state_observer(callback)

        await shared_context.start()
        callback.reset_mock()

        await shared_context.pause()

        callback.assert_called_once_with({"paused": True, "playing": False})

    @pytest.mark.asyncio
    async def test_toggle_pause_notifies_observers(self, shared_context):
        """Test that toggle_pause() notifies observers."""
        callback = MagicMock()
        shared_context.add_state_observer(callback)

        await shared_context.start()
        callback.reset_mock()

        # Toggle to paused
        await shared_context.toggle_pause()
        callback.assert_called_with({"paused": True, "playing": False})

        callback.reset_mock()

        # Toggle back to playing
        await shared_context.toggle_pause()
        callback.assert_called_with({"paused": False, "playing": True})

    @pytest.mark.asyncio
    async def test_seek_notifies_observers(self, shared_context):
        """Test that seek() notifies observers."""
        callback = MagicMock()
        shared_context.add_state_observer(callback)

        await shared_context.start()
        callback.reset_mock()

        await shared_context.seek(30.0)

        callback.assert_called_once_with({"position": 30.0})

    def test_loop_setter_notifies_observers(self, shared_context):
        """Test that setting loop notifies observers."""
        callback = MagicMock()
        shared_context.add_state_observer(callback)

        shared_context.loop = False

        callback.assert_called_once_with({"loop": False})

    def test_speed_setter_notifies_observers(self, shared_context):
        """Test that setting speed notifies observers."""
        callback = MagicMock()
        shared_context.add_state_observer(callback)

        shared_context.speed = 2.0

        callback.assert_called_once_with({"speed": 2.0})

    def test_speed_clamping(self, shared_context):
        """Test that speed is clamped to valid range."""
        # Test minimum
        shared_context.speed = 0.01
        assert shared_context.speed == 0.1

        # Test maximum
        shared_context.speed = 10.0
        assert shared_context.speed == 4.0


# =============================================================================
# Logical Source State Synchronization Tests
# =============================================================================


class TestLogicalSourceStateSync:
    """Tests for LogicalSource state synchronization via observers."""

    @pytest.fixture
    def mock_context_with_mocks(self, mock_media_input):
        """Create a SharedMediaContext for testing with sources."""
        context = SharedMediaContext(mock_media_input)
        return context

    def test_on_context_state_change_updates_paused(self, mock_context_with_mocks):
        """Test that _on_context_state_change updates paused control."""
        config = VideoLogicalSourceConfig(
            name="TestVideo",
            dimensions=[16, 16],
        )
        source = VideoLogicalSource(mock_context_with_mocks, config)

        # Initially not paused
        assert source._controls.get_value("paused") is False

        # Simulate state change from context
        source._on_context_state_change({"paused": True})

        assert source._controls.get_value("paused") is True

    def test_on_context_state_change_updates_loop(self, mock_context_with_mocks):
        """Test that _on_context_state_change updates loop control."""
        config = VideoLogicalSourceConfig(
            name="TestVideo",
            dimensions=[16, 16],
        )
        source = VideoLogicalSource(mock_context_with_mocks, config)

        source._on_context_state_change({"loop": False})

        assert source._controls.get_value("loop") is False

    def test_on_context_state_change_updates_speed(self, mock_context_with_mocks):
        """Test that _on_context_state_change updates speed control."""
        config = VideoLogicalSourceConfig(
            name="TestVideo",
            dimensions=[16, 16],
        )
        source = VideoLogicalSource(mock_context_with_mocks, config)

        source._on_context_state_change({"speed": 1.5})

        assert source._controls.get_value("speed") == 1.5

    def test_on_context_state_change_multiple_values(self, mock_context_with_mocks):
        """Test that _on_context_state_change handles multiple values."""
        config = VideoLogicalSourceConfig(
            name="TestVideo",
            dimensions=[16, 16],
        )
        source = VideoLogicalSource(mock_context_with_mocks, config)

        source._on_context_state_change({
            "paused": True,
            "loop": False,
            "speed": 0.5,
        })

        assert source._controls.get_value("paused") is True
        assert source._controls.get_value("loop") is False
        assert source._controls.get_value("speed") == 0.5

    def test_on_context_state_change_ignores_unknown_keys(self, mock_context_with_mocks):
        """Test that unknown state keys are gracefully ignored."""
        config = VideoLogicalSourceConfig(
            name="TestVideo",
            dimensions=[16, 16],
        )
        source = VideoLogicalSource(mock_context_with_mocks, config)

        # Should not raise
        source._on_context_state_change({
            "position": 30.0,  # Not a control
            "playing": True,  # Not a control
            "unknown_key": "value",
        })


# =============================================================================
# Source Start/Stop Observer Registration Tests
# =============================================================================


class TestSourceObserverRegistration:
    """Tests for source start/stop observer registration."""

    @pytest.fixture
    def patched_source(self, shared_context):
        """Create a source with patched network components."""
        config = VideoLogicalSourceConfig(
            name="TestVideo",
            dimensions=[16, 16],
        )
        source = VideoLogicalSource(shared_context, config)
        return source

    @pytest.mark.asyncio
    async def test_start_registers_observer(self, shared_context, patched_source):
        """Test that start() registers the source as a state observer."""
        # Patch network components to avoid real network operations
        with patch.object(patched_source, "_control_server", None):
            with patch.object(patched_source, "_advertiser", None):
                with patch("ltp_media_source.sources.base.ControlServer") as mock_server:
                    with patch("ltp_media_source.sources.base.SourceAdvertiser") as mock_adv:
                        mock_server_instance = AsyncMock()
                        mock_server_instance.actual_port = 12345
                        mock_server.return_value = mock_server_instance

                        mock_adv_instance = AsyncMock()
                        mock_adv.return_value = mock_adv_instance

                        await patched_source.start()

                        # Verify observer was registered
                        assert patched_source._on_context_state_change in shared_context._state_observers

                        await patched_source.stop()

    @pytest.mark.asyncio
    async def test_stop_unregisters_observer(self, shared_context, patched_source):
        """Test that stop() unregisters the source as a state observer."""
        with patch.object(patched_source, "_control_server", None):
            with patch.object(patched_source, "_advertiser", None):
                with patch("ltp_media_source.sources.base.ControlServer") as mock_server:
                    with patch("ltp_media_source.sources.base.SourceAdvertiser") as mock_adv:
                        mock_server_instance = AsyncMock()
                        mock_server_instance.actual_port = 12345
                        mock_server.return_value = mock_server_instance

                        mock_adv_instance = AsyncMock()
                        mock_adv.return_value = mock_adv_instance

                        await patched_source.start()
                        await patched_source.stop()

                        # Verify observer was unregistered
                        assert patched_source._on_context_state_change not in shared_context._state_observers

    @pytest.mark.asyncio
    async def test_start_syncs_initial_state(self, shared_context, patched_source):
        """Test that start() syncs initial state from context."""
        # Set context state before starting
        shared_context._paused = True
        shared_context._loop = False
        shared_context._speed = 1.5

        with patch.object(patched_source, "_control_server", None):
            with patch.object(patched_source, "_advertiser", None):
                with patch("ltp_media_source.sources.base.ControlServer") as mock_server:
                    with patch("ltp_media_source.sources.base.SourceAdvertiser") as mock_adv:
                        mock_server_instance = AsyncMock()
                        mock_server_instance.actual_port = 12345
                        mock_server.return_value = mock_server_instance

                        mock_adv_instance = AsyncMock()
                        mock_adv.return_value = mock_adv_instance

                        await patched_source.start()

                        # Verify initial state was synced
                        assert patched_source._controls.get_value("paused") is True
                        assert patched_source._controls.get_value("loop") is False
                        assert patched_source._controls.get_value("speed") == 1.5

                        await patched_source.stop()


# =============================================================================
# Multi-Source Coordination Tests
# =============================================================================


class TestMultiSourceCoordination:
    """Tests for coordination across multiple sources."""

    @pytest.fixture
    def multi_source_setup(self, shared_context):
        """Create multiple sources sharing the same context."""
        video_config = VideoLogicalSourceConfig(
            name="Video1",
            dimensions=[64, 32],
        )
        video_source = VideoLogicalSource(shared_context, video_config)

        audio_config = AudioVisualizerSourceConfig(
            name="Spectrum1",
            dimensions=[60],
            visualizer_type="spectrum",
        )
        audio_source = AudioVisualizerSource(shared_context, audio_config)

        return shared_context, video_source, audio_source

    def test_state_change_propagates_to_all_sources(self, multi_source_setup):
        """Test that state changes propagate to all sources."""
        context, video_source, audio_source = multi_source_setup

        # Register both sources as observers (normally done in start())
        context.add_state_observer(video_source._on_context_state_change)
        context.add_state_observer(audio_source._on_context_state_change)

        # Set loop on context
        context.loop = False

        # Both sources should have updated
        assert video_source._controls.get_value("loop") is False
        assert audio_source._controls.get_value("loop") is False

    def test_speed_change_propagates_to_all_sources(self, multi_source_setup):
        """Test that speed changes propagate to all sources."""
        context, video_source, audio_source = multi_source_setup

        context.add_state_observer(video_source._on_context_state_change)
        context.add_state_observer(audio_source._on_context_state_change)

        context.speed = 2.0

        assert video_source._controls.get_value("speed") == 2.0
        assert audio_source._controls.get_value("speed") == 2.0

    @pytest.mark.asyncio
    async def test_pause_propagates_to_all_sources(self, multi_source_setup):
        """Test that pause propagates to all sources."""
        context, video_source, audio_source = multi_source_setup

        context.add_state_observer(video_source._on_context_state_change)
        context.add_state_observer(audio_source._on_context_state_change)

        await context.start()
        await context.pause()

        assert video_source._controls.get_value("paused") is True
        assert audio_source._controls.get_value("paused") is True

        await context.stop()


# =============================================================================
# Control Set Forwarding Tests
# =============================================================================


class TestControlSetForwarding:
    """Tests for control set commands forwarding to context."""

    @pytest.fixture
    def source_with_context(self, shared_context):
        """Create a source with shared context."""
        config = VideoLogicalSourceConfig(
            name="TestVideo",
            dimensions=[16, 16],
        )
        return VideoLogicalSource(shared_context, config)

    @pytest.mark.asyncio
    async def test_control_set_paused_forwards_to_context(self, source_with_context, shared_context):
        """Test that setting paused control forwards to context."""
        await shared_context.start()

        # Create a mock message
        message = MagicMock()
        message.seq = 1
        message.data = {"values": {"paused": True}}

        response = await source_with_context._handle_control_set(message)

        # Context should be paused
        assert shared_context.paused is True
        assert response.data.get("applied", {}).get("paused") is True

        await shared_context.stop()

    @pytest.mark.asyncio
    async def test_control_set_loop_forwards_to_context(self, source_with_context, shared_context):
        """Test that setting loop control forwards to context."""
        message = MagicMock()
        message.seq = 1
        message.data = {"values": {"loop": False}}

        response = await source_with_context._handle_control_set(message)

        assert shared_context.loop is False

    @pytest.mark.asyncio
    async def test_control_set_speed_forwards_to_context(self, source_with_context, shared_context):
        """Test that setting speed control forwards to context."""
        message = MagicMock()
        message.seq = 1
        message.data = {"values": {"speed": 1.5}}

        response = await source_with_context._handle_control_set(message)

        assert shared_context.speed == 1.5

    @pytest.mark.asyncio
    async def test_control_set_seek_forwards_to_context(self, source_with_context, shared_context):
        """Test that seek command forwards to context."""
        await shared_context.start()

        message = MagicMock()
        message.seq = 1
        message.data = {"values": {"seek": 30.0}}

        response = await source_with_context._handle_control_set(message)

        # Position should have been updated (mock returns True for seek)
        assert response.data.get("applied", {}).get("seek") == 30.0

        await shared_context.stop()

    @pytest.mark.asyncio
    async def test_control_set_local_control_not_forwarded(self, source_with_context, shared_context):
        """Test that local controls are not forwarded to context."""
        message = MagicMock()
        message.seq = 1
        message.data = {"values": {"brightness": 0.5}}

        response = await source_with_context._handle_control_set(message)

        # Brightness should be set locally
        assert source_with_context._controls.get_value("brightness") == 0.5


# =============================================================================
# Integration Test with MediaSourceGroup
# =============================================================================


class TestMediaSourceGroupControlCoordination:
    """Integration tests for control coordination via MediaSourceGroup."""

    @pytest.fixture
    def mock_video_input(self):
        """Create a mock video input."""
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

    def test_source_group_sources_share_context(self, mock_video_input):
        """Test that sources in a group share the same context."""
        from ltp_media_source.source_group import (
            MediaSourceGroup,
            SourceGroupConfig,
            SourceDefinition,
        )

        config = SourceGroupConfig(
            media_path="/test/video.mp4",
            sources=[
                SourceDefinition(name="Video", type="video", dimensions=[64, 32]),
                SourceDefinition(name="Spectrum", type="audio_visualizer", dimensions=[60]),
            ],
        )
        group = MediaSourceGroup(config)

        # Create context manually for testing
        context = SharedMediaContext(mock_video_input)
        group._context = context
        group._sources = group._create_sources()

        # All sources should have the same context
        assert group._sources[0]._context is context
        assert group._sources[1]._context is context

    def test_source_group_context_state_affects_all_sources(self, mock_video_input):
        """Test that context state changes affect all sources in group."""
        from ltp_media_source.source_group import (
            MediaSourceGroup,
            SourceGroupConfig,
            SourceDefinition,
        )

        config = SourceGroupConfig(
            media_path="/test/video.mp4",
            sources=[
                SourceDefinition(name="Video", type="video", dimensions=[64, 32]),
                SourceDefinition(name="Spectrum", type="audio_visualizer", dimensions=[60]),
            ],
        )
        group = MediaSourceGroup(config)

        context = SharedMediaContext(mock_video_input)
        group._context = context
        group._sources = group._create_sources()

        # Register observers (normally done in start())
        for source in group._sources:
            context.add_state_observer(source._on_context_state_change)

        # Change speed on context
        context.speed = 0.5

        # All sources should have updated speed
        for source in group._sources:
            assert source._controls.get_value("speed") == 0.5
