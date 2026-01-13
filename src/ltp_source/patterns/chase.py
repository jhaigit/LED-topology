"""Chase pattern - moving dots."""

from typing import Any

import numpy as np
from pydantic import Field

from ltp_source.patterns.base import Pattern, PatternParams, PatternRegistry


class ChaseParams(PatternParams):
    """Parameters for chase pattern."""

    color: tuple[int, int, int] = (255, 255, 255)
    background: tuple[int, int, int] = (0, 0, 0)
    speed: float = Field(default=2.0, ge=0.0, le=20.0)
    tail_length: int = Field(default=5, ge=0, le=50)
    count: int = Field(default=1, ge=1, le=20)
    reverse: bool = False


@PatternRegistry.register
class ChasePattern(Pattern):
    """Chasing dot pattern with fading tail."""

    name = "chase"
    description = "Chasing dots with fading tail"
    params_class = ChaseParams

    def render(self, buffer: np.ndarray) -> None:
        """Render chase pattern into buffer."""
        params: ChaseParams = self.params

        # Handle both 1D and 2D buffers
        if buffer.ndim == 2:
            pixel_count = buffer.shape[0]
        else:
            pixel_count = buffer.shape[0] * buffer.shape[1]
            buffer = buffer.reshape(-1, buffer.shape[-1])

        # Fill with background
        buffer[:] = params.background

        # Calculate dot positions
        spacing = pixel_count / params.count
        base_pos = (self.time * params.speed * pixel_count / 10) % pixel_count

        if params.reverse:
            base_pos = pixel_count - base_pos

        for dot in range(params.count):
            dot_pos = (base_pos + dot * spacing) % pixel_count

            # Draw dot and tail
            for t in range(params.tail_length + 1):
                if params.reverse:
                    idx = int(dot_pos + t) % pixel_count
                else:
                    idx = int(dot_pos - t) % pixel_count

                # Fade factor
                fade = 1.0 - (t / (params.tail_length + 1))

                # Blend with existing color
                for c in range(min(3, buffer.shape[1])):
                    new_val = int(
                        params.background[c]
                        + (params.color[c] - params.background[c]) * fade
                    )
                    buffer[idx, c] = max(buffer[idx, c], new_val)

    def get_controls(self) -> list[dict[str, Any]]:
        """Get controls for chase pattern."""
        r, g, b = self.params.color
        br, bg, bb = self.params.background
        return [
            {
                "id": "color",
                "name": "Dot Color",
                "description": "Color of the chasing dots",
                "type": "color",
                "value": f"#{r:02X}{g:02X}{b:02X}",
                "group": "pattern",
            },
            {
                "id": "background",
                "name": "Background",
                "description": "Background color",
                "type": "color",
                "value": f"#{br:02X}{bg:02X}{bb:02X}",
                "group": "pattern",
            },
            {
                "id": "speed",
                "name": "Speed",
                "description": "Animation speed",
                "type": "number",
                "value": self.params.speed,
                "min": 0.0,
                "max": 20.0,
                "step": 0.5,
                "group": "pattern",
            },
            {
                "id": "tail_length",
                "name": "Tail Length",
                "description": "Length of fading tail",
                "type": "number",
                "value": self.params.tail_length,
                "min": 0,
                "max": 50,
                "step": 1,
                "group": "pattern",
            },
            {
                "id": "count",
                "name": "Dot Count",
                "description": "Number of dots",
                "type": "number",
                "value": self.params.count,
                "min": 1,
                "max": 20,
                "step": 1,
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
