"""Tests for audio input modules."""

import pytest
import numpy as np


class TestAudioFileInput:
    """Tests for AudioFileInput class."""

    def test_import_without_pyav(self, monkeypatch):
        """Test graceful handling when PyAV is not available."""
        # Simulate missing av module
        import sys
        if 'ltp_media_source.inputs.audio' in sys.modules:
            del sys.modules['ltp_media_source.inputs.audio']

        monkeypatch.setitem(sys.modules, 'av', None)

        # Should not raise
        from ltp_media_source.inputs import HAS_AUDIO_INPUTS
        # HAS_AUDIO_INPUTS may be True or False depending on import state
        assert isinstance(HAS_AUDIO_INPUTS, bool)

    def test_audio_file_input_properties(self):
        """Test AudioFileInput properties before opening."""
        try:
            from ltp_media_source.inputs.audio import AudioFileInput
        except ImportError:
            pytest.skip("PyAV not available")

        # Create input (don't need to open for property tests)
        try:
            audio_input = AudioFileInput(
                path="/nonexistent/file.mp3",
                sample_rate=44100,
                frame_rate=30.0,
            )
        except RuntimeError:
            pytest.skip("PyAV not available")

        # Check properties
        assert audio_input.has_audio is True
        assert audio_input.native_dimensions == (0, 0)
        assert audio_input.is_live is False
        assert audio_input.sample_rate == 44100
        assert audio_input.frame_rate == 30.0

    def test_audio_file_input_type(self):
        """Test AudioFileInput input_type."""
        try:
            from ltp_media_source.inputs.audio import AudioFileInput
            assert AudioFileInput.input_type == "audio_file"
        except ImportError:
            pytest.skip("PyAV not available")

    def test_audio_only_context(self):
        """Test AudioOnlyContext wrapper."""
        try:
            from ltp_media_source.inputs.audio import AudioFileInput, AudioOnlyContext
        except ImportError:
            pytest.skip("PyAV not available")

        # Would need a real audio file to fully test
        # Just verify the class exists and has expected methods
        assert hasattr(AudioOnlyContext, 'write_audio_samples')
        assert hasattr(AudioOnlyContext, 'get_audio_samples')
        assert hasattr(AudioOnlyContext, 'get_audio_mono')


class TestMicrophoneInput:
    """Tests for MicrophoneInput class."""

    def test_import_without_sounddevice(self, monkeypatch):
        """Test graceful handling when sounddevice is not available."""
        import sys
        if 'ltp_media_source.inputs.microphone' in sys.modules:
            del sys.modules['ltp_media_source.inputs.microphone']

        monkeypatch.setitem(sys.modules, 'sounddevice', None)

        # Should not raise on import
        from ltp_media_source.inputs import HAS_AUDIO_INPUTS
        assert isinstance(HAS_AUDIO_INPUTS, bool)

    def test_microphone_input_properties(self):
        """Test MicrophoneInput properties before opening."""
        try:
            from ltp_media_source.inputs.microphone import MicrophoneInput
        except (ImportError, RuntimeError):
            pytest.skip("sounddevice not available")

        try:
            mic_input = MicrophoneInput(
                device=None,  # Default device
                sample_rate=44100,
                channels=2,
            )
        except RuntimeError:
            pytest.skip("sounddevice not available")

        # Check properties
        assert mic_input.has_audio is True
        assert mic_input.native_dimensions == (0, 0)
        assert mic_input.is_live is True
        assert mic_input.duration is None  # Live sources have no duration
        assert mic_input.sample_rate == 44100

    def test_microphone_input_type(self):
        """Test MicrophoneInput input_type."""
        try:
            from ltp_media_source.inputs.microphone import MicrophoneInput
            assert MicrophoneInput.input_type == "microphone"
        except (ImportError, RuntimeError):
            pytest.skip("sounddevice not available")

    def test_system_audio_input_type(self):
        """Test SystemAudioInput input_type."""
        try:
            from ltp_media_source.inputs.microphone import SystemAudioInput
            assert SystemAudioInput.input_type == "system_audio"
        except (ImportError, RuntimeError):
            pytest.skip("sounddevice not available")

    def test_list_audio_devices(self):
        """Test list_audio_devices function."""
        try:
            from ltp_media_source.inputs.microphone import list_audio_devices
        except (ImportError, RuntimeError):
            pytest.skip("sounddevice not available")

        devices = list_audio_devices()
        assert isinstance(devices, list)
        # Each device should be a dict with expected keys
        for dev in devices:
            assert 'index' in dev
            assert 'name' in dev
            assert 'channels' in dev
            assert 'sample_rate' in dev

    def test_get_default_input_device(self):
        """Test get_default_input_device function."""
        try:
            from ltp_media_source.inputs.microphone import get_default_input_device
        except (ImportError, RuntimeError):
            pytest.skip("sounddevice not available")

        # Should return int or None
        device = get_default_input_device()
        assert device is None or isinstance(device, int)


class TestAudioPlayback:
    """Tests for AudioPlayback class."""

    def test_import_without_sounddevice(self, monkeypatch):
        """Test graceful handling when sounddevice is not available."""
        import sys
        if 'ltp_media_source.audio_playback' in sys.modules:
            del sys.modules['ltp_media_source.audio_playback']

        monkeypatch.setitem(sys.modules, 'sounddevice', None)

        # Should not raise on import (graceful fallback)
        from ltp_media_source import HAS_SOUNDDEVICE
        assert isinstance(HAS_SOUNDDEVICE, bool)

    def test_audio_playback_properties(self):
        """Test AudioPlayback properties before starting."""
        try:
            from ltp_media_source.audio_playback import AudioPlayback
        except (ImportError, RuntimeError):
            pytest.skip("sounddevice not available")

        try:
            playback = AudioPlayback(
                sample_rate=44100,
                channels=2,
            )
        except RuntimeError:
            pytest.skip("sounddevice not available")

        # Check properties
        assert playback.sample_rate == 44100
        assert playback.channels == 2
        assert playback.is_running is False
        assert playback.is_paused is False
        assert playback.volume == 1.0

    def test_audio_playback_volume(self):
        """Test AudioPlayback volume property."""
        try:
            from ltp_media_source.audio_playback import AudioPlayback
        except (ImportError, RuntimeError):
            pytest.skip("sounddevice not available")

        try:
            playback = AudioPlayback(sample_rate=44100, channels=2)
        except RuntimeError:
            pytest.skip("sounddevice not available")

        # Set volume
        playback.volume = 0.5
        assert playback.volume == 0.5

        # Volume should clamp to [0, 1]
        playback.volume = 1.5
        assert playback.volume == 1.0

        playback.volume = -0.5
        assert playback.volume == 0.0

    def test_list_output_devices(self):
        """Test list_output_devices function."""
        try:
            from ltp_media_source.audio_playback import list_output_devices
        except (ImportError, RuntimeError):
            pytest.skip("sounddevice not available")

        devices = list_output_devices()
        assert isinstance(devices, list)
        # Each device should be a dict with expected keys
        for dev in devices:
            assert 'index' in dev
            assert 'name' in dev
            assert 'channels' in dev
            assert 'sample_rate' in dev

    def test_get_default_output_device(self):
        """Test get_default_output_device function."""
        try:
            from ltp_media_source.audio_playback import get_default_output_device
        except (ImportError, RuntimeError):
            pytest.skip("sounddevice not available")

        # Should return int or None
        device = get_default_output_device()
        assert device is None or isinstance(device, int)


class TestSharedMediaContextAudio:
    """Tests for SharedMediaContext audio features."""

    def test_is_audio_only_property(self):
        """Test is_audio_only property."""
        from ltp_media_source.shared_context import SharedMediaContext
        from ltp_media_source.inputs.base import MediaInput, FitMode

        # Create a mock audio-only input
        class MockAudioInput(MediaInput):
            def __init__(self):
                super().__init__(path=None, fit_mode=FitMode.CONTAIN, loop=True, speed=1.0)
                self._opened = False

            @property
            def has_audio(self) -> bool:
                return True

            @property
            def native_dimensions(self) -> tuple[int, int]:
                return (0, 0)  # Audio-only

            @property
            def is_live(self) -> bool:
                return False

            @property
            def frame_rate(self) -> float:
                return 30.0

            @property
            def duration(self) -> float | None:
                return 10.0

            def open(self) -> None:
                self._opened = True

            def close(self) -> None:
                self._opened = False

            def read_frame(self):
                return None

            def seek(self, position: float) -> bool:
                return True

        mock_input = MockAudioInput()
        context = SharedMediaContext(mock_input)

        assert context.is_audio_only is True
        assert context.has_audio is True

    def test_is_audio_only_false_for_video(self):
        """Test is_audio_only is False for video input."""
        from ltp_media_source.shared_context import SharedMediaContext
        from ltp_media_source.inputs.base import MediaInput, FitMode

        # Create a mock video input
        class MockVideoInput(MediaInput):
            def __init__(self):
                super().__init__(path=None, fit_mode=FitMode.CONTAIN, loop=True, speed=1.0)
                self._opened = False

            @property
            def has_audio(self) -> bool:
                return True

            @property
            def native_dimensions(self) -> tuple[int, int]:
                return (1920, 1080)  # Video dimensions

            @property
            def is_live(self) -> bool:
                return False

            @property
            def frame_rate(self) -> float:
                return 30.0

            @property
            def duration(self) -> float | None:
                return 10.0

            def open(self) -> None:
                self._opened = True

            def close(self) -> None:
                self._opened = False

            def read_frame(self):
                return np.zeros((1080, 1920, 3), dtype=np.uint8)

            def seek(self, position: float) -> bool:
                return True

        mock_input = MockVideoInput()
        context = SharedMediaContext(mock_input)

        assert context.is_audio_only is False
        assert context.has_audio is True

    def test_audio_playback_integration(self):
        """Test SharedMediaContext with audio playback."""
        from ltp_media_source.shared_context import SharedMediaContext
        from ltp_media_source.inputs.base import MediaInput, FitMode

        # Create a mock input
        class MockInput(MediaInput):
            def __init__(self):
                super().__init__(path=None, fit_mode=FitMode.CONTAIN, loop=True, speed=1.0)
                self._opened = False

            @property
            def has_audio(self) -> bool:
                return True

            @property
            def native_dimensions(self) -> tuple[int, int]:
                return (0, 0)

            @property
            def is_live(self) -> bool:
                return False

            @property
            def frame_rate(self) -> float:
                return 30.0

            @property
            def duration(self) -> float | None:
                return 10.0

            def open(self) -> None:
                self._opened = True

            def close(self) -> None:
                self._opened = False

            def read_frame(self):
                return None

            def seek(self, position: float) -> bool:
                return True

        # Create a mock audio playback
        class MockAudioPlayback:
            def __init__(self):
                self.is_running = False
                self.volume = 1.0
                self.written_samples = []

            def start(self):
                self.is_running = True

            def stop(self):
                self.is_running = False

            def pause(self):
                pass

            def resume(self):
                pass

            def write(self, samples):
                self.written_samples.append(samples)
                return True

        mock_input = MockInput()
        mock_playback = MockAudioPlayback()

        context = SharedMediaContext(mock_input, audio_playback=mock_playback)

        assert context.audio_playback is mock_playback

        # Write samples and verify they go to playback
        mock_playback.is_running = True
        test_samples = np.zeros((1024, 2), dtype=np.float32)
        context.write_audio_samples(test_samples)

        assert len(mock_playback.written_samples) == 1
        assert mock_playback.written_samples[0] is test_samples

    def test_get_state_includes_audio_info(self):
        """Test get_state includes audio information."""
        from ltp_media_source.shared_context import SharedMediaContext
        from ltp_media_source.inputs.base import MediaInput, FitMode

        class MockInput(MediaInput):
            def __init__(self):
                super().__init__(path=None, fit_mode=FitMode.CONTAIN, loop=True, speed=1.0)
                self._opened = False

            @property
            def has_audio(self) -> bool:
                return True

            @property
            def native_dimensions(self) -> tuple[int, int]:
                return (0, 0)

            @property
            def is_live(self) -> bool:
                return False

            @property
            def frame_rate(self) -> float:
                return 30.0

            @property
            def duration(self) -> float | None:
                return 10.0

            def open(self) -> None:
                self._opened = True

            def close(self) -> None:
                self._opened = False

            def read_frame(self):
                return None

            def seek(self, position: float) -> bool:
                return True

        context = SharedMediaContext(MockInput())
        state = context.get_state()

        assert 'has_audio' in state
        assert 'is_audio_only' in state
        assert state['has_audio'] is True
        assert state['is_audio_only'] is True


class TestInputRegistry:
    """Tests for audio input type registration."""

    def test_audio_inputs_in_registry(self):
        """Test that audio inputs are registered when available."""
        from ltp_media_source.inputs import INPUT_TYPES, HAS_AUDIO_INPUTS

        if HAS_AUDIO_INPUTS:
            assert 'audio_file' in INPUT_TYPES
            assert 'microphone' in INPUT_TYPES
            assert 'system_audio' in INPUT_TYPES
        else:
            assert 'audio_file' not in INPUT_TYPES
            assert 'microphone' not in INPUT_TYPES
            assert 'system_audio' not in INPUT_TYPES

    def test_create_input_factory(self):
        """Test create_input factory function with audio types."""
        from ltp_media_source.inputs import create_input, HAS_AUDIO_INPUTS

        if not HAS_AUDIO_INPUTS:
            pytest.skip("Audio inputs not available")

        # Should raise for unknown type
        with pytest.raises(ValueError):
            create_input("unknown_type")
