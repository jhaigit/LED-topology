"""Unit tests for AudioAnalyzer."""

import numpy as np
import pytest

from ltp_media_source.audio.analyzer import AudioAnalyzer


class TestAudioAnalyzer:
    """Tests for AudioAnalyzer class."""

    def test_init_defaults(self):
        """Test default initialization."""
        analyzer = AudioAnalyzer()
        assert analyzer.sample_rate == 44100
        assert analyzer.fft_size == 2048
        assert analyzer.smoothing == 0.3
        assert analyzer.gain == 1.0
        assert analyzer.num_bins == 1025  # fft_size // 2 + 1

    def test_init_custom(self):
        """Test custom initialization."""
        analyzer = AudioAnalyzer(
            sample_rate=48000,
            fft_size=4096,
            smoothing=0.5,
            gain=2.0,
        )
        assert analyzer.sample_rate == 48000
        assert analyzer.fft_size == 4096
        assert analyzer.smoothing == 0.5
        assert analyzer.gain == 2.0

    def test_smoothing_clamping(self):
        """Test smoothing value is clamped."""
        analyzer1 = AudioAnalyzer(smoothing=-0.5)
        assert analyzer1.smoothing == 0.0

        analyzer2 = AudioAnalyzer(smoothing=1.5)
        assert analyzer2.smoothing == 1.0

    def test_analyze_silence(self):
        """Test analyzing silent audio."""
        analyzer = AudioAnalyzer(smoothing=0.0)  # No smoothing for deterministic test
        samples = np.zeros(2048, dtype=np.float32)
        analyzer.analyze(samples)

        assert analyzer.rms == 0.0
        assert analyzer.peak == 0.0
        np.testing.assert_array_equal(analyzer.spectrum, np.zeros(1025))

    def test_analyze_sine_wave(self):
        """Test analyzing a pure sine wave."""
        analyzer = AudioAnalyzer(sample_rate=44100, fft_size=2048, smoothing=0.0)

        # Generate 440 Hz sine wave
        t = np.arange(2048) / 44100
        frequency = 440
        samples = np.sin(2 * np.pi * frequency * t).astype(np.float32)

        analyzer.analyze(samples)

        # Check that we have non-zero spectrum
        assert np.max(analyzer.spectrum) > 0

        # Check RMS is close to expected for sine wave (1/sqrt(2) for unit amplitude)
        expected_rms = 1.0 / np.sqrt(2)
        assert abs(analyzer.rms - expected_rms) < 0.1

        # Peak should be close to 1.0
        assert abs(analyzer.peak - 1.0) < 0.1

    def test_analyze_stereo(self):
        """Test analyzing stereo audio (should average to mono)."""
        analyzer = AudioAnalyzer(smoothing=0.0)

        # Stereo samples: left = 0.5, right = 0.5
        samples = np.full((2048, 2), 0.5, dtype=np.float32)
        analyzer.analyze(samples)

        # RMS should be 0.5
        assert abs(analyzer.rms - 0.5) < 0.01

    def test_waveform_storage(self):
        """Test that waveform is stored correctly."""
        analyzer = AudioAnalyzer(fft_size=1024)

        samples = np.random.randn(1024).astype(np.float32)
        analyzer.analyze(samples)

        np.testing.assert_array_almost_equal(analyzer.waveform, samples)

    def test_waveform_padding(self):
        """Test short input is padded."""
        analyzer = AudioAnalyzer(fft_size=2048)

        samples = np.ones(512, dtype=np.float32)
        analyzer.analyze(samples)

        # Waveform should be padded to fft_size
        assert len(analyzer.waveform) == 2048
        np.testing.assert_array_equal(analyzer.waveform[:512], np.ones(512))
        np.testing.assert_array_equal(analyzer.waveform[512:], np.zeros(2048 - 512))

    def test_waveform_truncation(self):
        """Test long input uses most recent samples."""
        analyzer = AudioAnalyzer(fft_size=1024)

        samples = np.arange(2048, dtype=np.float32)
        analyzer.analyze(samples)

        # Should use last 1024 samples
        expected = np.arange(1024, 2048, dtype=np.float32)
        np.testing.assert_array_equal(analyzer.waveform, expected)

    def test_gain_application(self):
        """Test gain is applied to input."""
        analyzer1 = AudioAnalyzer(gain=1.0, smoothing=0.0)
        analyzer2 = AudioAnalyzer(gain=2.0, smoothing=0.0)

        samples = np.full(2048, 0.25, dtype=np.float32)

        analyzer1.analyze(samples)
        analyzer2.analyze(samples)

        # RMS should be doubled with 2x gain
        assert abs(analyzer2.rms - analyzer1.rms * 2) < 0.01

    def test_get_spectrum_bands_linear(self):
        """Test linear band extraction."""
        analyzer = AudioAnalyzer(smoothing=0.0)

        # Create spectrum with known values
        samples = np.random.randn(2048).astype(np.float32)
        analyzer.analyze(samples)

        bands = analyzer.get_spectrum_bands(10, log_scale=False)
        assert len(bands) == 10
        assert all(b >= 0 for b in bands)

    def test_get_spectrum_bands_log(self):
        """Test logarithmic band extraction."""
        analyzer = AudioAnalyzer(smoothing=0.0)

        samples = np.random.randn(2048).astype(np.float32)
        analyzer.analyze(samples)

        bands = analyzer.get_spectrum_bands(10, log_scale=True)
        assert len(bands) == 10
        assert all(b >= 0 for b in bands)

    def test_get_frequency_range(self):
        """Test frequency range extraction."""
        analyzer = AudioAnalyzer(sample_rate=44100, fft_size=2048, smoothing=0.0)

        samples = np.random.randn(2048).astype(np.float32)
        analyzer.analyze(samples)

        # Get bass range
        bass = analyzer.get_frequency_range(20, 250)
        assert len(bass) > 0
        assert all(b >= 0 for b in bass)

    def test_get_bass_mids_highs(self):
        """Test bass/mids/highs extraction."""
        analyzer = AudioAnalyzer(smoothing=0.0)

        samples = np.random.randn(2048).astype(np.float32)
        analyzer.analyze(samples)

        bass = analyzer.get_bass()
        mids = analyzer.get_mids()
        highs = analyzer.get_highs()

        assert bass >= 0
        assert mids >= 0
        assert highs >= 0

        # Test tuple method
        b, m, h = analyzer.get_bass_mids_highs()
        assert b == bass
        assert m == mids
        assert h == highs

    def test_bin_frequency_conversion(self):
        """Test bin to frequency and back."""
        analyzer = AudioAnalyzer(sample_rate=44100, fft_size=2048)

        # 440 Hz should map to approximately the same bin
        bin_440 = analyzer.frequency_to_bin(440)
        freq_back = analyzer.bin_to_frequency(bin_440)

        # Should be within one bin's resolution
        assert abs(freq_back - 440) < analyzer._freq_resolution

    def test_smoothing_behavior(self):
        """Test temporal smoothing."""
        analyzer = AudioAnalyzer(smoothing=0.5)

        # First analysis: raw RMS = 1.0, smoothed = 0.0*0.5 + 1.0*0.5 = 0.5
        samples1 = np.ones(2048, dtype=np.float32)
        analyzer.analyze(samples1)
        rms1 = analyzer.rms
        assert abs(rms1 - 0.5) < 0.01

        # Second analysis with zeros: smoothed = 0.5*0.5 + 0.0*0.5 = 0.25
        samples2 = np.zeros(2048, dtype=np.float32)
        analyzer.analyze(samples2)
        rms2 = analyzer.rms
        assert abs(rms2 - 0.25) < 0.01

    def test_reset(self):
        """Test reset clears all state."""
        analyzer = AudioAnalyzer()

        samples = np.random.randn(2048).astype(np.float32)
        analyzer.analyze(samples)
        assert analyzer.rms > 0

        analyzer.reset()
        assert analyzer.rms == 0.0
        assert analyzer.peak == 0.0
        np.testing.assert_array_equal(analyzer.spectrum, np.zeros(1025))

    def test_frequencies_property(self):
        """Test frequencies array is correct."""
        analyzer = AudioAnalyzer(sample_rate=44100, fft_size=2048)

        freqs = analyzer.frequencies
        assert len(freqs) == 1025  # num_bins

        # First bin should be 0 Hz (DC)
        assert freqs[0] == 0.0

        # Last bin should be Nyquist
        assert abs(freqs[-1] - 22050) < 50  # ~Nyquist frequency
