"""Beat and onset detection for audio-reactive effects.

Provides energy-based beat detection for driving reactive visualizations
like flashes, pulses, and rhythm-synced animations.
"""

import time
from collections import deque
from typing import Optional

import numpy as np


class BeatDetector:
    """Beat/onset detection for reactive effects.

    Uses energy-based detection comparing current energy to recent history.
    When current energy exceeds the average by a threshold, a beat is detected.

    The detector maintains a decaying "intensity" value that peaks at 1.0 on
    a beat and smoothly decays toward 0.0, useful for fading effects.
    """

    def __init__(
        self,
        sensitivity: float = 1.5,
        decay: float = 0.95,
        history_size: int = 43,
        min_interval: float = 0.1,
        frequency_weights: Optional[dict] = None,
    ):
        """Initialize the beat detector.

        Args:
            sensitivity: Beat threshold multiplier. Higher = less sensitive.
                        Typical range: 1.2 (very sensitive) to 2.5 (less sensitive)
            decay: Intensity decay factor per update (0.0 - 1.0).
                  Higher = slower decay. 0.95 at 30fps gives ~0.5s decay.
            history_size: Number of frames to average for energy baseline.
                         43 frames at 30fps ≈ 1.4 seconds.
            min_interval: Minimum seconds between beats (prevents double-triggers)
            frequency_weights: Optional dict mapping frequency bands to weights.
                              e.g., {"bass": 2.0, "mids": 1.0, "highs": 0.5}
        """
        self.sensitivity = max(1.0, sensitivity)
        self.decay = max(0.0, min(1.0, decay))
        self.history_size = max(1, history_size)
        self.min_interval = max(0.0, min_interval)

        # Default frequency weights emphasize bass
        if frequency_weights is None:
            frequency_weights = {
                "bass": 2.0,  # 20-250 Hz
                "mids": 1.0,  # 250-4000 Hz
                "highs": 0.5,  # 4000-20000 Hz
            }
        self.frequency_weights = frequency_weights

        # Energy history for baseline calculation
        self._energy_history: deque = deque(maxlen=history_size)

        # Sub-band energy histories for more sophisticated detection
        self._bass_history: deque = deque(maxlen=history_size)
        self._mids_history: deque = deque(maxlen=history_size)
        self._highs_history: deque = deque(maxlen=history_size)

        # Beat state
        self._beat_detected = False
        self._last_beat_time = 0.0
        self._intensity = 0.0

        # Statistics
        self._current_energy = 0.0
        self._average_energy = 0.0
        self._threshold = 0.0
        self._beat_count = 0

    @property
    def beat(self) -> bool:
        """True if a beat was detected on the last update."""
        return self._beat_detected

    @property
    def intensity(self) -> float:
        """Current beat intensity (0.0 - 1.0), decays after beat."""
        return self._intensity

    @property
    def beat_count(self) -> int:
        """Total number of beats detected."""
        return self._beat_count

    @property
    def current_energy(self) -> float:
        """Current weighted energy level."""
        return self._current_energy

    @property
    def average_energy(self) -> float:
        """Average energy from history."""
        return self._average_energy

    @property
    def threshold(self) -> float:
        """Current beat detection threshold."""
        return self._threshold

    def update(
        self,
        spectrum: Optional[np.ndarray] = None,
        rms: Optional[float] = None,
        bass: Optional[float] = None,
        mids: Optional[float] = None,
        highs: Optional[float] = None,
    ) -> bool:
        """Update beat detection with new audio data.

        Can be called with either:
        - spectrum: Full FFT spectrum array
        - rms: Simple volume level
        - bass/mids/highs: Pre-computed frequency band levels

        Returns:
            True if a beat was detected
        """
        current_time = time.time()

        # Apply decay to intensity
        self._intensity *= self.decay

        # Calculate current weighted energy
        if bass is not None and mids is not None and highs is not None:
            # Use provided band levels
            energy = (
                bass * self.frequency_weights.get("bass", 1.0)
                + mids * self.frequency_weights.get("mids", 1.0)
                + highs * self.frequency_weights.get("highs", 1.0)
            )
            self._bass_history.append(bass)
            self._mids_history.append(mids)
            self._highs_history.append(highs)
        elif spectrum is not None:
            # Calculate from spectrum
            energy = self._calculate_weighted_energy(spectrum)
        elif rms is not None:
            # Use simple RMS
            energy = rms
        else:
            # No input - just decay
            self._beat_detected = False
            return False

        self._current_energy = energy
        self._energy_history.append(energy)

        # Need enough history for reliable detection
        if len(self._energy_history) < self.history_size // 2:
            self._beat_detected = False
            return False

        # Calculate average energy and threshold
        self._average_energy = np.mean(list(self._energy_history))
        variance = np.var(list(self._energy_history))

        # Adaptive threshold: base threshold + variance-adjusted sensitivity
        # This helps handle both quiet and loud passages
        self._threshold = self._average_energy * self.sensitivity

        # Check for beat
        self._beat_detected = False

        if energy > self._threshold:
            # Check minimum interval
            time_since_last = current_time - self._last_beat_time
            if time_since_last >= self.min_interval:
                self._beat_detected = True
                self._last_beat_time = current_time
                self._intensity = 1.0
                self._beat_count += 1

        return self._beat_detected

    def _calculate_weighted_energy(self, spectrum: np.ndarray) -> float:
        """Calculate weighted energy from full spectrum.

        Args:
            spectrum: FFT magnitude spectrum

        Returns:
            Weighted energy value
        """
        if len(spectrum) == 0:
            return 0.0

        # Assume spectrum covers 0 to Nyquist (sample_rate/2)
        # Split into bass/mids/highs based on typical frequency ranges
        num_bins = len(spectrum)

        # Approximate bin boundaries (assuming 44100 Hz sample rate, 2048 FFT)
        # These are rough approximations
        bass_end = max(1, num_bins // 8)  # ~0-2750 Hz
        mids_end = max(bass_end + 1, num_bins // 2)  # ~2750-11025 Hz

        bass = np.mean(spectrum[:bass_end]) if bass_end > 0 else 0
        mids = np.mean(spectrum[bass_end:mids_end]) if mids_end > bass_end else 0
        highs = np.mean(spectrum[mids_end:]) if num_bins > mids_end else 0

        # Store for history
        self._bass_history.append(bass)
        self._mids_history.append(mids)
        self._highs_history.append(highs)

        # Weighted sum
        energy = (
            bass * self.frequency_weights.get("bass", 1.0)
            + mids * self.frequency_weights.get("mids", 1.0)
            + highs * self.frequency_weights.get("highs", 1.0)
        )

        return energy

    def update_from_analyzer(self, analyzer) -> bool:
        """Update using an AudioAnalyzer instance.

        Convenience method that extracts relevant data from an analyzer.

        Args:
            analyzer: AudioAnalyzer instance

        Returns:
            True if a beat was detected
        """
        return self.update(
            bass=analyzer.get_bass(),
            mids=analyzer.get_mids(),
            highs=analyzer.get_highs(),
        )

    def get_intensity(self) -> float:
        """Get current beat intensity (0.0 - 1.0).

        This value peaks at 1.0 when a beat is detected and
        smoothly decays toward 0.0 over time.
        """
        return self._intensity

    def get_band_intensities(self) -> dict:
        """Get current intensity levels for each frequency band.

        Returns:
            Dict with 'bass', 'mids', 'highs' keys and float values.
        """
        return {
            "bass": list(self._bass_history)[-1] if self._bass_history else 0.0,
            "mids": list(self._mids_history)[-1] if self._mids_history else 0.0,
            "highs": list(self._highs_history)[-1] if self._highs_history else 0.0,
        }

    def reset(self) -> None:
        """Reset all detector state."""
        self._energy_history.clear()
        self._bass_history.clear()
        self._mids_history.clear()
        self._highs_history.clear()
        self._beat_detected = False
        self._last_beat_time = 0.0
        self._intensity = 0.0
        self._current_energy = 0.0
        self._average_energy = 0.0
        self._threshold = 0.0
        self._beat_count = 0


class MultibandBeatDetector:
    """Beat detection with separate detectors for each frequency band.

    Useful for more complex visualizations that react differently
    to bass, mids, and highs.
    """

    def __init__(
        self,
        bass_sensitivity: float = 1.3,
        mids_sensitivity: float = 1.5,
        highs_sensitivity: float = 1.8,
        decay: float = 0.95,
        history_size: int = 43,
        min_interval: float = 0.08,
    ):
        """Initialize multiband beat detector.

        Args:
            bass_sensitivity: Sensitivity for bass detector
            mids_sensitivity: Sensitivity for mids detector
            highs_sensitivity: Sensitivity for highs detector
            decay: Intensity decay factor
            history_size: History size for baseline
            min_interval: Minimum interval between beats
        """
        self.bass_detector = BeatDetector(
            sensitivity=bass_sensitivity,
            decay=decay,
            history_size=history_size,
            min_interval=min_interval,
        )
        self.mids_detector = BeatDetector(
            sensitivity=mids_sensitivity,
            decay=decay,
            history_size=history_size,
            min_interval=min_interval,
        )
        self.highs_detector = BeatDetector(
            sensitivity=highs_sensitivity,
            decay=decay,
            history_size=history_size,
            min_interval=min_interval,
        )

    def update(
        self,
        bass: float,
        mids: float,
        highs: float,
    ) -> dict:
        """Update all band detectors.

        Args:
            bass: Bass level
            mids: Mids level
            highs: Highs level

        Returns:
            Dict with beat detection results for each band
        """
        return {
            "bass": self.bass_detector.update(rms=bass),
            "mids": self.mids_detector.update(rms=mids),
            "highs": self.highs_detector.update(rms=highs),
        }

    def update_from_analyzer(self, analyzer) -> dict:
        """Update using an AudioAnalyzer instance.

        Args:
            analyzer: AudioAnalyzer instance

        Returns:
            Dict with beat detection results for each band
        """
        return self.update(
            bass=analyzer.get_bass(),
            mids=analyzer.get_mids(),
            highs=analyzer.get_highs(),
        )

    @property
    def any_beat(self) -> bool:
        """True if any band detected a beat."""
        return (
            self.bass_detector.beat
            or self.mids_detector.beat
            or self.highs_detector.beat
        )

    @property
    def bass_beat(self) -> bool:
        """True if bass beat was detected."""
        return self.bass_detector.beat

    @property
    def mids_beat(self) -> bool:
        """True if mids beat was detected."""
        return self.mids_detector.beat

    @property
    def highs_beat(self) -> bool:
        """True if highs beat was detected."""
        return self.highs_detector.beat

    @property
    def bass_intensity(self) -> float:
        """Bass beat intensity (0.0 - 1.0)."""
        return self.bass_detector.intensity

    @property
    def mids_intensity(self) -> float:
        """Mids beat intensity (0.0 - 1.0)."""
        return self.mids_detector.intensity

    @property
    def highs_intensity(self) -> float:
        """Highs beat intensity (0.0 - 1.0)."""
        return self.highs_detector.intensity

    def get_intensities(self) -> dict:
        """Get all band intensities.

        Returns:
            Dict with 'bass', 'mids', 'highs' intensity values
        """
        return {
            "bass": self.bass_intensity,
            "mids": self.mids_intensity,
            "highs": self.highs_intensity,
        }

    def reset(self) -> None:
        """Reset all detectors."""
        self.bass_detector.reset()
        self.mids_detector.reset()
        self.highs_detector.reset()
