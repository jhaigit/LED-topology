"""Matrix test patterns for diagnosing pixel addressing issues.

These patterns are designed to help identify:
- Serpentine addressing errors
- Row/column order issues
- Off-by-one errors
- Orientation problems
- Pixel mapping errors
"""

import math
from typing import Any

import numpy as np

from libltp import NumberControl, BooleanControl, EnumControl, ColorControl

from ltp_controller.virtual_sources.base import VirtualSource, VirtualSourceConfig


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


class GridPattern(VirtualSource):
    """Grid pattern showing row and column lines.

    Helps identify:
    - Row/column alignment
    - Matrix dimensions
    - Serpentine issues (lines should be straight)
    """

    source_type = "grid"

    def _setup_controls(self) -> None:
        self._controls.register(
            NumberControl(
                id="grid_spacing",
                name="Grid Spacing",
                description="Pixels between grid lines",
                value=4.0,
                min=2.0,
                max=16.0,
                step=1.0,
                group="pattern",
            )
        )
        self._controls.register(
            ColorControl(
                id="line_color",
                name="Line Color",
                description="Grid line color",
                value="#FFFFFF",
                group="pattern",
            )
        )
        self._controls.register(
            ColorControl(
                id="background",
                name="Background",
                description="Background color",
                value="#000000",
                group="pattern",
            )
        )
        self._controls.register(
            BooleanControl(
                id="animate",
                name="Animate",
                description="Animate grid movement",
                value=False,
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)

        # Get dimensions from config
        dims = self.config.output_dimensions
        if len(dims) >= 2:
            width, height = dims[0], dims[1]
        else:
            width = dims[0]
            height = 1

        spacing = int(self.get_control("grid_spacing"))
        line_color = hex_to_rgb(self.get_control("line_color"))
        bg_color = hex_to_rgb(self.get_control("background"))
        animate = self.get_control("animate")

        # Animation offset
        offset = int(time_elapsed * 2) % spacing if animate else 0

        # Fill background
        pixels[:] = bg_color

        idx = np.arange(num_pixels)
        x = idx % width
        y = idx // width
        line = ((x + offset) % spacing == 0) | ((y + offset) % spacing == 0)
        pixels[line] = line_color

        return pixels


class CornerMarkers(VirtualSource):
    """Marks the four corners with distinct colors.

    Helps identify:
    - Matrix orientation
    - Which corner is (0,0)
    - Rotation issues

    Colors:
    - Top-left: Red
    - Top-right: Green
    - Bottom-left: Blue
    - Bottom-right: Yellow
    """

    source_type = "corners"

    def _setup_controls(self) -> None:
        self._controls.register(
            NumberControl(
                id="marker_size",
                name="Marker Size",
                description="Size of corner markers in pixels",
                value=3.0,
                min=1.0,
                max=8.0,
                step=1.0,
                group="pattern",
            )
        )
        self._controls.register(
            BooleanControl(
                id="show_edges",
                name="Show Edges",
                description="Draw lines along edges",
                value=True,
                group="pattern",
            )
        )
        self._controls.register(
            BooleanControl(
                id="animate",
                name="Animate",
                description="Pulse the corner markers",
                value=False,
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)

        dims = self.config.output_dimensions
        if len(dims) >= 2:
            width, height = dims[0], dims[1]
        else:
            width = dims[0]
            height = 1

        marker_size = int(self.get_control("marker_size"))
        show_edges = self.get_control("show_edges")
        animate = self.get_control("animate")

        # Animation brightness
        if animate:
            brightness = 0.5 + 0.5 * math.sin(time_elapsed * 3)
        else:
            brightness = 1.0

        # Corner colors (with brightness)
        red = tuple(int(c * brightness) for c in (255, 0, 0))
        green = tuple(int(c * brightness) for c in (0, 255, 0))
        blue = tuple(int(c * brightness) for c in (0, 0, 255))
        yellow = tuple(int(c * brightness) for c in (255, 255, 0))

        idx = np.arange(num_pixels)
        x = idx % width
        y = idx // width

        # Corner precedence tl > tr > bl > br (elif chain); make the masks
        # mutually exclusive so they can overlap on thin strips (height==1).
        tl = (x < marker_size) & (y < marker_size)
        tr = ~tl & (x >= width - marker_size) & (y < marker_size)
        bl = ~tl & ~tr & (x < marker_size) & (y >= height - marker_size)
        br = ~tl & ~tr & ~bl & (x >= width - marker_size) & (y >= height - marker_size)
        pixels[tl] = red
        pixels[tr] = green
        pixels[bl] = blue
        pixels[br] = yellow

        if show_edges:
            eb = 0.3
            avail = ~(tl | tr | bl | br)
            top = avail & (y == 0)
            bot = avail & ~top & (y == height - 1)
            left = avail & ~top & ~bot & (x == 0)
            right = avail & ~top & ~bot & ~left & (x == width - 1)
            pixels[top] = (int(128 * eb), 0, 0)
            pixels[bot] = (0, 0, int(128 * eb))
            pixels[left] = (int(64 * eb), 0, int(64 * eb))
            pixels[right] = (0, int(64 * eb), int(64 * eb))

        return pixels


class RowColumnSweep(VirtualSource):
    """Sweeps a line across rows then columns.

    Helps identify:
    - Serpentine addressing (line should be straight)
    - Row/column order
    - Pixel mapping errors
    """

    source_type = "sweep"

    def _setup_controls(self) -> None:
        self._controls.register(
            EnumControl(
                id="mode",
                name="Mode",
                description="Sweep direction",
                value="rows",
                options=[
                    {"value": "rows", "label": "Rows", "description": "Sweep horizontal lines"},
                    {"value": "columns", "label": "Columns", "description": "Sweep vertical lines"},
                    {"value": "both", "label": "Both", "description": "Alternate rows and columns"},
                    {"value": "diagonal", "label": "Diagonal", "description": "Sweep diagonally"},
                ],
                group="pattern",
            )
        )
        self._controls.register(
            ColorControl(
                id="color",
                name="Color",
                description="Sweep line color",
                value="#FFFFFF",
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="line_width",
                name="Line Width",
                description="Width of sweep line",
                value=1.0,
                min=1.0,
                max=4.0,
                step=1.0,
                group="pattern",
            )
        )
        self._controls.register(
            BooleanControl(
                id="fade_trail",
                name="Fade Trail",
                description="Leave fading trail",
                value=True,
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)

        dims = self.config.output_dimensions
        if len(dims) >= 2:
            width, height = dims[0], dims[1]
        else:
            width = dims[0]
            height = 1

        mode = self.get_control("mode")
        color = hex_to_rgb(self.get_control("color"))
        line_width = int(self.get_control("line_width"))
        fade_trail = self.get_control("fade_trail")

        # Calculate sweep position
        cycle_time = 2.0  # seconds per full sweep

        idx = np.arange(num_pixels)
        x = idx % width
        y = idx // width
        col = np.array(color, dtype=np.float64)

        def _sweep(axis, span):
            # Shared rows/columns logic: line where |axis - pos| < line_width,
            # else a fading trail behind the sweep (axis < pos).
            pos = int((time_elapsed / cycle_time * span) % span)
            line = np.abs(axis - pos) < line_width
            pixels[line] = color
            if fade_trail:
                trail = ~line & (axis < pos)
                fade = np.maximum(0.0, 1.0 - (pos - axis) / span)
                vals = (col * fade[:, None] * 0.3).astype(np.uint8)
                pixels[trail] = vals[trail]

        if mode == "rows":
            _sweep(y, height)
        elif mode == "columns":
            _sweep(x, width)
        elif mode == "both":
            phase = int(time_elapsed / cycle_time) % 2
            if phase == 0:
                pos = int((time_elapsed / cycle_time * height) % height)
                pixels[np.abs(y - pos) < line_width] = color
            else:
                pos = int((time_elapsed / cycle_time * width) % width)
                pixels[np.abs(x - pos) < line_width] = color
        elif mode == "diagonal":
            diag_size = width + height
            pos = int((time_elapsed / cycle_time * diag_size) % diag_size)
            pixels[np.abs((x + y) - pos) < line_width] = color

        return pixels


class Checkerboard(VirtualSource):
    """Checkerboard pattern.

    Helps identify:
    - Serpentine addressing (pattern should be regular)
    - Off-by-one errors
    - Pixel skipping
    """

    source_type = "checkerboard"

    def _setup_controls(self) -> None:
        self._controls.register(
            NumberControl(
                id="cell_size",
                name="Cell Size",
                description="Size of each checker cell",
                value=2.0,
                min=1.0,
                max=8.0,
                step=1.0,
                group="pattern",
            )
        )
        self._controls.register(
            ColorControl(
                id="color1",
                name="Color 1",
                description="First checker color",
                value="#FFFFFF",
                group="pattern",
            )
        )
        self._controls.register(
            ColorControl(
                id="color2",
                name="Color 2",
                description="Second checker color",
                value="#000000",
                group="pattern",
            )
        )
        self._controls.register(
            BooleanControl(
                id="animate",
                name="Animate",
                description="Animate checker movement",
                value=False,
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)

        dims = self.config.output_dimensions
        if len(dims) >= 2:
            width, height = dims[0], dims[1]
        else:
            width = dims[0]
            height = 1

        cell_size = int(self.get_control("cell_size"))
        color1 = hex_to_rgb(self.get_control("color1"))
        color2 = hex_to_rgb(self.get_control("color2"))
        animate = self.get_control("animate")

        # Animation offset
        offset = int(time_elapsed * 2) if animate else 0

        idx = np.arange(num_pixels)
        x = idx % width
        y = idx // width
        cell_x = (x + offset) // cell_size
        cell_y = (y + offset) // cell_size
        even = (cell_x + cell_y) % 2 == 0
        pixels[even] = color1
        pixels[~even] = color2

        return pixels


class PixelIndex(VirtualSource):
    """Shows pixel index as color gradient.

    Helps identify:
    - Pixel addressing order
    - Where index 0 is located
    - Linear vs serpentine mapping
    """

    source_type = "pixel_index"

    def _setup_controls(self) -> None:
        self._controls.register(
            EnumControl(
                id="mode",
                name="Mode",
                description="Display mode",
                value="gradient",
                options=[
                    {"value": "gradient", "label": "Gradient", "description": "Smooth color gradient by index"},
                    {"value": "segments", "label": "Segments", "description": "Color segments every N pixels"},
                    {"value": "binary", "label": "Binary", "description": "Alternate colors for odd/even"},
                    {"value": "first_last", "label": "First/Last", "description": "Highlight first and last pixels"},
                ],
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="segment_size",
                name="Segment Size",
                description="Pixels per segment (for segment mode)",
                value=10.0,
                min=1.0,
                max=50.0,
                step=1.0,
                group="pattern",
            )
        )
        self._controls.register(
            BooleanControl(
                id="animate",
                name="Animate",
                description="Animate the gradient",
                value=False,
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)

        mode = self.get_control("mode")
        segment_size = int(self.get_control("segment_size"))
        animate = self.get_control("animate")

        offset = time_elapsed * 0.2 if animate else 0

        idx = np.arange(num_pixels)

        if mode == "gradient":
            # Rainbow gradient by pixel index (HSV with s=v=1, so only the hue
            # sextant math matters — replicated vectorized below).
            hue = ((idx / num_pixels) + offset) % 1.0
            h6 = hue * 6
            ii = h6.astype(np.int64)
            f = h6 - ii
            r = np.select([ii == 0, ii == 1, ii == 2, ii == 3, ii == 4],
                          [1.0, 1.0 - f, 0.0, 0.0, f], default=1.0)
            g = np.select([ii == 0, ii == 1, ii == 2, ii == 3, ii == 4],
                          [f, 1.0, 1.0, 1.0 - f, 0.0], default=0.0)
            b = np.select([ii == 0, ii == 1, ii == 2, ii == 3, ii == 4],
                          [0.0, 0.0, f, 1.0, 1.0], default=1.0 - f)
            pixels[:] = (np.stack([r, g, b], axis=1) * 255).astype(np.uint8)

        elif mode == "segments":
            colors = np.array([
                (255, 0, 0), (0, 255, 0), (0, 0, 255),
                (255, 255, 0), (255, 0, 255), (0, 255, 255),
                (255, 128, 0), (128, 0, 255),
            ], dtype=np.uint8)
            pixels[:] = colors[(idx // segment_size) % len(colors)]

        elif mode == "binary":
            even = idx % 2 == 0
            pixels[even] = (255, 255, 255)
            pixels[~even] = (0, 0, 0)

        elif mode == "first_last":
            pixels[1:-1] = (32, 32, 32)  # middle pixels dim white
            pixels[0] = (255, 0, 0)      # first pixel: red
            if num_pixels > 1:
                pixels[-1] = (0, 255, 0)  # last pixel: green

        return pixels

    def _hsv_to_rgb(self, h: float, s: float, v: float) -> tuple[int, int, int]:
        """Convert HSV to RGB."""
        if s == 0:
            return (int(v * 255), int(v * 255), int(v * 255))

        h = h * 6
        i = int(h)
        f = h - i
        p = v * (1 - s)
        q = v * (1 - s * f)
        t = v * (1 - s * (1 - f))

        if i == 0:
            r, g, b = v, t, p
        elif i == 1:
            r, g, b = q, v, p
        elif i == 2:
            r, g, b = p, v, t
        elif i == 3:
            r, g, b = p, q, v
        elif i == 4:
            r, g, b = t, p, v
        else:
            r, g, b = v, p, q

        return (int(r * 255), int(g * 255), int(b * 255))


class CoordinateDisplay(VirtualSource):
    """Shows X and Y coordinates with color coding.

    Helps identify:
    - Which axis is X vs Y
    - Coordinate system orientation
    - Row-major vs column-major ordering
    """

    source_type = "coordinates"

    def _setup_controls(self) -> None:
        self._controls.register(
            EnumControl(
                id="mode",
                name="Mode",
                description="Coordinate display mode",
                value="xy_gradient",
                options=[
                    {"value": "xy_gradient", "label": "XY Gradient", "description": "Red=X, Green=Y"},
                    {"value": "x_only", "label": "X Only", "description": "Show only X position"},
                    {"value": "y_only", "label": "Y Only", "description": "Show only Y position"},
                    {"value": "quadrants", "label": "Quadrants", "description": "Color each quadrant"},
                ],
                group="pattern",
            )
        )
        self._controls.register(
            BooleanControl(
                id="show_center",
                name="Show Center",
                description="Highlight center pixel",
                value=True,
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)

        dims = self.config.output_dimensions
        if len(dims) >= 2:
            width, height = dims[0], dims[1]
        else:
            width = dims[0]
            height = 1

        mode = self.get_control("mode")
        show_center = self.get_control("show_center")

        center_x = width // 2
        center_y = height // 2

        idx = np.arange(num_pixels)
        x = idx % width
        y = idx // width

        if width > 1:
            rx = (x / (width - 1) * 255).astype(np.int64)
        else:
            rx = np.full(num_pixels, 128, dtype=np.int64)
        if height > 1:
            gy = (y / (height - 1) * 255).astype(np.int64)
        else:
            gy = np.full(num_pixels, 128, dtype=np.int64)

        if mode == "xy_gradient":
            pixels[:, 0] = rx
            pixels[:, 1] = gy
        elif mode == "x_only":
            pixels[:, 0] = rx
        elif mode == "y_only":
            pixels[:, 1] = gy
        elif mode == "quadrants":
            left = x < center_x
            top = y < center_y
            pixels[left & top] = (255, 0, 0)
            pixels[~left & top] = (0, 255, 0)
            pixels[left & ~top] = (0, 0, 255)
            pixels[~left & ~top] = (255, 255, 0)

        # Highlight center (overrides mode output)
        if show_center:
            pixels[(x == center_x) & (y == center_y)] = (255, 255, 255)

        return pixels


class TestCard(VirtualSource):
    """TV-style test card pattern.

    Comprehensive test pattern showing:
    - Color bars
    - Grayscale ramp
    - Edge detection zones
    """

    source_type = "test_card"

    def _setup_controls(self) -> None:
        self._controls.register(
            EnumControl(
                id="style",
                name="Style",
                description="Test card style",
                value="bars",
                options=[
                    {"value": "bars", "label": "Color Bars", "description": "SMPTE-style color bars"},
                    {"value": "grayscale", "label": "Grayscale", "description": "Grayscale gradient"},
                    {"value": "rgb", "label": "RGB Bars", "description": "Red, Green, Blue bars"},
                    {"value": "white", "label": "White", "description": "Full white (brightness test)"},
                ],
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)

        dims = self.config.output_dimensions
        if len(dims) >= 2:
            width, height = dims[0], dims[1]
        else:
            width = dims[0]
            height = 1

        style = self.get_control("style")

        idx = np.arange(num_pixels)
        x = idx % width
        y = idx // width

        if style == "bars":
            # SMPTE-style color bars
            colors = np.array([
                (255, 255, 255),  # White
                (255, 255, 0),    # Yellow
                (0, 255, 255),    # Cyan
                (0, 255, 0),      # Green
                (255, 0, 255),    # Magenta
                (255, 0, 0),      # Red
                (0, 0, 255),      # Blue
                (0, 0, 0),        # Black
            ], dtype=np.uint8)
            bar_width = width // len(colors) if width >= len(colors) else 1
            bar_idx = np.minimum(x // bar_width, len(colors) - 1)
            pixels[:] = colors[bar_idx]

        elif style == "grayscale":
            if width > 1:
                gray = (x / (width - 1) * 255).astype(np.int64)
            else:
                gray = np.full(num_pixels, 128, dtype=np.int64)
            pixels[:] = gray[:, None]

        elif style == "rgb":
            third = height // 3 if height >= 3 else 1
            pixels[y < third] = (255, 0, 0)
            pixels[(y >= third) & (y < 2 * third)] = (0, 255, 0)
            pixels[y >= 2 * third] = (0, 0, 255)

        elif style == "white":
            pixels[:] = (255, 255, 255)

        return pixels
