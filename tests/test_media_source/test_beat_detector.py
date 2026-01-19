"""Unit tests for BeatDetector."""

import time
import numpy as np
import pytest

from ltp_media_source.audio.beat_detector import BeatDetector, MultibandBeatDetector


class TestBeatDetector:
    """Tests for BeatDetector class."""

    def test_init_defaults(self):
        """Test default initialization."""
        detector = BeatDetector()
        assert detector.sensitivity == 1.5
        assert detector.decay == 0.95
        assert detector.history_size == 43
        assert detector.min_interval == 0.1
        assert detector.beat is False
        assert detector.intensity == 0.0
        assert detector.beat_count == 0

    def test_init_custom(self):
        """Test custom initialization."""
        detector = BeatDetector(
            sensitivity=2.0,
            decay=0.9,
            history_size=50,
            min_interval=0.2,
        )
        assert detector.sensitivity == 2.0
        assert detector.decay == 0.9
        assert detector.history_size == 50
        assert detector.min_interval == 0.2

    def test_sensitivity_clamping(self):
        """Test sensitivity is clamped to minimum of 1.0."""
        detector = BeatDetector(sensitivity=0.5)
        assert detector.sensitivity == 1.0

    def test_decay_clamping(self):
        """Test decay is clamped to 0.0-1.0."""
        detector1 = BeatDetector(decay=-0.5)
        assert detector1.decay == 0.0

        detector2 = BeatDetector(decay=1.5)
        assert detector2.decay == 1.0

    def test_update_with_rms(self):
        """Test updating with RMS values."""
        detector = BeatDetector(history_size=10, min_interval=0.0)

        # Fill history with low values
        for _ in range(15):
            detector.update(rms=0.1)

        assert detector.beat is False

    def test_update_with_bands(self):
        """Test updating with bass/mids/highs."""
        detector = BeatDetector(history_size=10, min_interval=0.0)

        # Fill history
        for _ in range(15):
            detector.update(bass=0.1, mids=0.1, highs=0.1)

        assert detector.beat is False

    def test_beat_detection(self):
        """Test that a spike triggers beat detection."""
        detector = BeatDetector(
            sensitivity=1.3,
            history_size=10,
            min_interval=0.0,
        )

        # Fill history with low values
        for _ in range(15):
            detector.update(rms=0.1)

        # Now send a spike
        result = detector.update(rms=0.5)

        # Should detect beat
        assert result is True
        assert detector.beat is True
        assert detector.intensity == 1.0
        assert detector.beat_count == 1

    def test_min_interval_enforcement(self):
        """Test minimum interval between beats."""
        detector = BeatDetector(
            sensitivity=1.3,
            history_size=10,
            min_interval=0.5,  # 500ms
        )

        # Fill history
        for _ in range(15):
            detector.update(rms=0.1)

        # Trigger first beat
        detector.update(rms=0.5)
        assert detector.beat_count == 1

        # Immediately try another beat - should be blocked
        detector.update(rms=0.5)
        assert detector.beat_count == 1  # Still 1

    def test_intensity_decay(self):
        """Test that intensity decays over time."""
        detector = BeatDetector(
            sensitivity=1.3,
            decay=0.5,
            history_size=10,
            min_interval=0.0,
        )

        # Fill history
        for _ in range(15):
            detector.update(rms=0.1)

        # Trigger beat
        detector.update(rms=0.5)
        assert detector.intensity == 1.0

        # Update without beat - intensity should decay
        detector.update(rms=0.1)
        assert detector.intensity == 0.5

        detector.update(rms=0.1)
        assert detector.intensity == 0.25

    def test_get_intensity(self):
        """Test get_intensity method."""
        detector = BeatDetector()
        assert detector.get_intensity() == 0.0

        # After manipulation
        detector._intensity = 0.75
        assert detector.get_intensity() == 0.75

    def test_update_with_spectrum(self):
        """Test updating with spectrum array."""
        detector = BeatDetector(history_size=10, min_interval=0.0)

        spectrum = np.random.rand(1025).astype(np.float32) * 0.1

        for _ in range(15):
            detector.update(spectrum=spectrum)

        assert detector.beat is False

    def test_no_input_returns_false(self):
        """Test that update with no input returns False."""
        detector = BeatDetector()
        result = detector.update()
        assert result is False
        assert detector.beat is False

    def test_reset(self):
        """Test reset clears all state."""
        detector = BeatDetector(history_size=10, min_interval=0.0)

        # Build up some state
        for _ in range(15):
            detector.update(rms=0.1)
        detector.update(rms=0.5)

        assert detector.beat_count > 0

        detector.reset()

        assert detector.beat is False
        assert detector.intensity == 0.0
        assert detector.beat_count == 0
        assert detector.current_energy == 0.0
        assert detector.average_energy == 0.0

    def test_get_band_intensities(self):
        """Test get_band_intensities returns dict."""
        detector = BeatDetector(history_size=10)

        # Update with band values
        detector.update(bass=0.2, mids=0.3, highs=0.1)

        intensities = detector.get_band_intensities()
        assert "bass" in intensities
        assert "mids" in intensities
        assert "highs" in intensities
        assert intensities["bass"] == 0.2
        assert intensities["mids"] == 0.3
        assert intensities["highs"] == 0.1

    def test_frequency_weights(self):
        """Test custom frequency weights."""
        detector = BeatDetector(
            frequency_weights={"bass": 5.0, "mids": 1.0, "highs": 0.0},
            history_size=10,
        )

        # Update with equal band values
        detector.update(bass=0.1, mids=0.1, highs=0.1)

        # Energy should be weighted: 0.1*5 + 0.1*1 + 0.1*0 = 0.6
        assert abs(detector.current_energy - 0.6) < 0.01


