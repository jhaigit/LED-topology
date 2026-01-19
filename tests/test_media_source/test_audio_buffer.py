"""Unit tests for AudioRingBuffer."""

import numpy as np
import pytest

from ltp_media_source.audio_buffer import AudioRingBuffer


class TestAudioRingBuffer:
    """Tests for AudioRingBuffer class."""

    def test_init_defaults(self):
        """Test default initialization."""
        buf = AudioRingBuffer()
        assert buf.sample_rate == 44100
        assert buf.channels == 2
        assert buf.buffer_samples == 44100 * 2  # 2 seconds
        assert buf.total_written == 0

    def test_init_custom(self):
        """Test custom initialization."""
        buf = AudioRingBuffer(
            duration_sec=1.0,
            sample_rate=48000,
            channels=1,
        )
        assert buf.sample_rate == 48000
        assert buf.channels == 1
        assert buf.buffer_samples == 48000

    def test_write_stereo(self):
        """Test writing stereo samples."""
        buf = AudioRingBuffer(duration_sec=1.0, sample_rate=1000, channels=2)

        # Write 100 stereo samples
        samples = np.random.randn(100, 2).astype(np.float32)
        buf.write(samples)

        assert buf.total_written == 100

    def test_write_mono_to_stereo(self):
        """Test writing mono samples to stereo buffer (should duplicate)."""
        buf = AudioRingBuffer(duration_sec=1.0, sample_rate=1000, channels=2)

        # Write 100 mono samples
        samples = np.random.randn(100).astype(np.float32)
        buf.write(samples)

        assert buf.total_written == 100

        # Read back and verify both channels have same data
        result = buf.read_recent(100)
        assert result.shape == (100, 2)
        np.testing.assert_array_equal(result[:, 0], result[:, 1])

    def test_write_wrap_around(self):
        """Test buffer wrap-around."""
        buf = AudioRingBuffer(duration_sec=0.1, sample_rate=1000, channels=1)
        # Buffer holds 100 samples

        # Write 150 samples (should wrap)
        samples = np.arange(150).astype(np.float32).reshape(-1, 1)
        buf.write(samples)

        assert buf.total_written == 150

        # Read recent 100 - should be samples 50-149
        result = buf.read_recent(100)
        expected = np.arange(50, 150).astype(np.float32).reshape(-1, 1)
        np.testing.assert_array_equal(result, expected)

    def test_read_recent_partial(self):
        """Test reading when less data written than requested."""
        buf = AudioRingBuffer(duration_sec=1.0, sample_rate=1000, channels=1)

        # Write only 50 samples
        samples = np.ones((50, 1), dtype=np.float32)
        buf.write(samples)

        # Request 100 samples - should pad with zeros
        result = buf.read_recent(100)
        assert result.shape == (100, 1)

        # Last 50 should be ones
        np.testing.assert_array_equal(result[-50:], np.ones((50, 1), dtype=np.float32))

    def test_read_recent_exact(self):
        """Test reading exact amount of data written."""
        buf = AudioRingBuffer(duration_sec=1.0, sample_rate=1000, channels=1)

        samples = np.arange(100).astype(np.float32).reshape(-1, 1)
        buf.write(samples)

        result = buf.read_recent(100)
        np.testing.assert_array_equal(result, samples)

    def test_read_at_time(self):
        """Test reading samples at a specific time."""
        buf = AudioRingBuffer(duration_sec=1.0, sample_rate=1000, channels=1)

        # Write 500 samples (0.5 seconds at 1000Hz)
        samples = np.arange(500).astype(np.float32).reshape(-1, 1)
        buf.write(samples)

        # Read at time 0.3 (sample 300)
        result = buf.read_at_time(0.3, 10)
        assert result.shape == (10, 1)

    def test_clear(self):
        """Test clearing the buffer."""
        buf = AudioRingBuffer(duration_sec=0.1, sample_rate=1000, channels=1)

        samples = np.ones((100, 1), dtype=np.float32)
        buf.write(samples)
        assert buf.total_written == 100

        buf.clear()
        assert buf.total_written == 0

        result = buf.read_recent(50)
        np.testing.assert_array_equal(result, np.zeros((50, 1), dtype=np.float32))

    def test_get_mono(self):
        """Test converting stereo to mono."""
        buf = AudioRingBuffer(duration_sec=1.0, sample_rate=1000, channels=2)

        # Create stereo samples: left=1, right=3 -> mono should be 2
        samples = np.zeros((100, 2), dtype=np.float32)
        samples[:, 0] = 1.0
        samples[:, 1] = 3.0
        buf.write(samples)

        result = buf.read_recent(100)
        mono = buf.get_mono(result)

        assert mono.shape == (100,)
        np.testing.assert_array_almost_equal(mono, np.full(100, 2.0, dtype=np.float32))

    def test_multiple_writes(self):
        """Test multiple sequential writes."""
        buf = AudioRingBuffer(duration_sec=1.0, sample_rate=1000, channels=1)

        # Write in chunks
        for i in range(10):
            chunk = np.full((10, 1), i, dtype=np.float32)
            buf.write(chunk)

        assert buf.total_written == 100

        result = buf.read_recent(100)
        for i in range(10):
            expected = np.full((10, 1), i, dtype=np.float32)
            np.testing.assert_array_equal(result[i * 10 : (i + 1) * 10], expected)

    def test_empty_write(self):
        """Test writing empty array."""
        buf = AudioRingBuffer(duration_sec=1.0, sample_rate=1000, channels=1)

        samples = np.array([], dtype=np.float32)
        buf.write(samples)

        assert buf.total_written == 0

    def test_thread_safety(self):
        """Test concurrent read/write operations."""
        import threading

        buf = AudioRingBuffer(duration_sec=1.0, sample_rate=44100, channels=2)
        errors = []

        def writer():
            try:
                for _ in range(100):
                    samples = np.random.randn(441, 2).astype(np.float32)
                    buf.write(samples)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    buf.read_recent(441)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
