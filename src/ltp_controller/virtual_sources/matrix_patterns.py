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

        for i in range(num_pixels):
            # Convert linear index to 2D coordinates
            x = i % width
            y = i // width

            # Draw grid lines
            if (x + offset) % spacing == 0 or (y + offset) % spacing == 0:
                pixels[i] = line_color

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

        for i in range(num_pixels):
            x = i % width
            y = i // width

            # Top-left corner (Red)
            if x < marker_size and y < marker_size:
                pixels[i] = red
            # Top-right corner (Green)
            elif x >= width - marker_size and y < marker_size:
                pixels[i] = green
            # Bottom-left corner (Blue)
            elif x < marker_size and y >= height - marker_size:
                pixels[i] = blue
            # Bottom-right corner (Yellow)
            elif x >= width - marker_size and y >= height - marker_size:
                pixels[i] = yellow
            # Edges
            elif show_edges:
                edge_brightness = 0.3
                if y == 0:  # Top edge
                    pixels[i] = (int(128 * edge_brightness), 0, 0)
                elif y == height - 1:  # Bottom edge
                    pixels[i] = (0, 0, int(128 * edge_brightness))
                elif x == 0:  # Left edge
                    pixels[i] = (int(64 * edge_brightness), 0, int(64 * edge_brightness))
                elif x == width - 1:  # Right edge
                    pixels[i] = (0, int(64 * edge_brightness), int(64 * edge_brightness))

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

        if mode == "rows":
            pos = int((time_elapsed / cycle_time * height) % height)
            for i in range(num_pixels):
                x = i % width
                y = i // width
                dist = abs(y - pos)
                if dist < line_width:
                    pixels[i] = color
                elif fade_trail and y < pos:
                    fade = max(0, 1.0 - (pos - y) / height)
                    pixels[i] = tuple(int(c * fade * 0.3) for c in color)

        elif mode == "columns":
            pos = int((time_elapsed / cycle_time * width) % width)
            for i in range(num_pixels):
                x = i % width
                y = i // width
                dist = abs(x - pos)
                if dist < line_width:
                    pixels[i] = color
                elif fade_trail and x < pos:
                    fade = max(0, 1.0 - (pos - x) / width)
                    pixels[i] = tuple(int(c * fade * 0.3) for c in color)

        elif mode == "both":
            # Alternate between rows and columns
            phase = int(time_elapsed / cycle_time) % 2
            if phase == 0:
                pos = int((time_elapsed / cycle_time * height) % height)
                for i in range(num_pixels):
                    y = i // width
                    if abs(y - pos) < line_width:
                        pixels[i] = color
            else:
                pos = int((time_elapsed / cycle_time * width) % width)
                for i in range(num_pixels):
                    x = i % width
                    if abs(x - pos) < line_width:
                        pixels[i] = color

        elif mode == "diagonal":
            diag_size = width + height
            pos = int((time_elapsed / cycle_time * diag_size) % diag_size)
            for i in range(num_pixels):
                x = i % width
                y = i // width
                if abs((x + y) - pos) < line_width:
                    pixels[i] = color

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

        for i in range(num_pixels):
            x = i % width
            y = i // width

            # Determine checker cell
            cell_x = (x + offset) // cell_size
            cell_y = (y + offset) // cell_size

            if (cell_x + cell_y) % 2 == 0:
                pixels[i] = color1
            else:
                pixels[i] = color2

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

        if mode == "gradient":
            # Rainbow gradient by pixel index
            for i in range(num_pixels):
                hue = ((i / num_pixels) + offset) % 1.0
                pixels[i] = self._hsv_to_rgb(hue, 1.0, 1.0)

        elif mode == "segments":
            # Different color per segment
            colors = [
                (255, 0, 0), (0, 255, 0), (0, 0, 255),
                (255, 255, 0), (255, 0, 255), (0, 255, 255),
                (255, 128, 0), (128, 0, 255),
            ]
            for i in range(num_pixels):
                segment = i // segment_size
                pixels[i] = colors[segment % len(colors)]

        elif mode == "binary":
            # Odd/even alternation
            for i in range(num_pixels):
                if i % 2 == 0:
                    pixels[i] = (255, 255, 255)
                else:
                    pixels[i] = (0, 0, 0)

        elif mode == "first_last":
            # Highlight first and last pixels
            pixels[0] = (255, 0, 0)  # First pixel: red
            if num_pixels > 1:
                pixels[-1] = (0, 255, 0)  # Last pixel: green
            # Middle pixels dim white
            for i in range(1, num_pixels - 1):
                pixels[i] = (32, 32, 32)

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

        for i in range(num_pixels):
            x = i % width
            y = i // width

            if mode == "xy_gradient":
                # Red increases with X, Green increases with Y
                r = int((x / max(1, width - 1)) * 255) if width > 1 else 128
                g = int((y / max(1, height - 1)) * 255) if height > 1 else 128
                pixels[i] = (r, g, 0)

            elif mode == "x_only":
                # Red gradient along X
                r = int((x / max(1, width - 1)) * 255) if width > 1 else 128
                pixels[i] = (r, 0, 0)

            elif mode == "y_only":
                # Green gradient along Y
                g = int((y / max(1, height - 1)) * 255) if height > 1 else 128
                pixels[i] = (0, g, 0)

            elif mode == "quadrants":
                # Different color per quadrant
                if x < center_x and y < center_y:
                    pixels[i] = (255, 0, 0)  # Top-left: Red
                elif x >= center_x and y < center_y:
                    pixels[i] = (0, 255, 0)  # Top-right: Green
                elif x < center_x and y >= center_y:
                    pixels[i] = (0, 0, 255)  # Bottom-left: Blue
                else:
                    pixels[i] = (255, 255, 0)  # Bottom-right: Yellow

            # Highlight center
            if show_center and x == center_x and y == center_y:
                pixels[i] = (255, 255, 255)

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

        if style == "bars":
            # SMPTE-style color bars
            colors = [
                (255, 255, 255),  # White
                (255, 255, 0),    # Yellow
                (0, 255, 255),    # Cyan
                (0, 255, 0),      # Green
                (255, 0, 255),    # Magenta
                (255, 0, 0),      # Red
                (0, 0, 255),      # Blue
                (0, 0, 0),        # Black
            ]
            bar_width = width // len(colors) if width >= len(colors) else 1

            for i in range(num_pixels):
                x = i % width
                bar_idx = min(x // bar_width, len(colors) - 1)
                pixels[i] = colors[bar_idx]

        elif style == "grayscale":
            # Grayscale gradient
            for i in range(num_pixels):
                x = i % width
                gray = int((x / max(1, width - 1)) * 255) if width > 1 else 128
                pixels[i] = (gray, gray, gray)

        elif style == "rgb":
            # RGB bars (horizontal thirds)
            third = height // 3 if height >= 3 else 1

            for i in range(num_pixels):
                y = i // width
                if y < third:
                    pixels[i] = (255, 0, 0)
                elif y < 2 * third:
                    pixels[i] = (0, 255, 0)
                else:
                    pixels[i] = (0, 0, 255)

        elif style == "white":
            pixels[:] = (255, 255, 255)

        return pixels
