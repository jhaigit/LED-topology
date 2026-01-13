"""Rainbow pattern."""

from typing import Any

import numpy as np
from pydantic import Field

from ltp_source.patterns.base import Pattern, PatternParams, PatternRegistry, hsv_to_rgb


class RainbowParams(PatternParams):
    """Parameters for rainbow pattern."""

    speed: float = Field(default=1.0, ge=0.0, le=10.0)
    saturation: float = Field(default=1.0, ge=0.0, le=1.0)
    brightness: float = Field(default=1.0, ge=0.0, le=1.0)
    scale: float = Field(default=1.0, ge=0.1, le=10.0)
    reverse: bool = False


@PatternRegistry.register
class RainbowPattern(Pattern):
    """Animated rainbow gradient pattern."""

    name = "rainbow"
    description = "Moving rainbow gradient"
    params_class = RainbowParams

    def render(self, buffer: np.ndarray) -> None:
        """Render rainbow into buffer."""
        params: RainbowParams = self.params

        # Handle both 1D and 2D buffers
        if buffer.ndim == 2:
            # 1D: (pixels, channels)
            pixel_count = buffer.shape[0]
            for i in range(pixel_count):
                pos = i / pixel_count
                if params.reverse:
                    pos = 1.0 - pos

                hue = (pos * params.scale + self.time * params.speed) % 1.0
                r, g, b = hsv_to_rgb(hue, params.saturation, params.brightness)
                buffer[i] = [r, g, b]
        else:
            # 2D: (height, width, channels)
            height, width = buffer.shape[:2]
            for y in range(height):
                for x in range(width):
                    # Use diagonal position for 2D
                    pos = (x / width + y / height) / 2
                    if params.reverse:
                        pos = 1.0 - pos

                    hue = (pos * params.scale + self.time * params.speed) % 1.0
                    r, g, b = hsv_to_rgb(hue, params.saturation, params.brightness)
                    buffer[y, x] = [r, g, b]

    def get_controls(self) -> list[dict[str, Any]]:
        """Get controls for rainbow pattern."""
        return [
            {
                "id": "speed",
                "name": "Speed",
                "description": "Animation speed",
                "type": "number",
                "value": self.params.speed,
                "min": 0.0,
                "max": 10.0,
                "step": 0.1,
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
            {
                "id": "scale",
                "name": "Scale",
                "description": "Number of rainbow cycles",
                "type": "number",
                "value": self.params.scale,
                "min": 0.1,
                "max": 10.0,
                "step": 0.1,
                "group": "pattern",
            },
            {
                "id": "reverse",
                "name": "Reverse",
                "description": "Reverse direction",
                "type": "boolean",
                "value": self.params.reverse,
                "group": "pattern",
            },
        ]
