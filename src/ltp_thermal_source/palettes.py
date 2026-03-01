"""Thermal-to-RGB color palettes for thermal imaging."""

from typing import Callable

import numpy as np


def _lerp(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, int, int]:
    """Linearly interpolate between two RGB colors."""
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _gradient(t: float, stops: list[tuple[float, tuple[int, ...]]]) -> tuple[int, int, int]:
    """Evaluate a multi-stop gradient at position t (0.0-1.0)."""
    t = max(0.0, min(1.0, t))
    for i in range(len(stops) - 1):
        pos_a, color_a = stops[i]
        pos_b, color_b = stops[i + 1]
        if t <= pos_b:
            local_t = (t - pos_a) / (pos_b - pos_a) if pos_b > pos_a else 0.0
            return _lerp(color_a, color_b, local_t)
    return stops[-1][1][:3]


def ironbow(t: float) -> tuple[int, int, int]:
    """Classic thermal camera palette: black -> blue -> magenta -> red -> yellow -> white."""
    stops = [
        (0.0, (0, 0, 0)),
        (0.2, (0, 0, 128)),
        (0.4, (128, 0, 128)),
        (0.6, (192, 0, 0)),
        (0.8, (255, 192, 0)),
        (1.0, (255, 255, 255)),
    ]
    return _gradient(t, stops)


def heat(t: float) -> tuple[int, int, int]:
    """Heat palette: black -> red -> yellow -> white."""
    stops = [
        (0.0, (0, 0, 0)),
        (0.33, (192, 0, 0)),
        (0.66, (255, 192, 0)),
        (1.0, (255, 255, 255)),
    ]
    return _gradient(t, stops)


def grayscale(t: float) -> tuple[int, int, int]:
    """Linear grayscale: black -> white."""
    v = int(max(0.0, min(1.0, t)) * 255)
    return (v, v, v)


def arctic(t: float) -> tuple[int, int, int]:
    """Arctic palette: blue -> cyan -> white -> yellow -> red."""
    stops = [
        (0.0, (0, 0, 128)),
        (0.25, (0, 128, 255)),
        (0.5, (255, 255, 255)),
        (0.75, (255, 255, 0)),
        (1.0, (255, 0, 0)),
    ]
    return _gradient(t, stops)


PALETTES: dict[str, Callable[[float], tuple[int, int, int]]] = {
    "ironbow": ironbow,
    "heat": heat,
    "grayscale": grayscale,
    "arctic": arctic,
}


def apply_palette(temps_normalized: np.ndarray, palette_name: str) -> np.ndarray:
    """Map normalized temperatures (0.0-1.0) to RGB colors using a palette.

    Args:
        temps_normalized: Array of shape (64,) with values in [0.0, 1.0].
        palette_name: Name of the palette to use.

    Returns:
        Array of shape (64, 3) with uint8 RGB values.
    """
    palette_func = PALETTES[palette_name]
    result = np.empty((len(temps_normalized), 3), dtype=np.uint8)
    for i, t in enumerate(temps_normalized):
        result[i] = palette_func(float(t))
    return result
