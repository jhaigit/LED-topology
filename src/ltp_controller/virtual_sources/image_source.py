"""Image matrix source - displays images with viewport panning.

Loads an image file and renders a configurable viewport (sub-rectangle)
onto the LED matrix.  The viewport can be positioned manually or set to
auto-pan across the image.
"""

import io
import logging
import math
import os
from pathlib import Path
from typing import Any

import numpy as np

from libltp import NumberControl, BooleanControl, EnumControl

from ltp_controller.virtual_sources.base import VirtualSource, VirtualSourceConfig

logger = logging.getLogger(__name__)

try:
    from PIL import Image as PILImage

    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    logger.debug("PIL not available - ImageSource will show fallback pattern")


class ImageSource(VirtualSource):
    """Displays an image with a movable viewport window.

    Load an image from a file path or upload, select a sub-rectangle
    to display, and optionally auto-pan the viewport across the image.
    """

    source_type = "image"

    def __init__(self, config: VirtualSourceConfig | None = None):
        # Image state (before super().__init__ which calls _setup_controls)
        self._image: np.ndarray | None = None  # Full image as RGB array
        self._image_width = 0
        self._image_height = 0
        self._image_path = ""
        super().__init__(config)

        # Load image from saved path if present
        saved_path = self.config.control_values.get("_image_path", "")
        if saved_path and os.path.isfile(saved_path):
            self._load_image_file(saved_path)

    def _setup_controls(self) -> None:
        # Viewport position (as fraction of image, 0.0-1.0)
        self._controls.register(
            NumberControl(
                id="viewport_x",
                name="Viewport X",
                description="Viewport left edge (0.0-1.0)",
                value=0.0,
                min=0.0,
                max=1.0,
                step=0.01,
                group="viewport",
            )
        )
        self._controls.register(
            NumberControl(
                id="viewport_y",
                name="Viewport Y",
                description="Viewport top edge (0.0-1.0)",
                value=0.0,
                min=0.0,
                max=1.0,
                step=0.01,
                group="viewport",
            )
        )
        self._controls.register(
            NumberControl(
                id="zoom",
                name="Zoom",
                description="Viewport zoom (1.0 = fit image, higher = zoom in)",
                value=1.0,
                min=1.0,
                max=20.0,
                step=0.1,
                group="viewport",
            )
        )

        # Auto-pan
        self._controls.register(
            EnumControl(
                id="pan_mode",
                name="Pan Mode",
                description="Auto-pan direction",
                value="none",
                options=[
                    {"value": "none", "label": "None"},
                    {"value": "horizontal", "label": "Horizontal"},
                    {"value": "vertical", "label": "Vertical"},
                    {"value": "diagonal", "label": "Diagonal"},
                    {"value": "bounce_h", "label": "Bounce H"},
                    {"value": "bounce_v", "label": "Bounce V"},
                    {"value": "bounce_diag", "label": "Bounce Diag"},
                    {"value": "circular", "label": "Circular"},
                ],
                group="pan",
            )
        )
        self._controls.register(
            NumberControl(
                id="pan_speed",
                name="Pan Speed",
                description="Pan speed (pixels per second in image space)",
                value=10.0,
                min=0.5,
                max=200.0,
                step=0.5,
                group="pan",
            )
        )

        # Scaling
        self._controls.register(
            EnumControl(
                id="fit_mode",
                name="Fit Mode",
                description="How to fit viewport to output",
                value="contain",
                options=[
                    {"value": "contain", "label": "Contain"},
                    {"value": "cover", "label": "Cover"},
                    {"value": "stretch", "label": "Stretch"},
                    {"value": "nearest", "label": "Nearest (pixelated)"},
                ],
                group="display",
            )
        )
        self._controls.register(
            BooleanControl(
                id="tile",
                name="Tile",
                description="Tile image to fill viewport (for pan wrap-around)",
                value=False,
                group="display",
            )
        )

    def set_data(self, data: Any) -> None:
        """Accept image data via API.

        data can be:
        - {"path": "/path/to/image.png"} - load from filesystem
        - {"image_bytes": bytes} - raw image bytes (set internally)
        """
        if isinstance(data, dict):
            if "path" in data:
                self._load_image_file(data["path"])
            elif "image_bytes" in data:
                self._load_image_bytes(data["image_bytes"])

    def load_image_bytes(self, image_bytes: bytes) -> bool:
        """Load image from raw bytes. Called by API endpoint."""
        return self._load_image_bytes(image_bytes)

    def _load_image_file(self, path: str) -> bool:
        """Load an image from a filesystem path."""
        if not HAS_PIL:
            logger.error("PIL not available, cannot load image")
            return False

        path = str(Path(path).expanduser().resolve())
        if not os.path.isfile(path):
            logger.error(f"Image file not found: {path}")
            return False

        try:
            img = PILImage.open(path)
            self._store_image(img)
            self._image_path = path
            self.config.control_values["_image_path"] = path
            logger.info(
                f"Loaded image: {path} ({self._image_width}x{self._image_height})"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to load image {path}: {e}")
            return False

    def _load_image_bytes(self, image_bytes: bytes) -> bool:
        """Load an image from raw bytes."""
        if not HAS_PIL:
            logger.error("PIL not available, cannot load image")
            return False

        try:
            img = PILImage.open(io.BytesIO(image_bytes))
            self._store_image(img)
            self._image_path = ""
            logger.info(
                f"Loaded image from bytes ({self._image_width}x{self._image_height})"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to load image from bytes: {e}")
            return False

    def _store_image(self, img: "PILImage.Image") -> None:
        """Convert PIL image to numpy RGB array."""
        # Handle animated GIFs - use first frame
        if hasattr(img, "n_frames") and img.n_frames > 1:
            img.seek(0)

        # Convert to RGB
        if img.mode == "RGBA":
            background = PILImage.new("RGB", img.size, (0, 0, 0))
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        self._image = np.array(img, dtype=np.uint8)
        self._image_height, self._image_width = self._image.shape[:2]

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)

        if self._image is None:
            return self._render_no_image(pixels, num_pixels, time_elapsed)

        # Output dimensions
        dims = self.config.output_dimensions
        if len(dims) >= 2:
            out_w, out_h = dims[0], dims[1]
        else:
            out_w = dims[0]
            out_h = 1

        img_w = self._image_width
        img_h = self._image_height
        zoom = self.get_control("zoom")

        # Viewport size in image pixels (inverse of zoom)
        # At zoom=1.0, viewport covers the whole image
        vp_w = img_w / zoom
        vp_h = img_h / zoom

        # Get viewport position
        pan_mode = self.get_control("pan_mode")
        if pan_mode != "none":
            vp_x, vp_y = self._calc_pan_position(
                time_elapsed, pan_mode, img_w, img_h, vp_w, vp_h
            )
        else:
            # Manual position - viewport_x/y are 0-1 fractions
            max_x = max(0, img_w - vp_w)
            max_y = max(0, img_h - vp_h)
            vp_x = self.get_control("viewport_x") * max_x
            vp_y = self.get_control("viewport_y") * max_y

        # Extract viewport region and resize to output
        tile = self.get_control("tile")
        fit_mode = self.get_control("fit_mode")

        viewport = self._extract_viewport(
            vp_x, vp_y, vp_w, vp_h, tile
        )

        # Resize viewport to output dimensions
        resized = self._resize_viewport(viewport, out_w, out_h, fit_mode)

        # Flatten to pixel array
        flat = resized.reshape(-1, 3)
        count = min(len(flat), num_pixels)
        pixels[:count] = flat[:count]

        return pixels

    def _calc_pan_position(
        self,
        t: float,
        mode: str,
        img_w: int,
        img_h: int,
        vp_w: float,
        vp_h: float,
    ) -> tuple[float, float]:
        """Calculate viewport position for auto-pan modes."""
        speed = self.get_control("pan_speed")
        max_x = max(0.001, img_w - vp_w)
        max_y = max(0.001, img_h - vp_h)

        if mode == "horizontal":
            x = (t * speed) % (img_w if self.get_control("tile") else max_x + vp_w)
            if not self.get_control("tile"):
                x = x - vp_w  # Start with viewport off-screen left
                x = max(0, min(x, max_x))
            return x, self.get_control("viewport_y") * max_y

        elif mode == "vertical":
            y = (t * speed) % (img_h if self.get_control("tile") else max_y + vp_h)
            if not self.get_control("tile"):
                y = y - vp_h
                y = max(0, min(y, max_y))
            return self.get_control("viewport_x") * max_x, y

        elif mode == "diagonal":
            x = (t * speed) % (img_w if self.get_control("tile") else max_x + vp_w)
            y = (t * speed * 0.7) % (img_h if self.get_control("tile") else max_y + vp_h)
            if not self.get_control("tile"):
                x = max(0, min(x - vp_w, max_x))
                y = max(0, min(y - vp_h, max_y))
            return x, y

        elif mode == "bounce_h":
            # Bounce back and forth horizontally
            cycle = max_x * 2
            pos = (t * speed) % cycle if cycle > 0 else 0
            x = pos if pos <= max_x else cycle - pos
            return x, self.get_control("viewport_y") * max_y

        elif mode == "bounce_v":
            cycle = max_y * 2
            pos = (t * speed) % cycle if cycle > 0 else 0
            y = pos if pos <= max_y else cycle - pos
            return self.get_control("viewport_x") * max_x, y

        elif mode == "bounce_diag":
            cycle_x = max_x * 2
            cycle_y = max_y * 2
            pos_x = (t * speed) % cycle_x if cycle_x > 0 else 0
            pos_y = (t * speed * 0.7) % cycle_y if cycle_y > 0 else 0
            x = pos_x if pos_x <= max_x else cycle_x - pos_x
            y = pos_y if pos_y <= max_y else cycle_y - pos_y
            return x, y

        elif mode == "circular":
            cx = max_x / 2
            cy = max_y / 2
            radius_x = max_x / 2
            radius_y = max_y / 2
            angle = t * speed * 0.05
            x = cx + radius_x * math.cos(angle)
            y = cy + radius_y * math.sin(angle)
            return max(0, min(x, max_x)), max(0, min(y, max_y))

        return 0.0, 0.0

    def _extract_viewport(
        self,
        vp_x: float,
        vp_y: float,
        vp_w: float,
        vp_h: float,
        tile: bool,
    ) -> np.ndarray:
        """Extract a viewport region from the image.

        Returns an RGB numpy array of shape (ceil(vp_h), ceil(vp_w), 3).
        If tile is True, wraps around the image edges.
        """
        img = self._image
        img_h, img_w = img.shape[:2]

        out_h = max(1, int(round(vp_h)))
        out_w = max(1, int(round(vp_w)))

        ix_start = int(vp_x)
        iy_start = int(vp_y)

        if tile:
            # Vectorized wrapping with modular indexing
            ys = (iy_start + np.arange(out_h)) % img_h
            xs = (ix_start + np.arange(out_w)) % img_w
            return img[np.ix_(ys, xs)]

        # Non-tiling: clamp and slice
        # Compute valid source and destination ranges
        sx0 = max(0, ix_start)
        sy0 = max(0, iy_start)
        sx1 = min(img_w, ix_start + out_w)
        sy1 = min(img_h, iy_start + out_h)

        if sx0 >= sx1 or sy0 >= sy1:
            return np.zeros((out_h, out_w, 3), dtype=np.uint8)

        dx0 = sx0 - ix_start
        dy0 = sy0 - iy_start
        copy_w = sx1 - sx0
        copy_h = sy1 - sy0

        result = np.zeros((out_h, out_w, 3), dtype=np.uint8)
        result[dy0 : dy0 + copy_h, dx0 : dx0 + copy_w] = img[sy0:sy1, sx0:sx1]
        return result

    def _resize_viewport(
        self,
        viewport: np.ndarray,
        out_w: int,
        out_h: int,
        fit_mode: str,
    ) -> np.ndarray:
        """Resize viewport to output dimensions."""
        vp_h, vp_w = viewport.shape[:2]

        if vp_w == out_w and vp_h == out_h:
            return viewport

        if HAS_PIL:
            return self._resize_pil(viewport, out_w, out_h, fit_mode)

        # Fallback: nearest-neighbor with numpy
        return self._resize_nearest(viewport, out_w, out_h)

    def _resize_pil(
        self,
        viewport: np.ndarray,
        out_w: int,
        out_h: int,
        fit_mode: str,
    ) -> np.ndarray:
        """Resize using PIL for quality interpolation."""
        img = PILImage.fromarray(viewport)
        vp_w, vp_h = img.size

        if fit_mode == "nearest":
            resample = PILImage.NEAREST
        else:
            resample = PILImage.LANCZOS

        if fit_mode == "stretch":
            img = img.resize((out_w, out_h), resample)
        elif fit_mode == "cover":
            scale = max(out_w / vp_w, out_h / vp_h)
            new_w = max(1, int(vp_w * scale))
            new_h = max(1, int(vp_h * scale))
            img = img.resize((new_w, new_h), resample)
            left = (new_w - out_w) // 2
            top = (new_h - out_h) // 2
            img = img.crop((left, top, left + out_w, top + out_h))
        elif fit_mode in ("contain", "nearest"):
            scale = min(out_w / vp_w, out_h / vp_h)
            new_w = max(1, int(vp_w * scale))
            new_h = max(1, int(vp_h * scale))
            img = img.resize((new_w, new_h), resample)
            result = PILImage.new("RGB", (out_w, out_h), (0, 0, 0))
            paste_x = (out_w - new_w) // 2
            paste_y = (out_h - new_h) // 2
            result.paste(img, (paste_x, paste_y))
            img = result

        return np.array(img, dtype=np.uint8)

    def _resize_nearest(
        self, viewport: np.ndarray, out_w: int, out_h: int
    ) -> np.ndarray:
        """Nearest-neighbor resize with numpy (no PIL fallback)."""
        vp_h, vp_w = viewport.shape[:2]
        y_indices = (np.arange(out_h) * vp_h // out_h).clip(0, vp_h - 1)
        x_indices = (np.arange(out_w) * vp_w // out_w).clip(0, vp_w - 1)
        return viewport[np.ix_(y_indices, x_indices)]

    def _render_no_image(
        self, pixels: np.ndarray, num_pixels: int, time_elapsed: float
    ) -> np.ndarray:
        """Render a placeholder pattern when no image is loaded."""
        dims = self.config.output_dimensions
        if len(dims) >= 2:
            width = dims[0]
        else:
            width = dims[0]

        # Dim checkerboard with "no image" indication
        for i in range(num_pixels):
            x = i % width
            y = i // width
            if (x // 2 + y // 2) % 2 == 0:
                pixels[i] = (15, 15, 20)
            else:
                pixels[i] = (5, 5, 8)

        return pixels

    def to_dict(self) -> dict[str, Any]:
        """Include image info in serialization."""
        d = super().to_dict()
        d["image_info"] = {
            "loaded": self._image is not None,
            "width": self._image_width,
            "height": self._image_height,
            "path": self._image_path,
        }
        return d