class TestMultibandBeatDetector:
    """Tests for MultibandBeatDetector class."""

    def test_init(self):
        """Test initialization."""
        detector = MultibandBeatDetector()
        assert detector.bass_detector is not None
        assert detector.mids_detector is not None
        assert detector.highs_detector is not None

    def test_init_custom_sensitivities(self):
        """Test custom sensitivities."""
        detector = MultibandBeatDetector(
            bass_sensitivity=1.2,
            mids_sensitivity=1.6,
            highs_sensitivity=2.0,
        )
        assert detector.bass_detector.sensitivity == 1.2
        assert detector.mids_detector.sensitivity == 1.6
        assert detector.highs_detector.sensitivity == 2.0

    def test_update(self):
        """Test update method."""
        detector = MultibandBeatDetector()

        # Fill history
        for _ in range(50):
            result = detector.update(bass=0.1, mids=0.1, highs=0.1)

        assert "bass" in result
        assert "mids" in result
        assert "highs" in result

    def test_any_beat_property(self):
        """Test any_beat property."""
        detector = MultibandBeatDetector()

        # No beats initially
        for _ in range(50):
            detector.update(bass=0.1, mids=0.1, highs=0.1)

        assert detector.any_beat is False

    def test_individual_beat_properties(self):
        """Test individual beat properties."""
        detector = MultibandBeatDetector()

        assert detector.bass_beat is False
        assert detector.mids_beat is False
        assert detector.highs_beat is False

    def test_intensity_properties(self):
        """Test intensity properties."""
        detector = MultibandBeatDetector()

        assert detector.bass_intensity == 0.0
        assert detector.mids_intensity == 0.0
        assert detector.highs_intensity == 0.0

    def test_get_intensities(self):
        """Test get_intensities method."""
        detector = MultibandBeatDetector()

        intensities = detector.get_intensities()
        assert "bass" in intensities
        assert "mids" in intensities
        assert "highs" in intensities

    def test_reset(self):
        """Test reset clears all detectors."""
        detector = MultibandBeatDetector()

        # Build up state
        for _ in range(50):
            detector.update(bass=0.1, mids=0.1, highs=0.1)

        detector.reset()

        assert detector.bass_detector.beat_count == 0
        assert detector.mids_detector.beat_count == 0
        assert detector.highs_detector.beat_count == 0

    def test_independent_detection(self):
        """Test that bands detect independently."""
        detector = MultibandBeatDetector(
            bass_sensitivity=1.3,
            mids_sensitivity=1.3,
            highs_sensitivity=1.3,
        )

        # Override min_interval for testing
        detector.bass_detector.min_interval = 0.0
        detector.mids_detector.min_interval = 0.0
        detector.highs_detector.min_interval = 0.0

        # Fill with low values
        for _ in range(50):
            detector.update(bass=0.1, mids=0.1, highs=0.1)

        # Spike only in bass
        result = detector.update(bass=0.5, mids=0.1, highs=0.1)

        # Only bass should detect
        assert result["bass"] is True or detector.bass_detector.beat_count > 0
