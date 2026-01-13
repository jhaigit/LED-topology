"""Gradient pattern."""

from typing import Any

import numpy as np
from pydantic import Field

from ltp_source.patterns.base import Pattern, PatternParams, PatternRegistry, lerp_color


class GradientParams(PatternParams):
    """Parameters for gradient pattern."""

    colors: list[tuple[int, int, int]] = Field(
        default=[(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    )
    speed: float = Field(default=0.0, ge=0.0, le=5.0)
    reverse: bool = False


@PatternRegistry.register
class GradientPattern(Pattern):
    """Static or animated multi-color gradient."""

    name = "gradient"
    description = "Multi-color gradient"
    params_class = GradientParams

    def render(self, buffer: np.ndarray) -> None:
        """Render gradient into buffer."""
        params: GradientParams = self.params
        colors = params.colors

        if len(colors) < 2:
            colors = [(0, 0, 0), (255, 255, 255)]

        # Handle both 1D and 2D buffers
        if buffer.ndim == 2:
            pixel_count = buffer.shape[0]
        else:
            height, width = buffer.shape[:2]
            pixel_count = width  # Use width for gradient direction
            buffer_2d = buffer

        segments = len(colors) - 1

        for i in range(pixel_count if buffer.ndim == 2 else width):
            # Position along gradient
            pos = i / max(pixel_count - 1, 1) if buffer.ndim == 2 else i / max(width - 1, 1)

            # Add animation offset
            if params.speed > 0:
                pos = (pos + self.time * params.speed) % 1.0

            if params.reverse:
                pos = 1.0 - pos

            # Find which segment we're in
            segment_pos = pos * segments
            segment = min(int(segment_pos), segments - 1)
            t = segment_pos - segment

            # Interpolate between colors
            c1 = colors[segment]
            c2 = colors[segment + 1]
            color = lerp_color(c1, c2, t)

            if buffer.ndim == 2:
                buffer[i] = color
            else:
                # For 2D, fill the entire column
                buffer_2d[:, i] = color

    def get_controls(self) -> list[dict[str, Any]]:
        """Get controls for gradient pattern."""
        # Convert colors to hex strings for the first two colors
        controls = [
            {
                "id": "speed",
                "name": "Speed",
                "description": "Animation speed (0 for static)",
                "type": "number",
                "value": self.params.speed,
                "min": 0.0,
                "max": 5.0,
                "step": 0.1,
                "group": "pattern",
            },
            {
                "id": "reverse",
                "name": "Reverse",
                "description": "Reverse gradient direction",
                "type": "boolean",
                "value": self.params.reverse,
                "group": "pattern",
            },
        ]

        # Add color controls for first 4 colors
        for i, color in enumerate(self.params.colors[:4]):
            r, g, b = color
            controls.append(
                {
                    "id": f"color_{i}",
                    "name": f"Color {i + 1}",
                    "description": f"Gradient color {i + 1}",
                    "type": "color",
                    "value": f"#{r:02X}{g:02X}{b:02X}",
                    "group": "pattern",
                }
            )

        return controls
