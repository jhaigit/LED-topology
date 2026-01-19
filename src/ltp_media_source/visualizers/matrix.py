"""Matrix (2D) audio visualizations for LED matrices.

These visualizers output 2D frames suitable for LED matrix displays.
"""

from typing import TYPE_CHECKING, Optional, Tuple
import time

import numpy as np

from ltp_media_source.visualizers.base import MatrixVisualizer
from ltp_media_source.visualizers.colors import (
    ColorMode,
    get_color_for_mode,
    get_gradient_color,
    PALETTE_FIRE,
    PALETTE_ICE,
    generate_rainbow_colors,
    hsv_to_rgb,
)

if TYPE_CHECKING:
    from ltp_media_source.audio.analyzer import AudioAnalyzer
    from ltp_media_source.audio.beat_detector import BeatDetector


class SpectrumBarsMatrix(MatrixVisualizer):
    """Frequency spectrum as vertical bars from bottom.

    Each column represents a frequency band, with bar height
    proportional to the band's magnitude.
    """

    def __init__(
        self,
        width: int,
        height: int,
        log_scale: bool = True,
        mirror: bool = False,
        **kwargs,
    ):
        """Initialize spectrum bars matrix.

        Args:
            width: Matrix width (number of bands)
            height: Matrix height (bar max height)
            log_scale: Use logarithmic frequency spacing
            mirror: Mirror bars from center
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, height=height, **kwargs)
        self.log_scale = log_scale
        self.mirror = mirror
        self._smoothed_bands: Optional[np.ndarray] = None

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render spectrum bars matrix."""
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

        # Render bars
        for x in range(self.width):
            bar_height = int(normalized[x] * self.height)
            color = get_color_for_mode(
                self.color_mode,
                position=x / self.width,
                intensity=1.0,
                base_color=self.base_color,
            )

            for y in range(bar_height):
                # Draw from bottom up
                frame[self.height - 1 - y, x] = color

        # Beat flash
        if beat_detector and beat_detector.intensity > 0:
            flash = int(beat_detector.intensity * 40)
            frame = np.clip(frame.astype(np.int16) + flash, 0, 255).astype(np.uint8)

        return frame


class SpectrogramMatrix(MatrixVisualizer):
    """Scrolling spectrogram (spectrum history).

    Shows spectrum over time, scrolling in the specified direction.
    """

    def __init__(
        self,
        width: int,
        height: int,
        scroll_direction: str = "left",
        **kwargs,
    ):
        """Initialize spectrogram.

        Args:
            width: Matrix width
            height: Matrix height (number of frequency bands)
            scroll_direction: Direction to scroll ('left', 'right', 'up', 'down')
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, height=height, **kwargs)
        self.scroll_direction = scroll_direction

        # History buffer
        if scroll_direction in ("left", "right"):
            self._history = np.zeros((height, width), dtype=np.float32)
        else:
            self._history = np.zeros((height, width), dtype=np.float32)

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render spectrogram."""
        frame = self._create_frame()

        # Get spectrum bands
        if self.scroll_direction in ("left", "right"):
            num_bands = self.height
        else:
            num_bands = self.width

        bands = analyzer.get_spectrum_bands(
            num_bands,
            log_scale=True,
            min_freq=self.min_freq,
            max_freq=self.max_freq,
        )

        # Apply gain and normalize
        bands = self._apply_gain(bands)
        max_val = max(np.max(bands), 0.001)
        normalized = np.clip(bands / max_val, 0, 1)

        # Scroll and add new column/row
        if self.scroll_direction == "left":
            self._history = np.roll(self._history, -1, axis=1)
            self._history[:, -1] = normalized
        elif self.scroll_direction == "right":
            self._history = np.roll(self._history, 1, axis=1)
            self._history[:, 0] = normalized
        elif self.scroll_direction == "up":
            self._history = np.roll(self._history, -1, axis=0)
            self._history[-1, :] = normalized
        else:  # down
            self._history = np.roll(self._history, 1, axis=0)
            self._history[0, :] = normalized

        # Render to frame
        for y in range(self.height):
            for x in range(self.width):
                intensity = self._history[y, x]
                if self.scroll_direction in ("left", "right"):
                    freq_pos = y / self.height
                else:
                    freq_pos = x / self.width

                color = get_color_for_mode(
                    self.color_mode,
                    position=freq_pos,
                    intensity=intensity,
                    base_color=self.base_color,
                )
                frame[y, x] = color

        return frame


