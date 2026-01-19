"""Linear (1D) audio visualizations for LED strips.

These visualizers output single-row frames suitable for linear LED strips.
"""

from typing import TYPE_CHECKING, Optional, Tuple
import time

import numpy as np

from ltp_media_source.visualizers.base import LinearVisualizer
from ltp_media_source.visualizers.colors import (
    ColorMode,
    get_color_for_mode,
    get_gradient_color,
    PALETTE_FIRE,
    generate_rainbow_colors,
)

if TYPE_CHECKING:
    from ltp_media_source.audio.analyzer import AudioAnalyzer
    from ltp_media_source.audio.beat_detector import BeatDetector


class SpectrumBarsLinear(LinearVisualizer):
    """Frequency spectrum as colored bars.

    Each pixel represents a frequency band, with brightness
    proportional to the band's magnitude.
    """

    def __init__(
        self,
        width: int,
        log_scale: bool = True,
        **kwargs,
    ):
        """Initialize spectrum bars visualizer.

        Args:
            width: Number of pixels (= number of bands)
            log_scale: Use logarithmic frequency spacing
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, **kwargs)
        self.log_scale = log_scale
        self._smoothed_bands: Optional[np.ndarray] = None

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render spectrum bars."""
        frame = self._create_frame()

        # Get spectrum bands
        bands = analyzer.get_spectrum_bands(
            self.width,
            log_scale=self.log_scale,
            min_freq=self.min_freq,
            max_freq=self.max_freq,
        )

        # Apply gain
        bands = self._apply_gain(bands)

        # Apply smoothing
        if self._smoothed_bands is None:
            self._smoothed_bands = bands.copy()
        else:
            self._smoothed_bands = (
                self._smoothed_bands * self.smoothing
                + bands * (1 - self.smoothing)
            )

        # Normalize
        max_val = max(np.max(self._smoothed_bands), 0.001)
        normalized = np.clip(self._smoothed_bands / max_val, 0, 1)

        # Render each band
        for i in range(self.width):
            intensity = normalized[i]
            color = get_color_for_mode(
                self.color_mode,
                position=i / self.width,
                intensity=intensity,
                base_color=self.base_color,
            )
            frame[0, i] = color

        # Beat flash
        if beat_detector and beat_detector.intensity > 0:
            flash = int(beat_detector.intensity * 50)
            frame = np.clip(frame.astype(np.int16) + flash, 0, 255).astype(np.uint8)

        return frame


class VUMeterLinear(LinearVisualizer):
    """Volume meter with peak hold.

    Displays current volume level as a filled bar with a peak indicator
    that slowly falls.
    """

    def __init__(
        self,
        width: int,
        peak_hold_time: float = 1.0,
        peak_decay: float = 0.95,
        use_rms: bool = True,
        **kwargs,
    ):
        """Initialize VU meter.

        Args:
            width: Number of pixels
            peak_hold_time: Time to hold peak indicator (seconds)
            peak_decay: Peak decay rate after hold time
            use_rms: Use RMS (True) or peak (False) for level
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, **kwargs)
        self.peak_hold_time = peak_hold_time
        self.peak_decay = peak_decay
        self.use_rms = use_rms

        self._current_level = 0.0
        self._peak_level = 0.0
        self._peak_time = 0.0

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render VU meter."""
        frame = self._create_frame()

        # Get level
        level = analyzer.rms if self.use_rms else analyzer.peak
        level = min(level * self.gain, 1.0)

        # Smooth level
        self._current_level = (
            self._current_level * self.smoothing
            + level * (1 - self.smoothing)
        )

        # Update peak
        current_time = time.time()
        if level > self._peak_level:
            self._peak_level = level
            self._peak_time = current_time
        elif current_time - self._peak_time > self.peak_hold_time:
            self._peak_level *= self.peak_decay

        # Calculate bar lengths
        bar_length = int(self._current_level * self.width)
        peak_pos = int(self._peak_level * (self.width - 1))

        # Draw bar
        for i in range(self.width):
            if i < bar_length:
                # Main bar - gradient from green to red
                t = i / self.width
                if t < 0.6:
                    color = (0, 255, 0)  # Green
                elif t < 0.8:
                    color = (255, 255, 0)  # Yellow
                else:
                    color = (255, 0, 0)  # Red
                frame[0, i] = color

        # Draw peak indicator
        if peak_pos < self.width:
            frame[0, peak_pos] = (255, 255, 255)  # White peak

        return frame


