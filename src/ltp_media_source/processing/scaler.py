"""Frame scaling and fit mode handling."""

import logging
from typing import Tuple

import numpy as np

from ltp_media_source.inputs.base import FitMode

logger = logging.getLogger(__name__)

# Try to import OpenCV, fall back to PIL-based scaling
try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False
    from PIL import Image

    logger.debug("OpenCV not available, using PIL for scaling")


class FrameScaler:
    """Scales frames to target dimensions with various fit modes."""

    def __init__(
        self,
        target_width: int,
        target_height: int,
        fit_mode: FitMode = FitMode.CONTAIN,
        background: Tuple[int, int, int] = (0, 0, 0),
    ):
        """Initialize the scaler.

        Args:
            target_width: Target width in pixels
            target_height: Target height in pixels
            fit_mode: How to fit content to target
            background: Background color for letterboxing (RGB)
        """
        self.target_width = target_width
        self.target_height = target_height
        self.fit_mode = fit_mode
        self.background = background

        # Pre-allocate output buffer
        self._output_buffer = np.zeros(
            (target_height, target_width, 3), dtype=np.uint8
        )
        if background != (0, 0, 0):
            self._output_buffer[:] = background

    def scale(self, frame: np.ndarray) -> np.ndarray:
        """Scale frame to target dimensions.

        Args:
            frame: Input frame (H, W, 3) or (H, W, 4) RGB/RGBA uint8

        Returns:
            Scaled frame (target_height, target_width, 3) RGB uint8
        """
        if frame is None or frame.size == 0:
            return self._output_buffer.copy()

        # Handle RGBA by dropping alpha
        if len(frame.shape) == 3 and frame.shape[2] == 4:
            frame = frame[:, :, :3]

        # Handle grayscale
        if len(frame.shape) == 2:
            frame = np.stack([frame, frame, frame], axis=-1)

        h, w = frame.shape[:2]
        tw, th = self.target_width, self.target_height

        # Fast path: already correct size
        if w == tw and h == th:
            return frame.copy()

        if self.fit_mode == FitMode.CONTAIN:
            return self._scale_contain(frame, w, h, tw, th)
        elif self.fit_mode == FitMode.COVER:
            return self._scale_cover(frame, w, h, tw, th)
        elif self.fit_mode == FitMode.STRETCH:
            return self._scale_stretch(frame, tw, th)
        elif self.fit_mode == FitMode.TILE:
            return self._scale_tile(frame, w, h, tw, th)
        elif self.fit_mode == FitMode.CENTER:
            return self._scale_center(frame, w, h, tw, th)
        else:
            return self._scale_contain(frame, w, h, tw, th)

    def _scale_contain(
        self, frame: np.ndarray, w: int, h: int, tw: int, th: int
    ) -> np.ndarray:
        """Scale to fit within bounds, preserve aspect, letterbox."""
        scale = min(tw / w, th / h)
        new_w = int(w * scale)
        new_h = int(h * scale)

        scaled = self._resize(frame, new_w, new_h)

        # Create output with background
        result = np.full((th, tw, 3), self.background, dtype=np.uint8)

        # Center the scaled image
        x_off = (tw - new_w) // 2
        y_off = (th - new_h) // 2
        result[y_off : y_off + new_h, x_off : x_off + new_w] = scaled

        return result

    def _scale_cover(
        self, frame: np.ndarray, w: int, h: int, tw: int, th: int
    ) -> np.ndarray:
        """Scale to fill, preserve aspect, crop edges."""
        scale = max(tw / w, th / h)
        new_w = int(w * scale)
        new_h = int(h * scale)

        scaled = self._resize(frame, new_w, new_h)

        # Center crop to target size
        x_off = (new_w - tw) // 2
        y_off = (new_h - th) // 2

        return scaled[y_off : y_off + th, x_off : x_off + tw].copy()

    def _scale_stretch(self, frame: np.ndarray, tw: int, th: int) -> np.ndarray:
        """Stretch to exact size, ignore aspect ratio."""
        return self._resize(frame, tw, th)

    def _scale_tile(
        self, frame: np.ndarray, w: int, h: int, tw: int, th: int
    ) -> np.ndarray:
        """Tile the image to fill the target."""
        result = np.zeros((th, tw, 3), dtype=np.uint8)

        # Tile across the target
        for y in range(0, th, h):
            for x in range(0, tw, w):
                # Calculate the region to copy
                copy_h = min(h, th - y)
                copy_w = min(w, tw - x)
                result[y : y + copy_h, x : x + copy_w] = frame[:copy_h, :copy_w]

        return result

    def _scale_center(
        self, frame: np.ndarray, w: int, h: int, tw: int, th: int
    ) -> np.ndarray:
        """Center without scaling, crop or pad as needed."""
        result = np.full((th, tw, 3), self.background, dtype=np.uint8)

        # Calculate offsets for centering
        src_x = max(0, (w - tw) // 2)
        src_y = max(0, (h - th) // 2)
        dst_x = max(0, (tw - w) // 2)
        dst_y = max(0, (th - h) // 2)

        # Calculate copy dimensions
        copy_w = min(w, tw)
        copy_h = min(h, th)

        result[dst_y : dst_y + copy_h, dst_x : dst_x + copy_w] = frame[
            src_y : src_y + copy_h, src_x : src_x + copy_w
        ]

        return result

    def _resize(self, frame: np.ndarray, width: int, height: int) -> np.ndarray:
        """Resize frame using available backend."""
        if width <= 0 or height <= 0:
            return np.zeros((max(1, height), max(1, width), 3), dtype=np.uint8)

        if HAS_OPENCV:
            # Use INTER_AREA for downscaling (best quality)
            # Use INTER_LINEAR for upscaling
            h, w = frame.shape[:2]
            if width < w or height < h:
                interp = cv2.INTER_AREA
            else:
                interp = cv2.INTER_LINEAR

            return cv2.resize(frame, (width, height), interpolation=interp)
        else:
            # PIL fallback
            img = Image.fromarray(frame)
            resized = img.resize((width, height), Image.Resampling.LANCZOS)
            return np.array(resized)

    def set_fit_mode(self, fit_mode: FitMode) -> None:
        """Change the fit mode."""
        self.fit_mode = fit_mode

    def set_background(self, color: Tuple[int, int, int]) -> None:
        """Change the background color."""
        self.background = color