class BeatRipplesMatrix(MatrixVisualizer):
    """Ripples emanate from center on beat.

    Circular ripples expand outward when a beat is detected.
    """

    def __init__(
        self,
        width: int,
        height: int,
        ripple_speed: float = 1.5,
        ripple_width: float = 2.0,
        max_ripples: int = 5,
        **kwargs,
    ):
        """Initialize beat ripples.

        Args:
            width: Matrix width
            height: Matrix height
            ripple_speed: How fast ripples expand
            ripple_width: Width of each ripple band
            max_ripples: Maximum concurrent ripples
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, height=height, **kwargs)
        self.ripple_speed = ripple_speed
        self.ripple_width = ripple_width
        self.max_ripples = max_ripples

        # Active ripples: list of (radius, intensity, color)
        self._ripples: list = []
        self._max_radius = self._max_distance() * 1.5

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render beat ripples."""
        frame = self._create_frame()

        # Check for new beat
        if beat_detector and beat_detector.beat:
            # Add new ripple with random color from rainbow
            hue = np.random.randint(0, 360)
            color = hsv_to_rgb(hue, 1.0, 1.0)
            self._ripples.append([0.0, 1.0, color])

            # Limit ripples
            if len(self._ripples) > self.max_ripples:
                self._ripples.pop(0)

        # Update and render ripples
        cx, cy = self.center
        new_ripples = []

        for ripple in self._ripples:
            radius, intensity, color = ripple

            # Expand ripple
            radius += self.ripple_speed
            intensity *= 0.97  # Fade

            if radius < self._max_radius and intensity > 0.05:
                new_ripples.append([radius, intensity, color])

                # Draw ripple
                for y in range(self.height):
                    for x in range(self.width):
                        dist = self._distance_from_center(x, y)
                        # Check if pixel is on the ripple ring
                        diff = abs(dist - radius)
                        if diff < self.ripple_width:
                            ring_intensity = (1 - diff / self.ripple_width) * intensity
                            r = int(color[0] * ring_intensity)
                            g = int(color[1] * ring_intensity)
                            b = int(color[2] * ring_intensity)
                            # Additive blend
                            frame[y, x] = np.clip(
                                frame[y, x].astype(np.int16) + [r, g, b],
                                0, 255
                            ).astype(np.uint8)

        self._ripples = new_ripples
        return frame