class WaveformLinear(LinearVisualizer):
    """Audio waveform display.

    Shows the actual audio waveform mapped to pixel brightness.
    """

    def __init__(
        self,
        width: int,
        center_line: bool = True,
        color: Tuple[int, int, int] = (0, 255, 0),
        **kwargs,
    ):
        """Initialize waveform visualizer.

        Args:
            width: Number of pixels
            center_line: If True, waveform is centered; if False, absolute value
            color: Waveform color
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, **kwargs)
        self.center_line = center_line
        self.waveform_color = color

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render waveform."""
        frame = self._create_frame()

        # Get waveform samples
        waveform = analyzer.waveform

        # Resample to match width
        if len(waveform) > self.width:
            indices = np.linspace(0, len(waveform) - 1, self.width, dtype=int)
            samples = waveform[indices]
        else:
            samples = np.zeros(self.width)
            samples[:len(waveform)] = waveform

        # Apply gain
        samples = samples * self.gain

        # Map to brightness
        if self.center_line:
            # Signed: map -1..1 to brightness
            brightness = np.abs(samples)
        else:
            # Absolute value
            brightness = np.abs(samples)

        brightness = np.clip(brightness, 0, 1)

        # Render
        for i in range(self.width):
            intensity = brightness[i]
            r = int(self.waveform_color[0] * intensity)
            g = int(self.waveform_color[1] * intensity)
            b = int(self.waveform_color[2] * intensity)
            frame[0, i] = (r, g, b)

        return frame


class BeatPulseLinear(LinearVisualizer):
    """Flash/fade on beat detection.

    Entire strip flashes on beat and fades out.
    """

    def __init__(
        self,
        width: int,
        color: Tuple[int, int, int] = (255, 255, 255),
        fade_speed: float = 0.9,
        **kwargs,
    ):
        """Initialize beat pulse visualizer.

        Args:
            width: Number of pixels
            color: Flash color
            fade_speed: How quickly the flash fades (0-1, higher = slower)
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, **kwargs)
        self.pulse_color = color
        self.fade_speed = max(0.0, min(0.99, fade_speed))
        self._brightness = 0.0

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render beat pulse."""
        frame = self._create_frame()

        # Check for beat
        if beat_detector and beat_detector.beat:
            self._brightness = 1.0
        else:
            self._brightness *= self.fade_speed

        # Fill with color at current brightness
        if self._brightness > 0.01:
            r = int(self.pulse_color[0] * self._brightness)
            g = int(self.pulse_color[1] * self._brightness)
            b = int(self.pulse_color[2] * self._brightness)
            frame[0, :] = (r, g, b)

        return frame


class BassFillLinear(LinearVisualizer):
    """Low frequency fills from center outward.

    Bass intensity causes the strip to fill from center outward.
    """

    def __init__(
        self,
        width: int,
        threshold: float = 0.1,
        color: Tuple[int, int, int] = (255, 0, 0),
        **kwargs,
    ):
        """Initialize bass fill visualizer.

        Args:
            width: Number of pixels
            threshold: Minimum bass level to show
            color: Fill color
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, **kwargs)
        self.threshold = threshold
        self.fill_color = color
        self._current_fill = 0.0

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render bass fill."""
        frame = self._create_frame()

        # Get bass level
        bass = analyzer.get_bass() * self.gain

        # Apply threshold
        if bass < self.threshold:
            bass = 0

        # Smooth
        self._current_fill = (
            self._current_fill * self.smoothing
            + bass * (1 - self.smoothing)
        )

        # Calculate fill extent (0-1 maps to center-to-edge)
        fill_amount = min(self._current_fill * 2, 1.0)  # Scale up
        half_width = self.width // 2
        fill_pixels = int(fill_amount * half_width)

        # Fill from center outward
        center = self.width // 2
        for i in range(fill_pixels):
            intensity = 1.0 - (i / max(half_width, 1)) * 0.5  # Fade toward edges
            r = int(self.fill_color[0] * intensity)
            g = int(self.fill_color[1] * intensity)
            b = int(self.fill_color[2] * intensity)

            # Left side
            left_idx = center - 1 - i
            if left_idx >= 0:
                frame[0, left_idx] = (r, g, b)

            # Right side
            right_idx = center + i
            if right_idx < self.width:
                frame[0, right_idx] = (r, g, b)

        return frame


