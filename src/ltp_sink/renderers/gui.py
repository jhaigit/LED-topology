"""Pygame-based GUI renderer for LTP Sink."""

import logging
import threading
import time
from typing import Any, Callable

import numpy as np
from ltp_sink.renderers.base import Renderer, RendererConfig

logger = logging.getLogger(__name__)

try:
    import pygame
    import pygame.freetype

    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False


# Colors
COLOR_BG = (24, 24, 30)
COLOR_PANEL_BG = (32, 32, 40)
COLOR_TEXT = (200, 200, 210)
COLOR_TEXT_DIM = (120, 120, 135)
COLOR_TEXT_LABEL = (150, 155, 170)
COLOR_TEXT_VALUE = (220, 225, 240)
COLOR_ACCENT = (80, 140, 220)
COLOR_GOOD = (80, 200, 120)
COLOR_WARN = (220, 180, 60)
COLOR_BTN_IDLE = (55, 55, 70)
COLOR_BTN_HOVER = (70, 70, 90)
COLOR_BTN_ACTIVE = (80, 140, 220)
COLOR_BTN_TEXT = (220, 220, 230)
COLOR_SWITCH_ON = (80, 200, 120)
COLOR_SWITCH_OFF = (70, 70, 85)
COLOR_PIXEL_GAP = (18, 18, 22)
COLOR_BORDER = (50, 50, 65)


class GuiConfig(RendererConfig):
    """Configuration for the GUI renderer."""

    type: str = "gui"
    pixel_size: int = 0  # 0 = auto-fit
    gap: int = 1
    title: str = "LTP Sink"
    font_size: int = 14
    stats_update_ms: int = 500
    min_window_width: int = 640
    min_window_height: int = 400


