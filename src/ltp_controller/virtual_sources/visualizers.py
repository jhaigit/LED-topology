"""Data visualizer virtual sources."""

from typing import Any

import numpy as np

from libltp import NumberControl, BooleanControl, EnumControl, ColorControl

from ltp_controller.palettes import palette_registry
from ltp_controller.virtual_sources.base import VirtualSource, VirtualSourceConfig


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


class BarGraph(VirtualSource):
    """Displays a scalar value as a filled bar."""

    source_type = "bar_graph"

    def __init__(self, config: VirtualSourceConfig | None = None):
        super().__init__(config)
        self._value = 0.0
        self._peak_value = 0.0
        self._peak_time = 0.0

    def _setup_controls(self) -> None:
        self._controls.register(
            EnumControl(
                id="direction",
                name="Direction",
                description="Bar fill direction",
                value="left_to_right",
                options=[
                    {"value": "left_to_right", "label": "Left to Right"},
                    {"value": "right_to_left", "label": "Right to Left"},
                    {"value": "center_out", "label": "Center Out"},
                    {"value": "edges_in", "label": "Edges In"},
                ],
                group="display",
            )
        )
        self._controls.register(
            EnumControl(
                id="color_mode",
                name="Color Mode",
                description="How to color the bar",
                value="solid",
                options=[
                    {"value": "solid", "label": "Solid"},
                    {"value": "gradient", "label": "Gradient"},
                    {"value": "threshold", "label": "Threshold"},
                ],
                group="display",
            )
        )
        self._controls.register(
            ColorControl(
                id="color",
                name="Color",
                description="Bar color (solid mode)",
                value="#00FF00",
                group="display",
            )
        )
        self._controls.register(
            ColorControl(
                id="gradient_low",
                name="Gradient Low",
                description="Low value color (gradient mode)",
                value="#00FF00",
                group="display",
            )
        )
        self._controls.register(
            ColorControl(
                id="gradient_high",
                name="Gradient High",
                description="High value color (gradient mode)",
                value="#FF0000",
                group="display",
            )
        )
        self._controls.register(
            ColorControl(
                id="background",
                name="Background",
                description="Background color",
                value="#000000",
                group="display",
            )
        )
        self._controls.register(
            BooleanControl(
                id="show_peak",
                name="Show Peak",
                description="Show peak hold indicator",
                value=False,
                group="display",
            )
        )
        self._controls.register(
            NumberControl(
                id="peak_hold_time",
                name="Peak Hold Time",
                description="Peak hold time in seconds",
                value=1.0,
                min=0.1,
                max=5.0,
                step=0.1,
                unit="s",
                group="display",
            )
        )

    def set_data(self, data: Any) -> None:
        """Set the bar value (0.0 to 1.0)."""
        if isinstance(data, dict):
            self._value = float(data.get("value", 0.0))
        else:
            self._value = float(data)
        self._value = max(0.0, min(1.0, self._value))

        # Update peak
        if self._value > self._peak_value:
            self._peak_value = self._value
            self._peak_time = 0.0

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        direction = self.get_control("direction")
        color_mode = self.get_control("color_mode")
        background = hex_to_rgb(self.get_control("background"))
        show_peak = self.get_control("show_peak")
        peak_hold_time = self.get_control("peak_hold_time")

        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        pixels[:] = background

        # Calculate fill amount
        fill_pixels = int(self._value * num_pixels)

        # Update peak decay
        if show_peak:
            self._peak_time += 1.0 / 30.0  # Approximate frame time
            if self._peak_time > peak_hold_time:
                self._peak_value = max(self._value, self._peak_value * 0.95)

        # Get color function based on mode
        def get_color(position: float) -> tuple[int, int, int]:
            if color_mode == "solid":
                return hex_to_rgb(self.get_control("color"))
            elif color_mode == "gradient":
                low = hex_to_rgb(self.get_control("gradient_low"))
                high = hex_to_rgb(self.get_control("gradient_high"))
                return tuple(int(low[i] + (high[i] - low[i]) * position) for i in range(3))
            elif color_mode == "threshold":
                if position < 0.7:
                    return (0, 255, 0)  # Green
                elif position < 0.9:
                    return (255, 255, 0)  # Yellow
                else:
                    return (255, 0, 0)  # Red
            return (255, 255, 255)

        # Fill based on direction
        if direction == "left_to_right":
            for i in range(fill_pixels):
                pixels[i] = get_color(i / max(1, num_pixels - 1))
            if show_peak:
                peak_pos = int(self._peak_value * (num_pixels - 1))
                if peak_pos < num_pixels:
                    pixels[peak_pos] = (255, 255, 255)

        elif direction == "right_to_left":
            for i in range(fill_pixels):
                pos = num_pixels - 1 - i
                pixels[pos] = get_color(i / max(1, num_pixels - 1))
            if show_peak:
                peak_pos = num_pixels - 1 - int(self._peak_value * (num_pixels - 1))
                if peak_pos >= 0:
                    pixels[peak_pos] = (255, 255, 255)

        elif direction == "center_out":
            center = num_pixels // 2
            half_fill = fill_pixels // 2
            for i in range(half_fill):
                t = i / max(1, center)
                color = get_color(t)
                if center + i < num_pixels:
                    pixels[center + i] = color
                if center - i >= 0:
                    pixels[center - i] = color

        elif direction == "edges_in":
            half_fill = fill_pixels // 2
            for i in range(half_fill):
                t = i / max(1, num_pixels // 2)
                color = get_color(t)
                pixels[i] = color
                pixels[num_pixels - 1 - i] = color

        return pixels


class MultiBar(VirtualSource):
    """Displays an array of values as multiple bar graphs."""

    source_type = "multi_bar"

    def __init__(self, config: VirtualSourceConfig | None = None):
        super().__init__(config)
        self._values: list[float] = []

    def _setup_controls(self) -> None:
        self._controls.register(
            NumberControl(
                id="bar_width",
                name="Bar Width",
                description="Pixels per bar (0 = auto-fit)",
                value=0,
                min=0,
                max=50,
                step=1,
                group="display",
            )
        )
        self._controls.register(
            NumberControl(
                id="bar_gap",
                name="Bar Gap",
                description="Pixels between bars",
                value=1,
                min=0,
                max=10,
                step=1,
                group="display",
            )
        )
        self._controls.register(
            EnumControl(
                id="direction",
                name="Direction",
                description="Bar fill direction",
                value="bottom_to_top",
                options=[
                    {"value": "bottom_to_top", "label": "Bottom to Top"},
                    {"value": "top_to_bottom", "label": "Top to Bottom"},
                ],
                group="display",
            )
        )
        self._controls.register(
            EnumControl(
                id="color_mode",
                name="Color Mode",
                description="How to color the bars",
                value="gradient",
                options=[
                    {"value": "solid", "label": "Solid"},
                    {"value": "gradient", "label": "Gradient"},
                    {"value": "per_bar", "label": "Per Bar (Rainbow)"},
                ],
                group="display",
            )
        )
        self._controls.register(
            ColorControl(
                id="color",
                name="Color",
                description="Bar color (solid mode)",
                value="#00FF00",
                group="display",
            )
        )
        self._controls.register(
            ColorControl(
                id="gradient_low",
                name="Gradient Low",
                description="Low value color",
                value="#00FF00",
                group="display",
            )
        )
        self._controls.register(
            ColorControl(
                id="gradient_high",
                name="Gradient High",
                description="High value color",
                value="#FF0000",
                group="display",
            )
        )
        self._controls.register(
            ColorControl(
                id="background",
                name="Background",
                description="Background color",
                value="#000000",
                group="display",
            )
        )

    def set_data(self, data: Any) -> None:
        """Set the bar values (array of 0.0 to 1.0)."""
        if isinstance(data, dict):
            self._values = [float(v) for v in data.get("values", [])]
        elif isinstance(data, (list, tuple)):
            self._values = [float(v) for v in data]
        else:
            self._values = [float(data)]

        # Clamp values
        self._values = [max(0.0, min(1.0, v)) for v in self._values]

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        if not self._values:
            background = hex_to_rgb(self.get_control("background"))
            pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
            pixels[:] = background
            return pixels

        bar_width = int(self.get_control("bar_width"))
        bar_gap = int(self.get_control("bar_gap"))
        direction = self.get_control("direction")
        color_mode = self.get_control("color_mode")
        background = hex_to_rgb(self.get_control("background"))

        num_bars = len(self._values)

        # Calculate bar width if auto
        if bar_width == 0:
            total_gaps = (num_bars - 1) * bar_gap
            bar_width = max(1, (num_pixels - total_gaps) // num_bars)

        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        pixels[:] = background

        rainbow = palette_registry.get("rainbow")

        for bar_idx, value in enumerate(self._values):
            # Calculate bar position
            bar_start = bar_idx * (bar_width + bar_gap)
            if bar_start >= num_pixels:
                break

            bar_end = min(bar_start + bar_width, num_pixels)
            fill_height = int(value * bar_width)

            # Get bar color
            if color_mode == "solid":
                bar_color = hex_to_rgb(self.get_control("color"))
            elif color_mode == "per_bar":
                bar_color = rainbow.get_color(bar_idx / max(1, num_bars - 1))
            else:
                bar_color = None  # Will be gradient

            # Fill bar
            for i in range(bar_start, bar_end):
                pixel_idx = i - bar_start
                if direction == "bottom_to_top":
                    in_fill = pixel_idx < fill_height
                    t = pixel_idx / max(1, bar_width - 1)
                else:
                    in_fill = (bar_width - 1 - pixel_idx) < fill_height
                    t = (bar_width - 1 - pixel_idx) / max(1, bar_width - 1)

                if in_fill:
                    if color_mode == "gradient":
                        low = hex_to_rgb(self.get_control("gradient_low"))
                        high = hex_to_rgb(self.get_control("gradient_high"))
                        pixels[i] = tuple(
                            int(low[j] + (high[j] - low[j]) * t) for j in range(3)
                        )
                    else:
                        pixels[i] = bar_color

        return pixels


class VUMeter(VirtualSource):
    """Audio-style level meter with segments."""

    source_type = "vu_meter"

    def __init__(self, config: VirtualSourceConfig | None = None):
        super().__init__(config)
        self._value = 0.0
        self._display_value = 0.0
        self._peak_value = 0.0
        self._peak_hold_counter = 0

    def _setup_controls(self) -> None:
        self._controls.register(
            NumberControl(
                id="segments",
                name="Segments",
                description="Number of segments",
                value=10,
                min=3,
                max=30,
                step=1,
                group="display",
            )
        )
        self._controls.register(
            BooleanControl(
                id="peak",
                name="Peak Hold",
                description="Show peak indicator",
                value=True,
                group="display",
            )
        )
        self._controls.register(
            NumberControl(
                id="decay",
                name="Decay",
                description="Decay rate for smooth falloff",
                value=0.95,
                min=0.8,
                max=0.99,
                step=0.01,
                group="display",
            )
        )
        self._controls.register(
            NumberControl(
                id="green_threshold",
                name="Green Threshold",
                description="Upper limit of green zone",
                value=0.6,
                min=0.3,
                max=0.8,
                step=0.05,
                group="display",
            )
        )
        self._controls.register(
            NumberControl(
                id="yellow_threshold",
                name="Yellow Threshold",
                description="Upper limit of yellow zone",
                value=0.85,
                min=0.5,
                max=0.95,
                step=0.05,
                group="display",
            )
        )
        self._controls.register(
            ColorControl(
                id="background",
                name="Background",
                description="Background color",
                value="#111111",
                group="display",
            )
        )

    def set_data(self, data: Any) -> None:
        """Set the meter value (0.0 to 1.0)."""
        if isinstance(data, dict):
            self._value = float(data.get("value", 0.0))
        else:
            self._value = float(data)
        self._value = max(0.0, min(1.0, self._value))

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        segments = int(self.get_control("segments"))
        show_peak = self.get_control("peak")
        decay = self.get_control("decay")
        green_threshold = self.get_control("green_threshold")
        yellow_threshold = self.get_control("yellow_threshold")
        background = hex_to_rgb(self.get_control("background"))

        # Smooth display value
        if self._value > self._display_value:
            self._display_value = self._value
        else:
            self._display_value *= decay

        # Update peak
        if self._value >= self._peak_value:
            self._peak_value = self._value
            self._peak_hold_counter = 30  # Hold for ~1 second at 30fps
        else:
            if self._peak_hold_counter > 0:
                self._peak_hold_counter -= 1
            else:
                self._peak_value *= 0.98

        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        pixels[:] = background

        pixels_per_segment = num_pixels // segments
        lit_segments = int(self._display_value * segments)
        peak_segment = int(self._peak_value * (segments - 1))

        for seg in range(segments):
            seg_start = seg * pixels_per_segment
            seg_end = seg_start + pixels_per_segment - 1  # Leave gap

            # Determine segment color based on position
            seg_position = seg / (segments - 1)
            if seg_position <= green_threshold:
                color = (0, 255, 0)  # Green
            elif seg_position <= yellow_threshold:
                color = (255, 255, 0)  # Yellow
            else:
                color = (255, 0, 0)  # Red

            # Dim background for off segments
            dim_color = tuple(c // 8 for c in color)

            if seg < lit_segments:
                # Lit segment
                for i in range(seg_start, min(seg_end, num_pixels)):
                    pixels[i] = color
            else:
                # Dim segment
                for i in range(seg_start, min(seg_end, num_pixels)):
                    pixels[i] = dim_color

            # Peak indicator
            if show_peak and seg == peak_segment:
                for i in range(seg_start, min(seg_end, num_pixels)):
                    pixels[i] = (255, 255, 255)

        return pixels
