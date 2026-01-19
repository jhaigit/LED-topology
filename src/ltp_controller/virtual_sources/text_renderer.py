"""Text rendering utilities for LED matrices.

Provides pixel-perfect text rendering with support for:
- Multiple bitmap fonts
- Horizontal and vertical text
- Scrolling animations
- Text alignment
- Word wrapping
"""

from enum import Enum
from typing import NamedTuple

import numpy as np

from ltp_controller.virtual_sources.fonts import (
    get_font,
    get_char_bitmap,
    list_fonts,
    DEFAULT_FONT,
    FontInfo,
)


class TextAlign(Enum):
    """Text alignment options."""
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class VerticalAlign(Enum):
    """Vertical alignment options."""
    TOP = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"


class ScrollDirection(Enum):
    """Scroll direction options."""
    NONE = "none"
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"


class TextMetrics(NamedTuple):
    """Metrics for rendered text."""
    width: int  # Total pixel width of text
    height: int  # Total pixel height of text
    char_count: int  # Number of characters


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def measure_text(text: str, font_name: str = DEFAULT_FONT) -> TextMetrics:
    """Measure the pixel dimensions of text.

    Args:
        text: Text to measure
        font_name: Font to use

    Returns:
        TextMetrics with width, height, and character count
    """
    info, _ = get_font(font_name)

    if not text:
        return TextMetrics(0, info.height, 0)

    # Calculate width (chars * char_width + spacing between chars)
    width = len(text) * info.width + (len(text) - 1) * info.spacing

    return TextMetrics(width, info.height, len(text))


def render_char_to_pixels(
    char: str,
    font_name: str = DEFAULT_FONT,
) -> np.ndarray:
    """Render a single character to a 2D pixel array.

    Args:
        char: Character to render
        font_name: Font to use

    Returns:
        2D boolean array (height, width) where True = pixel on
    """
    info, _ = get_font(font_name)
    bitmap = get_char_bitmap(char, font_name)

    if bitmap is None:
        return np.zeros((info.height, info.width), dtype=bool)

    pixels = np.zeros((info.height, info.width), dtype=bool)

    for row_idx, row_byte in enumerate(bitmap):
        for col_idx in range(info.width):
            # Read bits MSB first (bit width-1 is leftmost pixel)
            if row_byte & (1 << (info.width - 1 - col_idx)):
                pixels[row_idx, col_idx] = True

    return pixels


def render_text_to_pixels(
    text: str,
    font_name: str = DEFAULT_FONT,
) -> np.ndarray:
    """Render text to a 2D pixel array.

    Args:
        text: Text to render
        font_name: Font to use

    Returns:
        2D boolean array (height, width) where True = pixel on
    """
    if not text:
        info, _ = get_font(font_name)
        return np.zeros((info.height, 0), dtype=bool)

    info, _ = get_font(font_name)
    metrics = measure_text(text, font_name)

    pixels = np.zeros((metrics.height, metrics.width), dtype=bool)
    x_offset = 0

    for char in text:
        char_pixels = render_char_to_pixels(char, font_name)
        char_width = char_pixels.shape[1]

        # Copy character pixels
        pixels[:, x_offset:x_offset + char_width] = char_pixels

        # Move to next character position
        x_offset += char_width + info.spacing

    return pixels


