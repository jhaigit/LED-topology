"""Plasma pattern - psychedelic animated effect."""

import math
from typing import Any

import numpy as np
from pydantic import Field

from ltp_source.patterns.base import Pattern, PatternParams, PatternRegistry, hsv_to_rgb


class PlasmaParams(PatternParams):
    """Parameters for plasma pattern."""

    speed: float = Field(default=0.5, ge=0.0, le=5.0)
    scale: float = Field(default=4.0, ge=0.5, le=20.0)
    complexity: int = Field(default=3, ge=1, le=6)
    saturation: float = Field(default=1.0, ge=0.0, le=1.0)
    brightness: float = Field(default=1.0, ge=0.0, le=1.0)


@PatternRegistry.register
class PlasmaPattern(Pattern):
    """Animated plasma/lava lamp effect using sine waves."""

    name = "plasma"
    description = "Psychedelic plasma effect"
    params_class = PlasmaParams

    def render(self, buffer: np.ndarray) -> None:
        """Render plasma into buffer."""
        params: PlasmaParams = self.params
        t = self.time * params.speed

        # Handle both 1D and 2D buffers
        if buffer.ndim == 2:
            # 1D: treat as single row
            pixel_count = buffer.shape[0]
            for i in range(pixel_count):
                x = i / pixel_count * params.scale
                value = self._plasma_value(x, 0, t, params.complexity)
                hue = (value + 1) / 2  # Normalize -1..1 to 0..1
                r, g, b = hsv_to_rgb(hue, params.saturation, params.brightness)
                buffer[i] = [r, g, b]
        else:
            # 2D
            height, width = buffer.shape[:2]
            for y in range(height):
                for x in range(width):
                    nx = x / width * params.scale
                    ny = y / height * params.scale
                    value = self._plasma_value(nx, ny, t, params.complexity)
                    hue = (value + 1) / 2
                    r, g, b = hsv_to_rgb(hue, params.saturation, params.brightness)
                    buffer[y, x] = [r, g, b]

    def _plasma_value(self, x: float, y: float, t: float, complexity: int) -> float:
        """Calculate plasma value at a point.

        Returns value in range -1 to 1.
        """
        value = 0.0

        # Layer multiple sine waves for complexity
        if complexity >= 1:
            value += math.sin(x + t)
        if complexity >= 2:
            value += math.sin(y + t * 0.7)
        if complexity >= 3:
            value += math.sin((x + y) / 2 + t * 0.5)
        if complexity >= 4:
            value += math.sin(math.sqrt(x * x + y * y) + t * 0.8)
        if complexity >= 5:
            value += math.sin(x * math.cos(t / 3) + y * math.sin(t / 2))
        if complexity >= 6:
            cx, cy = x - 2, y - 2
            value += math.sin(math.sqrt(cx * cx + cy * cy + 1) + t)

        return value / complexity  # Normalize

    def get_controls(self) -> list[dict[str, Any]]:
        """Get controls for plasma pattern."""
        return [
            {
                "id": "speed",
                "name": "Speed",
                "description": "Animation speed",
                "type": "number",
                "value": self.params.speed,
                "min": 0.0,
                "max": 5.0,
                "step": 0.1,
                "group": "pattern",
            },
            {
                "id": "scale",
                "name": "Scale",
                "description": "Pattern scale/zoom",
                "type": "number",
                "value": self.params.scale,
                "min": 0.5,
                "max": 20.0,
                "step": 0.5,
                "group": "pattern",
            },
            {
                "id": "complexity",
                "name": "Complexity",
                "description": "Number of wave layers",
                "type": "number",
                "value": self.params.complexity,
                "min": 1,
                "max": 6,
                "step": 1,
                "group": "pattern",
            },
            {
                "id": "saturation",
                "name": "Saturation",
                "description": "Color saturation",
                "type": "number",
                "value": self.params.saturation,
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
                "group": "pattern",
            },
            {
                "id": "brightness",
                "name": "Brightness",
                "description": "Color brightness",
                "type": "number",
                "value": self.params.brightness,
                "min": 0.0,
                "max": 1.0,
                "step": 0.05,
                "group": "pattern",
            },
        ]