class FrequencyHeatmapMatrix(MatrixVisualizer):
    """2D frequency intensity heatmap.

    Maps frequency bands to rows and shows intensity as color.
    """

    def __init__(
        self,
        width: int,
        height: int,
        history_length: int = 0,
        **kwargs,
    ):
        """Initialize frequency heatmap.

        Args:
            width: Matrix width
            height: Matrix height (number of frequency bands)
            history_length: If > 0, show temporal history
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, height=height, **kwargs)
        self.history_length = history_length

        if history_length > 0:
            self._history = np.zeros((height, history_length), dtype=np.float32)
        else:
            self._history = None

        self._smoothed_bands: Optional[np.ndarray] = None

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render frequency heatmap."""
        frame = self._create_frame()

        # Get spectrum bands
        bands = analyzer.get_spectrum_bands(
            self.height,
            log_scale=True,
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

        if self._history is not None:
            # Temporal mode: scroll history
            self._history = np.roll(self._history, -1, axis=1)
            self._history[:, -1] = normalized

            for y in range(self.height):
                for x in range(self.width):
                    hist_x = int(x / self.width * self.history_length)
                    intensity = self._history[y, hist_x]
                    color = get_color_for_mode(
                        self.color_mode,
                        position=y / self.height,
                        intensity=intensity,
                        base_color=self.base_color,
                    )
                    frame[self.height - 1 - y, x] = color
        else:
            # Static mode: stretch bands across width
            for y in range(self.height):
                intensity = normalized[y]
                color = get_color_for_mode(
                    self.color_mode,
                    position=y / self.height,
                    intensity=intensity,
                    base_color=self.base_color,
                )
                # Fill entire row
                frame[self.height - 1 - y, :] = color

        return frame


class VUMeterBarMatrix(MatrixVisualizer):
    """VU meter as horizontal or vertical bar.

    Shows volume level with peak hold indicator.
    """

    def __init__(
        self,
        width: int,
        height: int,
        orientation: str = "horizontal",
        peak_hold_time: float = 1.0,
        **kwargs,
    ):
        """Initialize VU meter bar.

        Args:
            width: Matrix width
            height: Matrix height
            orientation: 'horizontal' or 'vertical'
            peak_hold_time: Time to hold peak indicator
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, height=height, **kwargs)
        self.orientation = orientation
        self.peak_hold_time = peak_hold_time

        self._current_level = 0.0
        self._peak_level = 0.0
        self._peak_time = 0.0

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render VU meter bar."""
        frame = self._create_frame()

        # Get level
        level = analyzer.rms * self.gain
        level = min(level, 1.0)

        # Smooth
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
            self._peak_level *= 0.95

        if self.orientation == "horizontal":
            bar_length = int(self._current_level * self.width)
            peak_pos = int(self._peak_level * (self.width - 1))

            for x in range(self.width):
                if x < bar_length:
                    t = x / self.width
                    color = get_color_for_mode(
                        ColorMode.FIRE,
                        position=t,
                        intensity=1.0,
                    )
                    frame[:, x] = color

            # Peak indicator
            if peak_pos < self.width:
                frame[:, peak_pos] = (255, 255, 255)
        else:
            # Vertical
            bar_height = int(self._current_level * self.height)
            peak_pos = int(self._peak_level * (self.height - 1))

            for y in range(self.height):
                row_from_bottom = self.height - 1 - y
                if row_from_bottom < bar_height:
                    t = row_from_bottom / self.height
                    color = get_color_for_mode(
                        ColorMode.FIRE,
                        position=t,
                        intensity=1.0,
                    )
                    frame[y, :] = color

            # Peak indicator
            peak_row = self.height - 1 - peak_pos
            if 0 <= peak_row < self.height:
                frame[peak_row, :] = (255, 255, 255)

        return frame


class WaveformScopeMatrix(MatrixVisualizer):
    """Oscilloscope-style waveform display.

    Shows the audio waveform as a trace across the matrix.
    """

    def __init__(
        self,
        width: int,
        height: int,
        persistence: float = 0.0,
        color: Tuple[int, int, int] = (0, 255, 0),
        **kwargs,
    ):
        """Initialize waveform scope.

        Args:
            width: Matrix width
            height: Matrix height
            persistence: Trail persistence (0-1, 0=no trail)
            color: Waveform color
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, height=height, **kwargs)
        self.persistence = persistence
        self.waveform_color = color

        self._persistent_frame: Optional[np.ndarray] = None

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render waveform scope."""
        # Start with persistent frame or black
        if self.persistence > 0 and self._persistent_frame is not None:
            frame = (self._persistent_frame * self.persistence).astype(np.uint8)
        else:
            frame = self._create_frame()

        # Get waveform
        waveform = analyzer.waveform

        # Resample to width
        if len(waveform) > self.width:
            indices = np.linspace(0, len(waveform) - 1, self.width, dtype=int)
            samples = waveform[indices]
        else:
            samples = np.zeros(self.width)
            samples[:len(waveform)] = waveform

        # Apply gain and map to y positions
        samples = samples * self.gain
        samples = np.clip(samples, -1, 1)

        # Map -1..1 to 0..height-1
        y_positions = ((1 - samples) / 2 * (self.height - 1)).astype(int)
        y_positions = np.clip(y_positions, 0, self.height - 1)

        # Draw waveform
        for x in range(self.width):
            y = y_positions[x]
            frame[y, x] = self.waveform_color

            # Draw vertical line to previous point for continuity
            if x > 0:
                prev_y = y_positions[x - 1]
                y_min, y_max = min(y, prev_y), max(y, prev_y)
                for fill_y in range(y_min, y_max + 1):
                    frame[fill_y, x] = self.waveform_color

        # Store for persistence
        if self.persistence > 0:
            self._persistent_frame = frame.astype(np.float32)

        return frame


class PlasmaMatrix(MatrixVisualizer):
    """Plasma effect modulated by audio.

    Animated plasma pattern that reacts to audio levels.
    """

    def __init__(
        self,
        width: int,
        height: int,
        speed: float = 0.1,
        **kwargs,
    ):
        """Initialize plasma effect.

        Args:
            width: Matrix width
            height: Matrix height
            speed: Animation speed
            **kwargs: Additional visualizer parameters
        """
        super().__init__(width=width, height=height, **kwargs)
        self.speed = speed
        self._time = 0.0

    def render(
        self,
        analyzer: "AudioAnalyzer",
        beat_detector: Optional["BeatDetector"] = None,
    ) -> np.ndarray:
        """Render plasma effect."""
        frame = self._create_frame()

        # Get audio modulation
        bass = analyzer.get_bass() * self.gain
        mids = analyzer.get_mids() * self.gain

        # Update time
        self._time += self.speed
        if beat_detector and beat_detector.beat:
            self._time += 0.5  # Jump on beat

        # Generate plasma
        for y in range(self.height):
            for x in range(self.width):
                # Plasma function
                v1 = np.sin(x * 0.3 + self._time)
                v2 = np.sin((x * 0.2 + y * 0.2 + self._time) * 0.5)
                v3 = np.sin(np.sqrt((x - self.width/2)**2 + (y - self.height/2)**2) * 0.3 + self._time)

                # Combine with audio modulation
                value = (v1 + v2 + v3) / 3.0 + bass * 0.5

                # Map to color
                hue = ((value + 1) / 2 * 360 + mids * 60) % 360
                saturation = 0.8 + bass * 0.2
                brightness = 0.5 + mids * 0.5

                frame[y, x] = hsv_to_rgb(hue, min(saturation, 1.0), min(brightness, 1.0))

        return frame
