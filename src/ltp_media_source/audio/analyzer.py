"""Real-time audio analysis for visualization.

Provides FFT-based spectrum analysis, volume metering, and frequency band
extraction for driving audio-reactive LED visualizations.
"""

import numpy as np
from typing import Tuple


class AudioAnalyzer:
    """Real-time audio analysis for visualization.

    Performs FFT analysis on audio samples and provides various metrics
    useful for driving visualizations:
    - Full spectrum magnitude
    - Aggregated frequency bands
    - RMS and peak volume levels
    - Frequency range extraction

    All analysis results are updated when analyze() is called.
    """

    def __init__(
        self,
        sample_rate: int = 44100,
        fft_size: int = 2048,
        smoothing: float = 0.3,
        gain: float = 1.0,
    ):
        """Initialize the audio analyzer.

        Args:
            sample_rate: Audio sample rate in Hz
            fft_size: FFT window size (should be power of 2)
            smoothing: Temporal smoothing factor (0.0 = no smoothing, 1.0 = frozen)
            gain: Input gain multiplier
        """
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.smoothing = max(0.0, min(1.0, smoothing))
        self.gain = max(0.0, gain)

        # Derived values
        self._num_bins = fft_size // 2  # Only positive frequencies
        self._freq_resolution = sample_rate / fft_size  # Hz per bin

        # Pre-compute window function (Hann window for smooth FFT)
        self._window = np.hanning(fft_size).astype(np.float32)

        # Pre-compute frequency array for each bin
        self._frequencies = np.fft.rfftfreq(fft_size, 1.0 / sample_rate)

        # Analysis results (updated by analyze())
        self._spectrum = np.zeros(self._num_bins + 1, dtype=np.float32)
        self._smoothed_spectrum = np.zeros(self._num_bins + 1, dtype=np.float32)
        self._waveform = np.zeros(fft_size, dtype=np.float32)
        self._rms = 0.0
        self._peak = 0.0
        self._smoothed_rms = 0.0
        self._smoothed_peak = 0.0

        # Band cache (computed on demand)
        self._band_cache: dict[int, np.ndarray] = {}
        self._band_edges_cache: dict[int, np.ndarray] = {}

    @property
    def spectrum(self) -> np.ndarray:
        """Current FFT magnitude spectrum (smoothed)."""
        return self._smoothed_spectrum

    @property
    def spectrum_raw(self) -> np.ndarray:
        """Current FFT magnitude spectrum (unsmoothed)."""
        return self._spectrum

    @property
    def waveform(self) -> np.ndarray:
        """Most recent audio samples analyzed."""
        return self._waveform

    @property
    def rms(self) -> float:
        """RMS (root mean square) volume level (0.0 - 1.0+)."""
        return self._smoothed_rms

    @property
    def rms_raw(self) -> float:
        """Unsmoothed RMS volume level."""
        return self._rms

    @property
    def peak(self) -> float:
        """Peak sample level (0.0 - 1.0+)."""
        return self._smoothed_peak

    @property
    def peak_raw(self) -> float:
        """Unsmoothed peak level."""
        return self._peak

    @property
    def num_bins(self) -> int:
        """Number of frequency bins in spectrum."""
        return self._num_bins + 1

    @property
    def frequencies(self) -> np.ndarray:
        """Frequency (Hz) for each spectrum bin."""
        return self._frequencies

    def analyze(self, samples: np.ndarray) -> None:
        """Analyze audio samples.

        Updates all analysis results (spectrum, waveform, rms, peak).

        Args:
            samples: Audio samples, shape (num_samples,) or (num_samples, channels).
                    If stereo, channels are averaged to mono.
                    Values should be float32 in range [-1.0, 1.0].
        """
        # Convert to mono if needed
        if samples.ndim == 2:
            samples = samples.mean(axis=1)

        # Apply gain
        if self.gain != 1.0:
            samples = samples * self.gain

        # Handle size mismatch
        if len(samples) < self.fft_size:
            # Pad with zeros
            padded = np.zeros(self.fft_size, dtype=np.float32)
            padded[: len(samples)] = samples
            samples = padded
        elif len(samples) > self.fft_size:
            # Take the most recent samples
            samples = samples[-self.fft_size :]

        # Store waveform
        self._waveform = samples.astype(np.float32)

        # Calculate volume levels
        self._rms = float(np.sqrt(np.mean(samples**2)))
        self._peak = float(np.max(np.abs(samples)))

        # Apply smoothing to volume levels
        self._smoothed_rms = self._smooth(self._smoothed_rms, self._rms)
        self._smoothed_peak = self._smooth(self._smoothed_peak, self._peak)

        # Apply window and compute FFT
        windowed = samples * self._window
        fft_result = np.fft.rfft(windowed)

        # Get magnitude spectrum (normalized)
        # Divide by fft_size to normalize, multiply by 2 for one-sided spectrum
        magnitude = np.abs(fft_result) * (2.0 / self.fft_size)
        self._spectrum = magnitude.astype(np.float32)

        # Apply smoothing to spectrum
        self._smoothed_spectrum = self._smooth_array(
            self._smoothed_spectrum, self._spectrum
        )

        # Invalidate band cache
        self._band_cache.clear()

    def _smooth(self, old_value: float, new_value: float) -> float:
        """Apply temporal smoothing to a single value."""
        return old_value * self.smoothing + new_value * (1.0 - self.smoothing)

    def _smooth_array(self, old_array: np.ndarray, new_array: np.ndarray) -> np.ndarray:
        """Apply temporal smoothing to an array."""
        return old_array * self.smoothing + new_array * (1.0 - self.smoothing)

    def get_spectrum_bands(
        self,
        num_bands: int,
        log_scale: bool = True,
        min_freq: float = 20.0,
        max_freq: float = 20000.0,
    ) -> np.ndarray:
        """Get spectrum aggregated into frequency bands.

        Args:
            num_bands: Number of bands to return
            log_scale: If True, bands are logarithmically spaced (better for music).
                      If False, bands are linearly spaced.
            min_freq: Minimum frequency (Hz)
            max_freq: Maximum frequency (Hz)

        Returns:
            Array of band magnitudes, shape (num_bands,)
        """
        # Clamp frequency range
        min_freq = max(min_freq, self._freq_resolution)
        max_freq = min(max_freq, self.sample_rate / 2)

        # Check cache
        cache_key = (num_bands, log_scale, min_freq, max_freq)
        if cache_key in self._band_edges_cache:
            band_edges = self._band_edges_cache[cache_key]
        else:
            # Compute band edges
            if log_scale:
                # Logarithmic spacing (better for music perception)
                band_edges = np.logspace(
                    np.log10(min_freq),
                    np.log10(max_freq),
                    num_bands + 1,
                )
            else:
                # Linear spacing
                band_edges = np.linspace(min_freq, max_freq, num_bands + 1)

            self._band_edges_cache[cache_key] = band_edges

        # Aggregate spectrum into bands
        bands = np.zeros(num_bands, dtype=np.float32)

        for i in range(num_bands):
            low_freq = band_edges[i]
            high_freq = band_edges[i + 1]

            # Find bin indices for this frequency range
            low_bin = int(low_freq / self._freq_resolution)
            high_bin = int(high_freq / self._freq_resolution) + 1

            # Clamp to valid range
            low_bin = max(0, min(low_bin, len(self._smoothed_spectrum) - 1))
            high_bin = max(low_bin + 1, min(high_bin, len(self._smoothed_spectrum)))

            # Average magnitude in this range
            if high_bin > low_bin:
                bands[i] = np.mean(self._smoothed_spectrum[low_bin:high_bin])

        return bands

    def get_frequency_range(
        self,
        low_hz: float,
        high_hz: float,
    ) -> np.ndarray:
        """Get spectrum bins within a frequency range.

        Args:
            low_hz: Lower frequency bound (Hz)
            high_hz: Upper frequency bound (Hz)

        Returns:
            Spectrum magnitudes for the specified range
        """
        low_bin = int(low_hz / self._freq_resolution)
        high_bin = int(high_hz / self._freq_resolution) + 1

        low_bin = max(0, min(low_bin, len(self._smoothed_spectrum) - 1))
        high_bin = max(low_bin + 1, min(high_bin, len(self._smoothed_spectrum)))

        return self._smoothed_spectrum[low_bin:high_bin].copy()

    def get_bass(self) -> float:
        """Get average bass level (20-250 Hz)."""
        bass_range = self.get_frequency_range(20, 250)
        return float(np.mean(bass_range)) if len(bass_range) > 0 else 0.0

    def get_mids(self) -> float:
        """Get average mid-range level (250-4000 Hz)."""
        mids_range = self.get_frequency_range(250, 4000)
        return float(np.mean(mids_range)) if len(mids_range) > 0 else 0.0

    def get_highs(self) -> float:
        """Get average high frequency level (4000-20000 Hz)."""
        highs_range = self.get_frequency_range(4000, 20000)
        return float(np.mean(highs_range)) if len(highs_range) > 0 else 0.0

    def get_bass_mids_highs(self) -> Tuple[float, float, float]:
        """Get bass, mids, and highs as a tuple."""
        return (self.get_bass(), self.get_mids(), self.get_highs())

    def bin_to_frequency(self, bin_index: int) -> float:
        """Convert a bin index to its center frequency in Hz."""
        return bin_index * self._freq_resolution

    def frequency_to_bin(self, frequency: float) -> int:
        """Convert a frequency to its nearest bin index."""
        return int(frequency / self._freq_resolution)

    def reset(self) -> None:
        """Reset all analysis state."""
        self._spectrum.fill(0)
        self._smoothed_spectrum.fill(0)
        self._waveform.fill(0)
        self._rms = 0.0
        self._peak = 0.0
        self._smoothed_rms = 0.0
        self._smoothed_peak = 0.0
        self._band_cache.clear()