class GuiRenderer(Renderer):
    """Pygame-based GUI renderer with pixel display, inputs, and statistics."""

    def __init__(self, config: GuiConfig | None = None):
        if not HAS_PYGAME:
            raise ImportError(
                "pygame is required for the GUI renderer. "
                "Install it with: pip install pygame"
            )
        super().__init__(config or GuiConfig())
        self.config: GuiConfig = self.config

        # Thread-safe frame handoff
        self._lock = threading.Lock()
        self._latest_pixels: np.ndarray | None = None
        self._latest_dimensions: tuple[int, ...] | None = None
        self._has_new_frame = False

        # Callbacks
        self._stats_provider: Callable[[], dict[str, Any]] | None = None
        self._input_callback: Callable[[int, Any], None] | None = None
        self._input_defs: list[dict[str, str]] = []
        self._input_states: dict[int, bool] = {}

        # Pygame thread
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # FPS tracking
        self._last_frame_time = 0.0
        self._fps_samples: list[float] = []

        # Stats cache
        self._cached_stats: dict[str, Any] = {}
        self._last_stats_time = 0.0

    def set_stats_provider(self, fn: Callable[[], dict[str, Any]]) -> None:
        self._stats_provider = fn

    def set_input_callback(self, cb: Callable[[int, Any], None]) -> None:
        self._input_callback = cb

    def set_input_defs(self, defs: list[dict[str, str]]) -> None:
        self._input_defs = list(defs)
        self._input_states = {i: False for i in range(len(defs))}

    async def start(self) -> None:
        await super().start()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_pygame, name="gui-renderer", daemon=True
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        await super().stop()

    def render(self, pixels: np.ndarray, dimensions: tuple[int, ...]) -> None:
        super().render(pixels, dimensions)
        now = time.monotonic()
        if self._last_frame_time > 0:
            dt = now - self._last_frame_time
            if dt > 0:
                self._fps_samples.append(1.0 / dt)
                if len(self._fps_samples) > 30:
                    self._fps_samples.pop(0)
                self._fps = sum(self._fps_samples) / len(self._fps_samples)
        self._last_frame_time = now
        with self._lock:
            self._latest_pixels = pixels.copy()
            self._latest_dimensions = dimensions
            self._has_new_frame = True

    def clear(self) -> None:
        with self._lock:
            self._latest_pixels = None
            self._latest_dimensions = None
            self._has_new_frame = True

    # --- Pygame thread ---

    def _run_pygame(self) -> None:
        """Main pygame loop, runs in its own thread."""
        pygame.init()
        pygame.freetype.init()

        init_w = max(self.config.min_window_width, 800)
        init_h = max(self.config.min_window_height, 600)
        screen = pygame.display.set_mode((init_w, init_h), pygame.RESIZABLE)
        pygame.display.set_caption(self.config.title)
        clock = pygame.time.Clock()

        fs = self.config.font_size
        font = pygame.freetype.SysFont("monospace", fs)
        font_small = pygame.freetype.SysFont("monospace", max(8, fs - 2))
        font_label = pygame.freetype.SysFont("monospace", max(8, fs - 1))

        # Button rects (recalculated on resize)
        btn_rects: list[pygame.Rect] = []

        while not self._stop_event.is_set():
            # --- Events ---
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._stop_event.set()
                    break
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self._handle_click(event.pos, btn_rects)

            if self._stop_event.is_set():
                break

            # Query current window size every frame (handles resize reliably)
            screen = pygame.display.get_surface()
            win_w, win_h = screen.get_size()

            # --- Fetch latest data ---
            with self._lock:
                pixels = self._latest_pixels
                dimensions = self._latest_dimensions
                self._has_new_frame = False

            # Update stats periodically
            now = time.monotonic()
            if now - self._last_stats_time > self.config.stats_update_ms / 1000.0:
                if self._stats_provider:
                    self._cached_stats = self._stats_provider()
                self._last_stats_time = now

            # --- Draw ---
            screen.fill(COLOR_BG)

            # Layout: pixel area (top), inputs (middle), stats (bottom)
            margin = 10
            stats_height = self._calc_stats_height(fs)
            input_height = self._calc_input_height() if self._input_defs else 0

            pixel_area = pygame.Rect(
                margin,
                margin,
                win_w - 2 * margin,
                win_h - 2 * margin - stats_height - input_height
                - (margin if input_height else 0)
                - margin,
            )
            pixel_area.height = max(pixel_area.height, 60)

            self._draw_pixel_area(screen, pixel_area, pixels, dimensions, font)

            y_cursor = pixel_area.bottom + margin

            if self._input_defs:
                input_area = pygame.Rect(
                    margin, y_cursor, win_w - 2 * margin, input_height
                )
                btn_rects = self._draw_input_panel(
                    screen, input_area, font_label
                )
                y_cursor = input_area.bottom + margin
            else:
                btn_rects = []

            stats_area = pygame.Rect(
                margin, y_cursor, win_w - 2 * margin, stats_height
            )
            self._draw_stats_panel(screen, stats_area, font_small)

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()

    # --- Drawing helpers ---

    def _draw_pixel_area(
        self,
        screen: "pygame.Surface",
        area: "pygame.Rect",
        pixels: np.ndarray | None,
        dimensions: tuple[int, ...] | None,
        font: "pygame.freetype.Font",
    ) -> None:
        """Draw the pixel display area."""
        pygame.draw.rect(screen, COLOR_PANEL_BG, area, border_radius=6)
        pygame.draw.rect(screen, COLOR_BORDER, area, width=1, border_radius=6)

        if pixels is None or dimensions is None:
            text = "Waiting for data..."
            surf, rect = font.render(text, COLOR_TEXT_DIM)
            screen.blit(
                surf,
                (
                    area.centerx - rect.width // 2,
                    area.centery - rect.height // 2,
                ),
            )
            return

        # Ensure flat (N, channels)
        if pixels.ndim == 1:
            pixels = pixels.reshape(-1, 3)

        pad = 4  # inner padding
        inner = area.inflate(-pad * 2, -pad * 2)

        if len(dimensions) == 1:
            self._draw_1d(screen, inner, pixels, dimensions[0])
        else:
            self._draw_2d(screen, inner, pixels, dimensions)

    def _draw_1d(
        self,
        screen: "pygame.Surface",
        area: "pygame.Rect",
        pixels: np.ndarray,
        length: int,
    ) -> None:
        """Draw 1D pixel strip."""
        gap = self.config.gap
        if self.config.pixel_size > 0:
            px_w = self.config.pixel_size
        else:
            px_w = max(2, (area.width - gap * (length - 1)) // length)

        total_w = px_w * length + gap * (length - 1)
        start_x = area.x + max(0, (area.width - total_w) // 2)
        px_h = min(area.height, max(8, area.height // 2))
        start_y = area.y + (area.height - px_h) // 2

        for i in range(length):
            if i >= len(pixels):
                break
            r, g, b = int(pixels[i][0]), int(pixels[i][1]), int(pixels[i][2])
            x = start_x + i * (px_w + gap)
            if x + px_w > area.right:
                break
            pygame.draw.rect(screen, (r, g, b), (x, start_y, px_w, px_h))

    def _draw_2d(
        self,
        screen: "pygame.Surface",
        area: "pygame.Rect",
        pixels: np.ndarray,
        dimensions: tuple[int, ...],
    ) -> None:
        """Draw 2D pixel matrix."""
        mat_w, mat_h = dimensions[0], dimensions[1]
        gap = self.config.gap

        if pixels.ndim == 2:
            pixels = pixels.reshape(mat_h, mat_w, -1)

        if self.config.pixel_size > 0:
            px_size = self.config.pixel_size
        else:
            px_w = max(2, (area.width - gap * (mat_w - 1)) // mat_w)
            px_h = max(2, (area.height - gap * (mat_h - 1)) // mat_h)
            px_size = min(px_w, px_h)

        total_w = px_size * mat_w + gap * (mat_w - 1)
        total_h = px_size * mat_h + gap * (mat_h - 1)
        start_x = area.x + max(0, (area.width - total_w) // 2)
        start_y = area.y + max(0, (area.height - total_h) // 2)

        for y in range(mat_h):
            for x in range(mat_w):
                if y >= pixels.shape[0] or x >= pixels.shape[1]:
                    continue
                r, g, b = (
                    int(pixels[y, x][0]),
                    int(pixels[y, x][1]),
                    int(pixels[y, x][2]),
                )
                px_x = start_x + x * (px_size + gap)
                px_y = start_y + y * (px_size + gap)
                if px_x + px_size > area.right or px_y + px_size > area.bottom:
                    continue
                pygame.draw.rect(
                    screen, (r, g, b), (px_x, px_y, px_size, px_size)
                )

    def _calc_input_height(self) -> int:
        if not self._input_defs:
            return 0
        fs = self.config.font_size
        return fs + max(20, fs)

    def _draw_input_panel(
        self,
        screen: "pygame.Surface",
        area: "pygame.Rect",
        font: "pygame.freetype.Font",
    ) -> list["pygame.Rect"]:
        """Draw input buttons/switches. Returns button rects for click detection."""
        pygame.draw.rect(screen, COLOR_PANEL_BG, area, border_radius=6)
        pygame.draw.rect(screen, COLOR_BORDER, area, width=1, border_radius=6)

        n = len(self._input_defs)
        if n == 0:
            return []

        # Measure each label to size buttons from content
        pad_x = max(12, self.config.font_size)
        gap = max(8, self.config.font_size // 2)
        labels = []
        max_label_w = 0
        for inp in self._input_defs:
            label = inp.get("name", "Input")
            itype = inp.get("type", "button")
            # Account for " ON" suffix on switches
            measure = label + " ON" if itype == "switch" else label
            label_rect = font.get_rect(measure)
            max_label_w = max(max_label_w, label_rect.width)
            labels.append(label)

        btn_w = max_label_w + pad_x * 2
        btn_h = font.get_sized_height() + pad_x
        total_w = btn_w * n + gap * (n - 1)

        # Shrink if doesn't fit
        if total_w > area.width - 20:
            btn_w = max(40, (area.width - 20 - gap * (n - 1)) // n)
            total_w = btn_w * n + gap * (n - 1)

        start_x = area.x + (area.width - total_w) // 2
        start_y = area.y + (area.height - btn_h) // 2

        rects = []
        mouse_pos = pygame.mouse.get_pos()

        for i, inp in enumerate(self._input_defs):
            x = start_x + i * (btn_w + gap)
            rect = pygame.Rect(x, start_y, btn_w, btn_h)
            rects.append(rect)

            itype = inp.get("type", "button")
            active = self._input_states.get(i, False)

            if itype == "switch":
                color = COLOR_SWITCH_ON if active else COLOR_SWITCH_OFF
            elif active:
                color = COLOR_BTN_ACTIVE
            elif rect.collidepoint(mouse_pos):
                color = COLOR_BTN_HOVER
            else:
                color = COLOR_BTN_IDLE

            pygame.draw.rect(screen, color, rect, border_radius=4)

            label = labels[i]
            if itype == "switch" and active:
                label += " ON"
            surf, text_rect = font.render(label, COLOR_BTN_TEXT)
            screen.blit(
                surf,
                (
                    rect.centerx - text_rect.width // 2,
                    rect.centery - text_rect.height // 2,
                ),
            )

        return rects

    def _handle_click(
        self, pos: tuple[int, int], btn_rects: list["pygame.Rect"]
    ) -> None:
        """Handle mouse click on input buttons."""
        for i, rect in enumerate(btn_rects):
            if rect.collidepoint(pos):
                if i >= len(self._input_defs):
                    break
                itype = self._input_defs[i].get("type", "button")
                if itype == "switch":
                    new_val = not self._input_states.get(i, False)
                    self._input_states[i] = new_val
                    if self._input_callback:
                        self._input_callback(i, new_val)
                else:
                    # Button: momentary press
                    self._input_states[i] = True
                    if self._input_callback:
                        self._input_callback(i, True)
                    # Schedule release after brief visual feedback
                    threading.Timer(
                        0.15, self._release_button, args=(i,)
                    ).start()
                break

    def _release_button(self, input_id: int) -> None:
        self._input_states[input_id] = False

    def _calc_stats_height(self, font_size: int) -> int:
        rows = 5
        line_h = font_size + max(4, font_size // 3)
        return rows * line_h + 16

    def _draw_stats_panel(
        self,
        screen: "pygame.Surface",
        area: "pygame.Rect",
        font: "pygame.freetype.Font",
    ) -> None:
        """Draw statistics panel."""
        pygame.draw.rect(screen, COLOR_PANEL_BG, area, border_radius=6)
        pygame.draw.rect(screen, COLOR_BORDER, area, width=1, border_radius=6)

        s = self._cached_stats
        if not s:
            surf, _ = font.render("No stats yet", COLOR_TEXT_DIM)
            screen.blit(surf, (area.x + 10, area.y + 10))
            return

        line_h = font.get_sized_height() + 4
        col_w = area.width // 2
        x1 = area.x + 12
        x2 = area.x + col_w + 12
        y = area.y + 8

        # Measure the widest label to set the value column offset
        all_labels = [
            "Jitter min/avg/max:", "Data Packets:", "Packets/sec:",
            "Data Bytes:", "Throughput:", "FPS:", "Frames:",
            "Jitter stddev:", "Control msgs:", "Streams:",
        ]
        val_offset = max(font.get_rect(lb).width for lb in all_labels) + 8

        def draw_stat(x: int, y_pos: int, label: str, value: str,
                       val_color: tuple[int, int, int] = COLOR_TEXT_VALUE) -> None:
            surf_l, _ = font.render(label, COLOR_TEXT_LABEL)
            screen.blit(surf_l, (x, y_pos))
            surf_v, _ = font.render(value, val_color)
            screen.blit(surf_v, (x + val_offset, y_pos))

        # Row 1
        draw_stat(x1, y, "Data Packets:", f"{s.get('data_packets', 0):,}")
        draw_stat(x2, y, "Packets/sec:", f"{s.get('packets_per_sec', 0):.1f}")
        y += line_h

        # Row 2
        bytes_total = s.get("data_bytes", 0)
        if bytes_total > 1_000_000:
            bytes_str = f"{bytes_total / 1_000_000:.1f} MB"
        elif bytes_total > 1_000:
            bytes_str = f"{bytes_total / 1_000:.1f} KB"
        else:
            bytes_str = f"{bytes_total} B"

        bps = s.get("bytes_per_sec", 0)
        if bps > 1_000_000:
            bps_str = f"{bps / 1_000_000:.1f} MB/s"
        elif bps > 1_000:
            bps_str = f"{bps / 1_000:.1f} KB/s"
        else:
            bps_str = f"{bps:.0f} B/s"

        draw_stat(x1, y, "Data Bytes:", bytes_str)
        draw_stat(x2, y, "Throughput:", bps_str)
        y += line_h

        # Row 3
        fps = s.get("fps", 0)
        fps_color = COLOR_GOOD if fps > 10 else (COLOR_WARN if fps > 0 else COLOR_TEXT_DIM)
        draw_stat(x1, y, "Frames:", f"{s.get('frame_count', 0):,}")
        draw_stat(x2, y, "FPS:", f"{fps:.1f}", fps_color)
        y += line_h

        # Row 4 - Jitter
        jitter_avg = s.get("jitter_avg_ms", 0)
        jitter_str = (
            f"{s.get('jitter_min_ms', 0):.1f} / {jitter_avg:.1f} / "
            f"{s.get('jitter_max_ms', 0):.1f} ms"
        )
        draw_stat(x1, y, "Jitter min/avg/max:", jitter_str)
        draw_stat(x2, y, "Jitter stddev:", f"{s.get('jitter_stddev_ms', 0):.2f} ms")
        y += line_h

        # Row 5 - Control & Sequence
        ctrl_str = f"rx:{s.get('control_rx', 0)} tx:{s.get('control_tx', 0)}"
        draw_stat(x1, y, "Control msgs:", ctrl_str)

        seq_errors = s.get("seq_gaps", 0) + s.get("seq_duplicates", 0) + s.get("seq_out_of_order", 0)
        seq_color = COLOR_GOOD if seq_errors == 0 else COLOR_WARN
        streams_str = f"{s.get('active_streams', 0)} active / {s.get('total_streams', 0)} total"
        draw_stat(x2, y, "Streams:", streams_str)

    def get_stats(self) -> dict[str, Any]:
        stats = super().get_stats()
        stats["renderer"] = "gui"
        return stats