class ChasingDotsLinear(LinearVisualizer):
    """Dots that move with the beat.

    Multiple dots chase around the strip, speeding up with beat intensity.
    """

    def __init__(
        self,
        width: int,
        num_dots: int = 3,
        base_speed: float = 0.5,
        beat_speed_mult: float = 3.0,
        dot_size: int = 2,
        **kwargs,
    ):
        """Initialize chasing dots visualizer.

        Args:
            width: Number of pixels
            num_dots: Number of dots
            base_speed: Base movement speed (pixels per frame)
            beat_speed_mult: Speed multiplier on beat
            dot_size: Size of each dot in pixels
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, **kwargs)
        self.num_dots = num_dots
        self.base_speed = base_speed
        self.beat_speed_mult = beat_speed_mult
        self.dot_size = dot_size

        # Initialize dot positions evenly spaced
        self._positions = np.linspace(0, width, num_dots, endpoint=False)
        self._colors = generate_rainbow_colors(num_dots)

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render chasing dots."""
        frame = self._create_frame()

        # Calculate speed
        speed = self.base_speed
        if beat_detector:
            speed += beat_detector.intensity * self.beat_speed_mult

        # Move dots
        self._positions = (self._positions + speed) % self.width

        # Draw dots
        for i, pos in enumerate(self._positions):
            color = self._colors[i]
            for j in range(self.dot_size):
                idx = int(pos + j) % self.width
                frame[0, idx] = color

        return frame


class LevelMeterLinear(LinearVisualizer):
    """Multi-band level meter.

    Shows bass, mids, and highs as separate colored sections.
    """

    def __init__(
        self,
        width: int,
        bass_color: Tuple[int, int, int] = (255, 0, 0),
        mids_color: Tuple[int, int, int] = (0, 255, 0),
        highs_color: Tuple[int, int, int] = (0, 0, 255),
        **kwargs,
    ):
        """Initialize level meter.

        Args:
            width: Number of pixels (should be divisible by 3)
            bass_color: Color for bass section
            mids_color: Color for mids section
            highs_color: Color for highs section
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, **kwargs)
        self.bass_color = bass_color
        self.mids_color = mids_color
        self.highs_color = highs_color

        self._smoothed_levels = np.zeros(3)

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render level meter."""
        frame = self._create_frame()

        # Get levels
        bass = analyzer.get_bass() * self.gain
        mids = analyzer.get_mids() * self.gain
        highs = analyzer.get_highs() * self.gain

        levels = np.array([bass, mids, highs])

        # Smooth
        self._smoothed_levels = (
            self._smoothed_levels * self.smoothing
            + levels * (1 - self.smoothing)
        )

        # Normalize
        max_level = max(np.max(self._smoothed_levels), 0.001)
        normalized = np.clip(self._smoothed_levels / max_level, 0, 1)

        # Calculate section sizes
        section_width = self.width // 3
        colors = [self.bass_color, self.mids_color, self.highs_color]

        for section in range(3):
            level = normalized[section]
            color = colors[section]
            fill_pixels = int(level * section_width)

            start = section * section_width
            for i in range(fill_pixels):
                idx = start + i
                if idx < self.width:
                    frame[0, idx] = color

        return frame
