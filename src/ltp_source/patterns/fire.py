"""Fire pattern - flickering flame effect."""

import random
from typing import Any

import numpy as np
from pydantic import Field

from ltp_source.patterns.base import Pattern, PatternParams, PatternRegistry


class FireParams(PatternParams):
    """Parameters for fire pattern."""

    cooling: int = Field(default=55, ge=20, le=100)
    sparking: int = Field(default=120, ge=50, le=200)
    brightness: float = Field(default=1.0, ge=0.0, le=1.0)
    reverse: bool = False


# Fire color palette (heat values 0-255 map to colors)
FIRE_PALETTE = [
    (0, 0, 0),        # 0: black
    (32, 0, 0),       # ~32: dark red
    (64, 0, 0),       # ~64: red
    (128, 0, 0),      # ~96: bright red
    (192, 32, 0),     # ~128: orange-red
    (255, 64, 0),     # ~160: orange
    (255, 128, 0),    # ~192: yellow-orange
    (255, 192, 64),   # ~224: yellow
    (255, 255, 128),  # 255: white-yellow
]


@PatternRegistry.register
class FirePattern(Pattern):
    """Animated fire/flame effect using heat diffusion."""

    name = "fire"
    description = "Flickering flame effect"
    params_class = FireParams

    def __init__(self, params: dict[str, Any] | None = None):
        super().__init__(params)
        self._heat: np.ndarray | None = None
        self._initialized = False

    def _init_heat(self, size: int) -> None:
        """Initialize heat array."""
        self._heat = np.zeros(size, dtype=np.float32)
        self._initialized = True

    def render(self, buffer: np.ndarray) -> None:
        """Render fire into buffer."""
        params: FireParams = self.params

        # Handle both 1D and 2D buffers
        if buffer.ndim == 2:
            pixel_count = buffer.shape[0]
            is_2d = False
        else:
            height, width = buffer.shape[:2]
            pixel_count = height  # Fire rises vertically
            is_2d = True

        # Initialize heat array if needed
        if self._heat is None or len(self._heat) != pixel_count:
            self._init_heat(pixel_count)

        # Step 1: Cool down every cell
        for i in range(pixel_count):
            cooldown = random.randint(0, ((params.cooling * 10) // pixel_count) + 2)
            self._heat[i] = max(0, self._heat[i] - cooldown)

        # Step 2: Heat diffuses upward
        for i in range(pixel_count - 1, 2, -1):
            self._heat[i] = (
                self._heat[i - 1] + self._heat[i - 2] + self._heat[i - 2]
            ) / 3

        # Step 3: Randomly ignite sparks at the bottom
        if random.randint(0, 255) < params.sparking:
            spark_pos = random.randint(0, min(7, pixel_count - 1))
            self._heat[spark_pos] = min(
                255, self._heat[spark_pos] + random.randint(160, 255)
            )

        # Step 4: Map heat to colors
        for i in range(pixel_count):
            heat_idx = i if not params.reverse else pixel_count - 1 - i
            color = self._heat_to_color(int(self._heat[heat_idx]))
            color = tuple(int(c * params.brightness) for c in color)

            if is_2d:
                # For 2D, fill the row at this height
                y = pixel_count - 1 - i  # Invert so fire rises
                buffer[y, :] = color
            else:
                buffer[i] = color

    def _heat_to_color(self, heat: int) -> tuple[int, int, int]:
        """Map heat value (0-255) to RGB color."""
        heat = max(0, min(255, heat))

        # Find position in palette
        palette_pos = heat / 255 * (len(FIRE_PALETTE) - 1)
        idx = int(palette_pos)
        t = palette_pos - idx

        if idx >= len(FIRE_PALETTE) - 1:
            return FIRE_PALETTE[-1]

        # Interpolate between palette colors
        c1 = FIRE_PALETTE[idx]
        c2 = FIRE_PALETTE[idx + 1]

        return (
            int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t),
        )

    def get_controls(self) -> list[dict[str, Any]]:
        """Get controls for fire pattern."""
        return [
            {
                "id": "cooling",
                "name": "Cooling",
                "description": "How much the fire cools as it rises",
                "type": "number",
                "value": self.params.cooling,
                "min": 20,
                "max": 100,
                "step": 5,
                "group": "pattern",
            },
            {
                "id": "sparking",
                "name": "Sparking",
                "description": "Chance of new sparks at the base",
                "type": "number",
                "value": self.params.sparking,
                "min": 50,
                "max": 200,
                "step": 10,
                "group": "pattern",
            },
            {
                "id": "brightness",
                "name": "Brightness",
                "description": "Overall brightness",
                "type": "number",
                "value": self.params.brightness,
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
                "group": "pattern",
            },
            {
                "id": "reverse",
                "name": "Reverse",
                "description": "Flip fire direction",
                "type": "boolean",
                "value": self.params.reverse,
                "group": "pattern",
            },
        ]
