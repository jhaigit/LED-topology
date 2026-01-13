"""Solid color pattern."""

from typing import Any

import numpy as np
from pydantic import Field

from ltp_source.patterns.base import Pattern, PatternParams, PatternRegistry


class SolidParams(PatternParams):
    """Parameters for solid color pattern."""

    color: tuple[int, int, int] = (255, 255, 255)


@PatternRegistry.register
class SolidPattern(Pattern):
    """Static solid color pattern."""

    name = "solid"
    description = "Static solid color"
    params_class = SolidParams

    def render(self, buffer: np.ndarray) -> None:
        """Fill buffer with solid color."""
        buffer[:] = self.params.color

    def get_controls(self) -> list[dict[str, Any]]:
        """Get controls for solid pattern."""
        r, g, b = self.params.color
        return [
            {
                "id": "color",
                "name": "Color",
                "description": "Solid color to display",
                "type": "color",
                "value": f"#{r:02X}{g:02X}{b:02X}",
                "group": "pattern",
            }
        ]
