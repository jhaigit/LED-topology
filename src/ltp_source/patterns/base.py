"""Base pattern class and registry."""

from abc import ABC, abstractmethod
from typing import Any, Type

import numpy as np
from pydantic import BaseModel


class PatternParams(BaseModel):
    """Base parameters for patterns."""

    class Config:
        extra = "allow"


class Pattern(ABC):
    """Abstract base class for LED patterns."""

    # Pattern metadata - override in subclasses
    name: str = "base"
    description: str = "Base pattern"
    params_class: Type[PatternParams] = PatternParams

    def __init__(self, params: dict[str, Any] | None = None):
        """Initialize pattern with parameters.

        Args:
            params: Pattern-specific parameters
        """
        self.params = self.params_class(**(params or {}))
        self._time = 0.0

    @property
    def time(self) -> float:
        """Get current animation time."""
        return self._time

    def update_time(self, dt: float) -> None:
        """Update animation time.

        Args:
            dt: Time delta in seconds
        """
        self._time += dt

    def set_time(self, t: float) -> None:
        """Set animation time directly.

        Args:
            t: Time in seconds
        """
        self._time = t

    @abstractmethod
    def render(self, buffer: np.ndarray) -> None:
        """Render pattern into pixel buffer.

        Args:
            buffer: Numpy array to render into.
                   Shape is (pixels, channels) for 1D or (height, width, channels) for 2D.
                   Values should be 0-255 for each channel.
        """
        pass

    def get_controls(self) -> list[dict[str, Any]]:
        """Get control definitions for this pattern's parameters.

        Override to provide UI-adjustable controls.
        """
        return []

    def set_param(self, name: str, value: Any) -> None:
        """Set a parameter value.

        Args:
            name: Parameter name
            value: New value
        """
        if hasattr(self.params, name):
            setattr(self.params, name, value)


class PatternRegistry:
    """Registry of available patterns."""

    _patterns: dict[str, Type[Pattern]] = {}

    @classmethod
    def register(cls, pattern_class: Type[Pattern]) -> Type[Pattern]:
        """Register a pattern class.

        Can be used as a decorator:
            @PatternRegistry.register
            class MyPattern(Pattern):
                ...
        """
        cls._patterns[pattern_class.name] = pattern_class
        return pattern_class

    @classmethod
    def get(cls, name: str) -> Type[Pattern] | None:
        """Get a pattern class by name."""
        return cls._patterns.get(name)

    @classmethod
    def create(cls, name: str, params: dict[str, Any] | None = None) -> Pattern:
        """Create a pattern instance by name.

        Args:
            name: Pattern name
            params: Pattern parameters

        Returns:
            Pattern instance

        Raises:
            KeyError: If pattern not found
        """
        pattern_class = cls._patterns.get(name)
        if pattern_class is None:
            raise KeyError(f"Unknown pattern: {name}")
        return pattern_class(params)

    @classmethod
    def list_patterns(cls) -> list[dict[str, str]]:
        """List all registered patterns."""
        return [
            {"name": p.name, "description": p.description}
            for p in cls._patterns.values()
        ]

    @classmethod
    def names(cls) -> list[str]:
        """Get list of pattern names."""
        return list(cls._patterns.keys())


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    """Convert HSV to RGB.

    Args:
        h: Hue (0-1)
        s: Saturation (0-1)
        v: Value (0-1)

    Returns:
        RGB tuple (0-255 each)
    """
    if s == 0:
        r = g = b = int(v * 255)
        return r, g, b

    h = h % 1.0
    i = int(h * 6)
    f = (h * 6) - i
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))

    if i == 0:
        r, g, b = v, t, p
    elif i == 1:
        r, g, b = q, v, p
    elif i == 2:
        r, g, b = p, v, t
    elif i == 3:
        r, g, b = p, q, v
    elif i == 4:
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q

    return int(r * 255), int(g * 255), int(b * 255)


def rgb_to_hsv(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Convert RGB to HSV.

    Args:
        r, g, b: RGB values (0-255)

    Returns:
        HSV tuple (0-1 each)
    """
    r, g, b = r / 255, g / 255, b / 255
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    diff = max_c - min_c

    if diff == 0:
        h = 0
    elif max_c == r:
        h = ((g - b) / diff) % 6
    elif max_c == g:
        h = (b - r) / diff + 2
    else:
        h = (r - g) / diff + 4

    h /= 6
    s = 0 if max_c == 0 else diff / max_c
    v = max_c

    return h, s, v


def lerp_color(
    c1: tuple[int, int, int], c2: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    """Linear interpolation between two RGB colors.

    Args:
        c1: First color
        c2: Second color
        t: Interpolation factor (0-1)

    Returns:
        Interpolated color
    """
    t = max(0, min(1, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )
