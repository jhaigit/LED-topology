"""Screen capture input."""

import logging
from typing import Any

import numpy as np

from ltp_media_source.inputs.base import MediaInput, FitMode

logger = logging.getLogger(__name__)

try:
    import mss
    import mss.tools
    HAS_MSS = True
except ImportError:
    HAS_MSS = False
    logger.debug("mss not available - screen capture disabled")


class ScreenInput(MediaInput):
    """Screen capture input using mss."""

    input_type = "screen"

    def __init__(
        self,
        monitor: int = 0,
        region: tuple[int, int, int, int] | None = None,
        fit_mode: FitMode = FitMode.CONTAIN,
        fps: int = 30,
        **kwargs: Any,
    ):
        """Initialize screen capture.

        Args:
            monitor: Monitor index (0 = all monitors, 1 = first, etc.)
            region: Capture region as (x, y, width, height), None = full monitor
            fit_mode: How to fit frames to target dimensions
            fps: Target frame rate
        """
        if not HAS_MSS:
            raise RuntimeError("mss required for screen capture. Install: pip install mss")

        super().__init__(path=f"screen:{monitor}", fit_mode=fit_mode, loop=True, **kwargs)

        self.monitor_index = monitor
        self.region = region
        self.target_fps = fps

        self._sct: Any = None  # mss.mss instance
        self._monitor_info: dict = {}
        self._width = 0
        self._height = 0

    def open(self) -> None:
        """Initialize screen capture."""
        if self._opened:
            return

        try:
            self._sct = mss.mss()

            # Get monitor info
            monitors = self._sct.monitors

            if self.monitor_index >= len(monitors):
                raise ValueError(
                    f"Monitor {self.monitor_index} not found. "
                    f"Available: 0-{len(monitors)-1}"
                )

            self._monitor_info = monitors[self.monitor_index].copy()

            # Apply region if specified
            if self.region:
                x, y, w, h = self.region
                self._monitor_info = {
                    "left": self._monitor_info["left"] + x,
                    "top": self._monitor_info["top"] + y,
                    "width": w,
                    "height": h,
                }

            self._width = self._monitor_info["width"]
            self._height = self._monitor_info["height"]

            self._opened = True
            logger.info(
                f"Opened screen capture: monitor {self.monitor_index} "
                f"({self._width}x{self._height})"
            )

        except Exception as e:
            if self._sct:
                self._sct.close()
                self._sct = None
            raise RuntimeError(f"Failed to initialize screen capture: {e}") from e

    def read_frame(self) -> np.ndarray | None:
        """Capture current screen frame."""
        if not self._opened or self._sct is None:
            return None

        try:
            # Capture screen region
            screenshot = self._sct.grab(self._monitor_info)

            # Convert to numpy array (BGRA format)
            frame = np.array(screenshot)

            # Convert BGRA to RGB
            frame_rgb = frame[:, :, [2, 1, 0]]  # Reorder channels

            self._frame_index += 1

            return frame_rgb

        except Exception as e:
            logger.error(f"Screen capture failed: {e}")
            return None

    def close(self) -> None:
        """Close screen capture."""
        if self._sct:
            self._sct.close()
            self._sct = None
        self._opened = False
        logger.debug("Closed screen capture")

    @property
    def frame_rate(self) -> float:
        """Target frame rate."""
        return float(self.target_fps)

    @property
    def duration(self) -> float | None:
        """Screen capture is a live source with no duration."""
        return None

    @property
    def native_dimensions(self) -> tuple[int, int]:
        """Capture region dimensions."""
        return (self._width, self._height)

    @property
    def is_live(self) -> bool:
        """Screen capture is always live."""
        return True

    def set_region(self, x: int, y: int, width: int, height: int) -> None:
        """Update capture region dynamically.

        Args:
            x, y: Top-left corner
            width, height: Region size
        """
        self.region = (x, y, width, height)

        if self._opened and self._sct:
            monitors = self._sct.monitors
            base_monitor = monitors[self.monitor_index]

            self._monitor_info = {
                "left": base_monitor["left"] + x,
                "top": base_monitor["top"] + y,
                "width": width,
                "height": height,
            }

            self._width = width
            self._height = height

            logger.debug(f"Updated capture region: {self.region}")