class TextRenderer:
    """High-level text renderer for LED matrices.

    Supports:
    - Static text rendering
    - Scrolling text
    - Text alignment
    - Multiple fonts
    - Foreground/background colors
    """

    def __init__(
        self,
        width: int,
        height: int,
        font_name: str = DEFAULT_FONT,
        text_color: tuple[int, int, int] = (255, 255, 255),
        background_color: tuple[int, int, int] = (0, 0, 0),
        align: TextAlign = TextAlign.LEFT,
        vertical_align: VerticalAlign = VerticalAlign.MIDDLE,
    ):
        """Initialize text renderer.

        Args:
            width: Display width in pixels
            height: Display height in pixels
            font_name: Font to use (5x7, 3x5, 4x6)
            text_color: RGB text color
            background_color: RGB background color
            align: Horizontal text alignment
            vertical_align: Vertical text alignment
        """
        self.width = width
        self.height = height
        self.font_name = font_name
        self.text_color = text_color
        self.background_color = background_color
        self.align = align
        self.vertical_align = vertical_align

        self._font_info, _ = get_font(font_name)

    def set_font(self, font_name: str) -> None:
        """Change the font."""
        self.font_name = font_name
        self._font_info, _ = get_font(font_name)

    def set_colors(
        self,
        text_color: tuple[int, int, int] | str | None = None,
        background_color: tuple[int, int, int] | str | None = None,
    ) -> None:
        """Set text and/or background colors.

        Colors can be RGB tuples or hex strings.
        """
        if text_color is not None:
            if isinstance(text_color, str):
                text_color = hex_to_rgb(text_color)
            self.text_color = text_color

        if background_color is not None:
            if isinstance(background_color, str):
                background_color = hex_to_rgb(background_color)
            self.background_color = background_color

    def render_static(
        self,
        text: str,
        x_offset: int = 0,
        y_offset: int = 0,
    ) -> np.ndarray:
        """Render static text to pixel array.

        Args:
            text: Text to render
            x_offset: Additional horizontal offset
            y_offset: Additional vertical offset

        Returns:
            RGB pixel array (height, width, 3)
        """
        # Create output buffer with background
        output = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        output[:] = self.background_color

        if not text:
            return output

        # Render text to boolean mask
        text_pixels = render_text_to_pixels(text, self.font_name)
        text_height, text_width = text_pixels.shape

        # Calculate alignment offsets
        if self.align == TextAlign.LEFT:
            align_x = 0
        elif self.align == TextAlign.CENTER:
            align_x = (self.width - text_width) // 2
        else:  # RIGHT
            align_x = self.width - text_width

        if self.vertical_align == VerticalAlign.TOP:
            align_y = 0
        elif self.vertical_align == VerticalAlign.MIDDLE:
            align_y = (self.height - text_height) // 2
        else:  # BOTTOM
            align_y = self.height - text_height

        # Apply offsets
        x_start = align_x + x_offset
        y_start = align_y + y_offset

        # Clip to display bounds
        src_x_start = max(0, -x_start)
        src_y_start = max(0, -y_start)
        dst_x_start = max(0, x_start)
        dst_y_start = max(0, y_start)

        copy_width = min(text_width - src_x_start, self.width - dst_x_start)
        copy_height = min(text_height - src_y_start, self.height - dst_y_start)

        if copy_width <= 0 or copy_height <= 0:
            return output

        # Copy text pixels with color
        text_mask = text_pixels[
            src_y_start:src_y_start + copy_height,
            src_x_start:src_x_start + copy_width
        ]

        output[
            dst_y_start:dst_y_start + copy_height,
            dst_x_start:dst_x_start + copy_width
        ][text_mask] = self.text_color

        return output

    def render_scrolling(
        self,
        text: str,
        scroll_position: float,
        direction: ScrollDirection = ScrollDirection.LEFT,
        padding: int | None = None,
    ) -> np.ndarray:
        """Render scrolling text.

        Args:
            text: Text to scroll
            scroll_position: Scroll position (0.0 to 1.0 for one cycle)
            direction: Scroll direction
            padding: Padding pixels between text repeats (default: display width)

        Returns:
            RGB pixel array (height, width, 3)
        """
        if direction == ScrollDirection.NONE:
            return self.render_static(text)

        # Default padding to display width for seamless scroll
        if padding is None:
            padding = self.width

        # Render text to boolean mask
        text_pixels = render_text_to_pixels(text, self.font_name)
        text_height, text_width = text_pixels.shape

        if text_width == 0:
            output = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            output[:] = self.background_color
            return output

        # Total scroll distance
        total_width = text_width + padding

        if direction in (ScrollDirection.LEFT, ScrollDirection.RIGHT):
            # Horizontal scrolling
            offset = int(scroll_position * total_width) % total_width

            if direction == ScrollDirection.RIGHT:
                offset = -offset

            return self.render_static(text, x_offset=-offset)

        else:
            # Vertical scrolling
            total_height = text_height + padding
            offset = int(scroll_position * total_height) % total_height

            if direction == ScrollDirection.DOWN:
                offset = -offset

            return self.render_static(text, y_offset=-offset)

    def render_multiline(
        self,
        lines: list[str],
        line_spacing: int = 1,
        y_offset: int = 0,
    ) -> np.ndarray:
        """Render multiple lines of text.

        Args:
            lines: List of text lines
            line_spacing: Pixels between lines
            y_offset: Vertical offset for scrolling

        Returns:
            RGB pixel array (height, width, 3)
        """
        output = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        output[:] = self.background_color

        if not lines:
            return output

        line_height = self._font_info.height + line_spacing
        total_height = len(lines) * line_height - line_spacing

        # Calculate vertical alignment
        if self.vertical_align == VerticalAlign.TOP:
            start_y = 0
        elif self.vertical_align == VerticalAlign.MIDDLE:
            start_y = (self.height - total_height) // 2
        else:  # BOTTOM
            start_y = self.height - total_height

        start_y += y_offset

        for i, line in enumerate(lines):
            line_y = start_y + i * line_height

            # Skip lines completely off screen
            if line_y + self._font_info.height < 0:
                continue
            if line_y >= self.height:
                continue

            # Render line
            line_pixels = render_text_to_pixels(line, self.font_name)
            line_pixel_height, line_pixel_width = line_pixels.shape

            # Calculate horizontal alignment
            if self.align == TextAlign.LEFT:
                line_x = 0
            elif self.align == TextAlign.CENTER:
                line_x = (self.width - line_pixel_width) // 2
            else:  # RIGHT
                line_x = self.width - line_pixel_width

            # Clip to display bounds
            src_x = max(0, -line_x)
            src_y = max(0, -line_y)
            dst_x = max(0, line_x)
            dst_y = max(0, line_y)

            copy_w = min(line_pixel_width - src_x, self.width - dst_x)
            copy_h = min(line_pixel_height - src_y, self.height - dst_y)

            if copy_w > 0 and copy_h > 0:
                text_mask = line_pixels[
                    src_y:src_y + copy_h,
                    src_x:src_x + copy_w
                ]
                output[
                    dst_y:dst_y + copy_h,
                    dst_x:dst_x + copy_w
                ][text_mask] = self.text_color

        return output

    def to_linear(self, frame: np.ndarray) -> np.ndarray:
        """Convert 2D RGB frame to linear pixel array.

        Args:
            frame: 2D frame (height, width, 3)

        Returns:
            Linear array (num_pixels, 3) in row-major order
        """
        return frame.reshape(-1, 3)


def wrap_text(text: str, max_width: int, font_name: str = DEFAULT_FONT) -> list[str]:
    """Wrap text to fit within a maximum pixel width.

    Args:
        text: Text to wrap
        max_width: Maximum width in pixels
        font_name: Font to use

    Returns:
        List of wrapped lines
    """
    info, _ = get_font(font_name)
    char_width = info.width + info.spacing

    # Calculate characters per line
    chars_per_line = max_width // char_width
    if chars_per_line < 1:
        chars_per_line = 1

    lines = []
    words = text.split()
    current_line = ""

    for word in words:
        test_line = f"{current_line} {word}".strip() if current_line else word

        if len(test_line) <= chars_per_line:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            # Handle words longer than a line
            while len(word) > chars_per_line:
                lines.append(word[:chars_per_line])
                word = word[chars_per_line:]
            current_line = word

    if current_line:
        lines.append(current_line)

    return lines if lines else [""]
