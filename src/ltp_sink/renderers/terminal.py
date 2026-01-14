"""Terminal renderer using rich library."""

import asyncio
import time
from enum import Enum
from typing import Any

import numpy as np
from pydantic import Field
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ltp_sink.renderers.base import Renderer, RendererConfig


class TerminalStyle(str, Enum):
    """Terminal rendering styles."""

    BLOCK = "block"
    BRAILLE = "braille"
    ASCII = "ascii"
    BAR = "bar"


class TerminalConfig(RendererConfig):
    """Configuration for terminal renderer."""

    type: str = "terminal"
    style: TerminalStyle = TerminalStyle.BLOCK
    width: int | None = None  # Auto-detect if None
    show_info: bool = True
    show_border: bool = True
    title: str = "LTP Sink"


class TerminalRenderer(Renderer):
    """Renders LED data to terminal using Unicode and colors."""

    # ASCII intensity characters (darkest to brightest)
    ASCII_CHARS = " .:-=+*#%@"

    # Bar characters for bar style
    BAR_CHARS = " ▁▂▃▄▅▆▇█"

    def __init__(self, config: TerminalConfig | None = None):
        super().__init__(config or TerminalConfig())
        self.config: TerminalConfig = self.config

        self._console = Console()
        self._live: Live | None = None
        self._last_frame_time = 0.0
        self._last_data_time = 0.0  # Track when data last arrived
        self._fps_samples: list[float] = []
        self._last_pixels: np.ndarray | None = None
        self._last_dimensions: tuple[int, ...] | None = None
        self._data_rate = 0.0
        self._bytes_received = 0
        self._data_timeout = 2.0  # Seconds before showing "waiting"

    async def start(self) -> None:
        """Start the terminal renderer."""
        await super().start()
        self._last_frame_time = time.time()
        self._last_data_time = 0.0  # Reset on start
        self._live = Live(
            self._render_display(),
            console=self._console,
            refresh_per_second=30,
            transient=True,
        )
        self._live.start()

    async def stop(self) -> None:
        """Stop the terminal renderer."""
        if self._live:
            self._live.stop()
            self._live = None
        await super().stop()

    def clear(self) -> None:
        """Clear display and show waiting message (called when stream stops)."""
        self._last_pixels = None
        self._last_dimensions = None
        self._last_data_time = 0.0
        self._fps_samples.clear()
        self._fps = 0.0
        self._data_rate = 0.0
        if self._live:
            self._live.update(self._render_empty())

    def _render_empty(self) -> Panel:
        """Render empty display."""
        return Panel(
            Text("Waiting for data...", style="dim"),
            title=self.config.title,
            border_style="blue",
        )

    def _render_display(self) -> Panel:
        """Render current display state - either data or waiting message."""
        now = time.time()

        # Check if we have data and it's not stale
        if self._last_pixels is not None and self._last_dimensions is not None:
            if self._last_data_time > 0 and (now - self._last_data_time) < self._data_timeout:
                return self._render_frame(self._last_pixels, self._last_dimensions)

        # No data or data is stale - clear FPS samples and show waiting
        if self._last_data_time > 0 and (now - self._last_data_time) >= self._data_timeout:
            self._fps_samples.clear()
            self._fps = 0.0
            self._data_rate = 0.0

        return self._render_empty()

    def render(self, pixels: np.ndarray, dimensions: tuple[int, ...]) -> None:
        """Render a frame of pixel data."""
        super().render(pixels, dimensions)

        # Calculate FPS
        now = time.time()
        if self._last_frame_time > 0:
            dt = now - self._last_frame_time
            if dt > 0:
                self._fps_samples.append(1.0 / dt)
                if len(self._fps_samples) > 30:
                    self._fps_samples.pop(0)
                self._fps = sum(self._fps_samples) / len(self._fps_samples)
        self._last_frame_time = now
        self._last_data_time = now  # Track when data last arrived

        # Track data rate
        self._bytes_received += pixels.nbytes
        self._data_rate = pixels.nbytes * self._fps

        # Store for display
        self._last_pixels = pixels
        self._last_dimensions = dimensions

        # Update display
        if self._live:
            self._live.update(self._render_display())

    def _render_frame(self, pixels: np.ndarray, dimensions: tuple[int, ...]) -> Panel:
        """Render a frame to a rich Panel."""
        # Get terminal width
        term_width = self.config.width or self._console.width - 4

        if len(dimensions) == 1:
            # 1D strip
            content = self._render_1d(pixels, dimensions[0], term_width)
        else:
            # 2D matrix
            content = self._render_2d(pixels, dimensions, term_width)

        # Build panel
        if self.config.show_info:
            info = self._render_info()
            content = Text.assemble(content, "\n", info)

        if self.config.show_border:
            return Panel(
                content,
                title=self.config.title,
                subtitle=f"{dimensions[0]}{'x' + str(dimensions[1]) if len(dimensions) > 1 else ''} pixels",
                border_style="blue",
            )
        else:
            return Panel(content, border_style="none")

    def _render_1d(self, pixels: np.ndarray, length: int, width: int) -> Text:
        """Render 1D LED strip."""
        text = Text()

        # Ensure pixels is 2D (pixels, channels)
        if pixels.ndim == 1:
            pixels = pixels.reshape(-1, 3)

        # Scale to fit terminal width
        scale = max(1, length // width) if length > width else 1
        display_width = min(length, width)

        if self.config.style == TerminalStyle.BLOCK:
            for i in range(display_width):
                idx = i * scale
                if idx < len(pixels):
                    r, g, b = pixels[idx][:3]
                    text.append("█", style=f"rgb({r},{g},{b})")
                else:
                    text.append(" ")
        elif self.config.style == TerminalStyle.ASCII:
            for i in range(display_width):
                idx = i * scale
                if idx < len(pixels):
                    brightness = np.mean(pixels[idx][:3]) / 255.0
                    char_idx = int(brightness * (len(self.ASCII_CHARS) - 1))
                    r, g, b = pixels[idx][:3]
                    text.append(
                        self.ASCII_CHARS[char_idx], style=f"rgb({r},{g},{b})"
                    )
                else:
                    text.append(" ")
        elif self.config.style == TerminalStyle.BAR:
            for i in range(display_width):
                idx = i * scale
                if idx < len(pixels):
                    brightness = np.mean(pixels[idx][:3]) / 255.0
                    char_idx = int(brightness * (len(self.BAR_CHARS) - 1))
                    r, g, b = pixels[idx][:3]
                    text.append(self.BAR_CHARS[char_idx], style=f"rgb({r},{g},{b})")
                else:
                    text.append(" ")

        return text

    def _render_2d(
        self, pixels: np.ndarray, dimensions: tuple[int, ...], width: int
    ) -> Text:
        """Render 2D LED matrix."""
        text = Text()
        mat_width, mat_height = dimensions[:2]

        # Ensure pixels is 3D (height, width, channels)
        if pixels.ndim == 2:
            pixels = pixels.reshape(mat_height, mat_width, -1)

        # Calculate scaling
        scale_x = max(1, mat_width // width)
        scale_y = max(1, scale_x // 2)  # Terminal chars are ~2:1 aspect

        display_width = min(mat_width, width)
        display_height = mat_height // scale_y if scale_y > 1 else mat_height

        if self.config.style == TerminalStyle.BLOCK:
            for y in range(display_height):
                for x in range(display_width):
                    px = x * scale_x
                    py = y * scale_y
                    if py < mat_height and px < mat_width:
                        r, g, b = pixels[py, px][:3]
                        text.append("█", style=f"rgb({r},{g},{b})")
                    else:
                        text.append(" ")
                if y < display_height - 1:
                    text.append("\n")
        elif self.config.style == TerminalStyle.BRAILLE:
            # Braille rendering - each char is 2x4 dots
            for y in range(0, display_height, 4):
                for x in range(0, display_width, 2):
                    char, r, g, b = self._braille_char(pixels, x, y, scale_x, scale_y)
                    text.append(char, style=f"rgb({r},{g},{b})")
                if y + 4 < display_height:
                    text.append("\n")
        else:
            # Fallback to block for other styles on 2D
            return self._render_2d_block(pixels, dimensions, width)

        return text

    def _render_2d_block(
        self, pixels: np.ndarray, dimensions: tuple[int, ...], width: int
    ) -> Text:
        """Render 2D using block characters with half-height blocks."""
        text = Text()
        mat_width, mat_height = dimensions[:2]

        if pixels.ndim == 2:
            pixels = pixels.reshape(mat_height, mat_width, -1)

        scale = max(1, mat_width // width)
        display_width = min(mat_width, width)

        # Use ▀ (upper half block) to show two rows per line
        for y in range(0, mat_height, 2):
            for x in range(display_width):
                px = x * scale
                if px < mat_width:
                    # Top pixel
                    if y < mat_height:
                        r1, g1, b1 = pixels[y, px][:3]
                    else:
                        r1, g1, b1 = 0, 0, 0
                    # Bottom pixel
                    if y + 1 < mat_height:
                        r2, g2, b2 = pixels[y + 1, px][:3]
                    else:
                        r2, g2, b2 = 0, 0, 0

                    # Use ▀ with foreground=top, background=bottom
                    text.append(
                        "▀",
                        style=f"rgb({r1},{g1},{b1}) on rgb({r2},{g2},{b2})",
                    )
                else:
                    text.append(" ")
            if y + 2 < mat_height:
                text.append("\n")

        return text

    def _braille_char(
        self,
        pixels: np.ndarray,
        x: int,
        y: int,
        scale_x: int,
        scale_y: int,
    ) -> tuple[str, int, int, int]:
        """Generate a braille character for a 2x4 region."""
        # Braille dot positions:
        # 0 3
        # 1 4
        # 2 5
        # 6 7
        dots = [
            (0, 0),
            (0, 1),
            (0, 2),
            (1, 0),
            (1, 1),
            (1, 2),
            (0, 3),
            (1, 3),
        ]

        code = 0x2800  # Braille base
        total_r, total_g, total_b = 0, 0, 0
        count = 0

        for i, (dx, dy) in enumerate(dots):
            px = (x + dx) * scale_x
            py = (y + dy) * scale_y
            if py < pixels.shape[0] and px < pixels.shape[1]:
                r, g, b = pixels[py, px][:3]
                brightness = (int(r) + int(g) + int(b)) / 3
                if brightness > 64:  # Threshold
                    code |= 1 << i
                total_r += r
                total_g += g
                total_b += b
                count += 1

        if count > 0:
            total_r //= count
            total_g //= count
            total_b //= count

        return chr(code), total_r, total_g, total_b

    def _render_info(self) -> Text:
        """Render info bar."""
        text = Text()
        text.append(f"FPS: {self._fps:.1f}", style="cyan")
        text.append(" | ", style="dim")
        text.append(f"Frames: {self._frame_count}", style="green")
        text.append(" | ", style="dim")

        # Data rate
        if self._data_rate > 1024 * 1024:
            rate_str = f"{self._data_rate / 1024 / 1024:.1f} MB/s"
        elif self._data_rate > 1024:
            rate_str = f"{self._data_rate / 1024:.1f} KB/s"
        else:
            rate_str = f"{self._data_rate:.0f} B/s"
        text.append(f"Data: {rate_str}", style="yellow")

        return text

    def get_stats(self) -> dict[str, Any]:
        """Get renderer statistics."""
        stats = super().get_stats()
        stats.update(
            {
                "style": self.config.style.value,
                "data_rate": self._data_rate,
                "bytes_received": self._bytes_received,
            }
        )
        return stats
