"""Color palette system for virtual sources."""

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class ColorStop:
    """A color stop in a gradient palette."""

    position: float  # 0.0 to 1.0
    color: tuple[int, int, int]  # RGB


@dataclass
class Palette:
    """A color palette defined by gradient stops."""

    name: str
    stops: list[ColorStop] = field(default_factory=list)

    def get_color(self, t: float) -> tuple[int, int, int]:
        """Get interpolated color at position t (0.0 to 1.0)."""
        if not self.stops:
            return (0, 0, 0)

        t = max(0.0, min(1.0, t))

        # Find surrounding stops
        lower = self.stops[0]
        upper = self.stops[-1]

        for i, stop in enumerate(self.stops):
            if stop.position <= t:
                lower = stop
            if stop.position >= t:
                upper = stop
                break

        # Interpolate
        if lower.position == upper.position:
            return lower.color

        ratio = (t - lower.position) / (upper.position - lower.position)

        r = int(lower.color[0] + (upper.color[0] - lower.color[0]) * ratio)
        g = int(lower.color[1] + (upper.color[1] - lower.color[1]) * ratio)
        b = int(lower.color[2] + (upper.color[2] - lower.color[2]) * ratio)

        return (r, g, b)

    def get_colors(self, count: int) -> np.ndarray:
        """Get an array of colors sampled from the palette."""
        colors = np.zeros((count, 3), dtype=np.uint8)
        for i in range(count):
            t = i / max(1, count - 1)
            colors[i] = self.get_color(t)
        return colors

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "stops": [
                {"position": s.position, "color": list(s.color)} for s in self.stops
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Palette":
        """Create from dictionary."""
        stops = [
            ColorStop(position=s["position"], color=tuple(s["color"]))
            for s in data.get("stops", [])
        ]
        return cls(name=data["name"], stops=stops)

    @classmethod
    def from_colors(cls, name: str, colors: list[tuple[int, int, int]]) -> "Palette":
        """Create a palette from a list of evenly-spaced colors."""
        if not colors:
            return cls(name=name, stops=[])

        stops = []
        for i, color in enumerate(colors):
            position = i / max(1, len(colors) - 1)
            stops.append(ColorStop(position=position, color=color))

        return cls(name=name, stops=stops)


# Built-in palettes
BUILTIN_PALETTES: dict[str, Palette] = {}


def _init_builtin_palettes() -> None:
    """Initialize built-in palettes."""
    global BUILTIN_PALETTES

    # Rainbow
    BUILTIN_PALETTES["rainbow"] = Palette.from_colors(
        "rainbow",
        [
            (255, 0, 0),  # Red
            (255, 127, 0),  # Orange
            (255, 255, 0),  # Yellow
            (0, 255, 0),  # Green
            (0, 0, 255),  # Blue
            (75, 0, 130),  # Indigo
            (148, 0, 211),  # Violet
            (255, 0, 0),  # Back to red for seamless loop
        ],
    )

    # Fire
    BUILTIN_PALETTES["fire"] = Palette.from_colors(
        "fire",
        [
            (0, 0, 0),  # Black
            (128, 0, 0),  # Dark red
            (255, 0, 0),  # Red
            (255, 128, 0),  # Orange
            (255, 255, 0),  # Yellow
            (255, 255, 128),  # Light yellow
        ],
    )

    # Ice
    BUILTIN_PALETTES["ice"] = Palette.from_colors(
        "ice",
        [
            (0, 0, 32),  # Dark blue
            (0, 0, 128),  # Blue
            (0, 128, 255),  # Light blue
            (128, 200, 255),  # Pale blue
            (255, 255, 255),  # White
        ],
    )

    # Ocean
    BUILTIN_PALETTES["ocean"] = Palette.from_colors(
        "ocean",
        [
            (0, 0, 32),  # Deep blue
            (0, 32, 64),  # Dark blue
            (0, 64, 128),  # Blue
            (0, 128, 192),  # Teal
            (0, 192, 192),  # Cyan
            (64, 224, 208),  # Turquoise
        ],
    )

    # Forest
    BUILTIN_PALETTES["forest"] = Palette.from_colors(
        "forest",
        [
            (0, 32, 0),  # Dark green
            (0, 64, 0),  # Green
            (32, 128, 0),  # Light green
            (64, 192, 32),  # Lime
            (128, 96, 0),  # Brown
            (64, 48, 0),  # Dark brown
        ],
    )

    # Thermal (heat map)
    BUILTIN_PALETTES["thermal"] = Palette.from_colors(
        "thermal",
        [
            (0, 0, 0),  # Black (cold)
            (0, 0, 128),  # Blue
            (128, 0, 128),  # Purple
            (255, 0, 0),  # Red
            (255, 128, 0),  # Orange
            (255, 255, 0),  # Yellow
            (255, 255, 255),  # White (hot)
        ],
    )

    # Viridis (scientific color map)
    BUILTIN_PALETTES["viridis"] = Palette.from_colors(
        "viridis",
        [
            (68, 1, 84),
            (72, 40, 120),
            (62, 74, 137),
            (49, 104, 142),
            (38, 130, 142),
            (31, 158, 137),
            (53, 183, 121),
            (109, 205, 89),
            (180, 222, 44),
            (253, 231, 37),
        ],
    )

    # Plasma
    BUILTIN_PALETTES["plasma"] = Palette.from_colors(
        "plasma",
        [
            (13, 8, 135),
            (75, 3, 161),
            (125, 3, 168),
            (168, 34, 150),
            (203, 70, 121),
            (229, 107, 93),
            (248, 148, 65),
            (253, 195, 40),
            (240, 249, 33),
        ],
    )

    # Grayscale
    BUILTIN_PALETTES["grayscale"] = Palette.from_colors(
        "grayscale",
        [
            (0, 0, 0),
            (255, 255, 255),
        ],
    )

    # Party
    BUILTIN_PALETTES["party"] = Palette.from_colors(
        "party",
        [
            (255, 0, 128),  # Pink
            (128, 0, 255),  # Purple
            (0, 128, 255),  # Blue
            (0, 255, 128),  # Teal
            (128, 255, 0),  # Lime
            (255, 128, 0),  # Orange
            (255, 0, 128),  # Back to pink
        ],
    )

    # Lava
    BUILTIN_PALETTES["lava"] = Palette.from_colors(
        "lava",
        [
            (0, 0, 0),
            (128, 0, 0),
            (255, 0, 0),
            (255, 128, 0),
            (255, 255, 0),
            (255, 255, 255),
        ],
    )


# Initialize on module load
_init_builtin_palettes()


class PaletteRegistry:
    """Registry of available color palettes."""

    def __init__(self) -> None:
        self._custom: dict[str, Palette] = {}

    def get(self, name: str) -> Palette | None:
        """Get a palette by name."""
        if name in self._custom:
            return self._custom[name]
        return BUILTIN_PALETTES.get(name)

    def list_all(self) -> list[str]:
        """List all available palette names."""
        return list(BUILTIN_PALETTES.keys()) + list(self._custom.keys())

    def list_builtin(self) -> list[str]:
        """List built-in palette names."""
        return list(BUILTIN_PALETTES.keys())

    def list_custom(self) -> list[str]:
        """List custom palette names."""
        return list(self._custom.keys())

    def add_custom(self, palette: Palette) -> None:
        """Add a custom palette."""
        self._custom[palette.name] = palette

    def remove_custom(self, name: str) -> bool:
        """Remove a custom palette."""
        if name in self._custom:
            del self._custom[name]
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Export custom palettes to dictionary."""
        return {name: p.to_dict() for name, p in self._custom.items()}

    def load_from_dict(self, data: dict[str, Any]) -> None:
        """Load custom palettes from dictionary."""
        for name, pdata in data.items():
            self._custom[name] = Palette.from_dict(pdata)


# Global palette registry
palette_registry = PaletteRegistry()
