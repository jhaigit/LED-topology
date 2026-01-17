"""Static image input."""

import logging
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ltp_media_source.inputs.base import MediaInput, FitMode

logger = logging.getLogger(__name__)


class ImageInput(MediaInput):
    """Static image input (PNG, JPG, BMP, WebP, etc.)."""

    input_type = "image"

    def __init__(
        self,
        path: str,
        fit_mode: FitMode = FitMode.CONTAIN,
        **kwargs: Any,
    ):
        """Initialize image input.

        Args:
            path: Path to image file
            fit_mode: How to fit image to target dimensions
        """
        super().__init__(path=path, fit_mode=fit_mode, loop=True, **kwargs)

        self._image: np.ndarray | None = None
        self._width = 0
        self._height = 0

    def open(self) -> None:
        """Load the image file."""
        if self._opened:
            return

        if not self.path:
            raise ValueError("No path specified")

        path = Path(self.path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {self.path}")

        try:
            with Image.open(path) as img:
                # Convert to RGB
                if img.mode == "RGBA":
                    # Composite onto black background
                    background = Image.new("RGB", img.size, (0, 0, 0))
                    background.paste(img, mask=img.split()[3])
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                self._image = np.array(img)
                self._height, self._width = self._image.shape[:2]

            self._opened = True
            logger.info(f"Opened image: {self.path} ({self._width}x{self._height})")

        except Exception as e:
            raise RuntimeError(f"Failed to load image: {e}") from e

    def read_frame(self) -> np.ndarray | None:
        """Return the static image.

        For static images, always returns the same frame.
        """
        if not self._opened or self._image is None:
            return None

        self._frame_index += 1
        return self._image.copy()

    def close(self) -> None:
        """Release image resources."""
        self._image = None
        self._opened = False
        logger.debug(f"Closed image: {self.path}")

    @property
    def frame_rate(self) -> float:
        """Static images have no native frame rate."""
        return 30.0  # Default output rate

    @property
    def duration(self) -> float | None:
        """Static images have infinite duration."""
        return None  # Treated as infinite/live

    @property
    def native_dimensions(self) -> tuple[int, int]:
        """Return image dimensions."""
        return (self._width, self._height)
