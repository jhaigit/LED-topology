"""Color utilities for audio visualizations.

Provides color generation, gradients, and color mapping functions
used by visualizers.
"""

from enum import Enum
from typing import Tuple, List

import numpy as np


class ColorMode(Enum):
    """Color mode for visualizations."""
    SOLID = "solid"           # Single color
    GRADIENT = "gradient"     # Two-color gradient
    RAINBOW = "rainbow"       # Full spectrum rainbow
    FREQUENCY = "frequency"   # Color mapped to frequency
    INTENSITY = "intensity"   # Color mapped to intensity
    FIRE = "fire"             # Red-orange-yellow gradient
    ICE = "ice"               # Blue-cyan-white gradient
    OCEAN = "ocean"           # Deep blue to cyan


# Pre-defined color palettes
PALETTE_RAINBOW = [
    (255, 0, 0),      # Red
    (255, 127, 0),    # Orange
    (255, 255, 0),    # Yellow
    (0, 255, 0),      # Green
    (0, 255, 255),    # Cyan
    (0, 0, 255),      # Blue
    (127, 0, 255),    # Purple
]

PALETTE_FIRE = [
    (0, 0, 0),        # Black
    (128, 0, 0),      # Dark red
    (255, 0, 0),      # Red
    (255, 128, 0),    # Orange
    (255, 255, 0),    # Yellow
    (255, 255, 255),  # White
]

PALETTE_ICE = [
    (0, 0, 32),       # Dark blue
    (0, 0, 128),      # Blue
    (0, 128, 255),    # Light blue
    (0, 255, 255),    # Cyan
    (255, 255, 255),  # White
]

PALETTE_OCEAN = [
    (0, 0, 64),       # Deep blue
    (0, 32, 128),     # Dark blue
    (0, 64, 192),     # Blue
    (0, 128, 255),    # Light blue
    (0, 200, 255),    # Cyan-ish
]


def hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """Convert HSV to RGB.

    Args:
        h: Hue (0-360)
        s: Saturation (0-1)
        v: Value/brightness (0-1)

    Returns:
        Tuple of (R, G, B) values (0-255)
    """
    h = h / 60.0
    i = int(h) % 6
    f = h - int(h)
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)

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

    return (int(r * 255), int(g * 255), int(b * 255))


def rgb_to_hsv(r: int, g: int, b: int) -> Tuple[float, float, float]:
    """Convert RGB to HSV.

    Args:
        r, g, b: RGB values (0-255)

    Returns:
        Tuple of (H, S, V) where H is 0-360, S and V are 0-1
    """
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    diff = max_c - min_c

    if diff == 0:
        h = 0
    elif max_c == r:
        h = (60 * ((g - b) / diff) + 360) % 360
    elif max_c == g:
        h = (60 * ((b - r) / diff) + 120) % 360
    else:
        h = (60 * ((r - g) / diff) + 240) % 360

    s = 0 if max_c == 0 else diff / max_c
    v = max_c

    return (h, s, v)


def interpolate_color(
    color1: Tuple[int, int, int],
    color2: Tuple[int, int, int],
    t: float,
) -> Tuple[int, int, int]:
    """Linearly interpolate between two colors.

    Args:
        color1: Start color (R, G, B)
        color2: End color (R, G, B)
        t: Interpolation factor (0-1)

    Returns:
        Interpolated color (R, G, B)
    """
    t = max(0.0, min(1.0, t))
    r = int(color1[0] + (color2[0] - color1[0]) * t)
    g = int(color1[1] + (color2[1] - color1[1]) * t)
    b = int(color1[2] + (color2[2] - color1[2]) * t)
    return (r, g, b)


def get_gradient_color(
    palette: List[Tuple[int, int, int]],
    t: float,
) -> Tuple[int, int, int]:
    """Get a color from a gradient palette.

    Args:
        palette: List of colors defining the gradient
        t: Position in gradient (0-1)

    Returns:
        Interpolated color (R, G, B)
    """
    if len(palette) == 0:
        return (0, 0, 0)
    if len(palette) == 1:
        return palette[0]

    t = max(0.0, min(1.0, t))

    # Find which segment of the gradient we're in
    segment_count = len(palette) - 1
    segment_pos = t * segment_count
    segment_index = int(segment_pos)
    segment_t = segment_pos - segment_index

    # Clamp to valid range
    if segment_index >= segment_count:
        return palette[-1]

    return interpolate_color(
        palette[segment_index],
        palette[segment_index + 1],
        segment_t,
    )


def generate_rainbow_colors(count: int, saturation: float = 1.0, value: float = 1.0) -> np.ndarray:
    """Generate rainbow colors.

    Args:
        count: Number of colors to generate
        saturation: Color saturation (0-1)
        value: Color brightness (0-1)

    Returns:
        Array of colors, shape (count, 3), dtype uint8
    """
    colors = np.zeros((count, 3), dtype=np.uint8)
    for i in range(count):
        hue = (i / count) * 300  # 0 to 300 (red to purple, skip back to red)
        colors[i] = hsv_to_rgb(hue, saturation, value)
    return colors


def generate_gradient_colors(
    count: int,
    color1: Tuple[int, int, int],
    color2: Tuple[int, int, int],
) -> np.ndarray:
    """Generate a gradient between two colors.

    Args:
        count: Number of colors to generate
        color1: Start color (R, G, B)
        color2: End color (R, G, B)

    Returns:
        Array of colors, shape (count, 3), dtype uint8
    """
    colors = np.zeros((count, 3), dtype=np.uint8)
    for i in range(count):
        t = i / max(1, count - 1)
        colors[i] = interpolate_color(color1, color2, t)
    return colors


def generate_palette_colors(count: int, palette: List[Tuple[int, int, int]]) -> np.ndarray:
    """Generate colors from a palette gradient.

    Args:
        count: Number of colors to generate
        palette: Color palette to interpolate

    Returns:
        Array of colors, shape (count, 3), dtype uint8
    """
    colors = np.zeros((count, 3), dtype=np.uint8)
    for i in range(count):
        t = i / max(1, count - 1)
        colors[i] = get_gradient_color(palette, t)
    return colors


def apply_intensity(color: np.ndarray, intensity: float) -> np.ndarray:
    """Apply intensity to a color or colors.

    Args:
        color: Color(s) as numpy array
        intensity: Intensity multiplier (0-1)

    Returns:
        Color(s) with intensity applied
    """
    intensity = max(0.0, min(1.0, intensity))
    return (color * intensity).astype(np.uint8)


def blend_colors(
    color1: np.ndarray,
    color2: np.ndarray,
    alpha: float,
) -> np.ndarray:
    """Blend two colors/frames together.

    Args:
        color1: Base color/frame
        color2: Overlay color/frame
        alpha: Blend factor (0 = all color1, 1 = all color2)

    Returns:
        Blended result
    """
    alpha = max(0.0, min(1.0, alpha))
    return (color1 * (1 - alpha) + color2 * alpha).astype(np.uint8)


def get_color_for_mode(
    mode: ColorMode,
    position: float,
    intensity: float = 1.0,
    base_color: Tuple[int, int, int] = (255, 255, 255),
) -> Tuple[int, int, int]:
    """Get a color based on mode, position, and intensity.

    Args:
        mode: Color mode
        position: Position (0-1) for gradients/rainbow
        intensity: Brightness intensity (0-1)
        base_color: Base color for solid mode

    Returns:
        Color (R, G, B)
    """
    if mode == ColorMode.SOLID:
        color = base_color
    elif mode == ColorMode.RAINBOW:
        hue = position * 300  # Red to purple
        color = hsv_to_rgb(hue, 1.0, 1.0)
    elif mode == ColorMode.FIRE:
        color = get_gradient_color(PALETTE_FIRE, position)
    elif mode == ColorMode.ICE:
        color = get_gradient_color(PALETTE_ICE, position)
    elif mode == ColorMode.OCEAN:
        color = get_gradient_color(PALETTE_OCEAN, position)
    elif mode == ColorMode.FREQUENCY:
        # Map position (frequency) to color
        hue = (1 - position) * 240  # Low freq = red, high freq = blue
        color = hsv_to_rgb(hue, 1.0, 1.0)
    elif mode == ColorMode.INTENSITY:
        # Map intensity to color (fire-like)
        color = get_gradient_color(PALETTE_FIRE, intensity)
    else:
        color = base_color

    # Apply intensity
    return (
        int(color[0] * intensity),
        int(color[1] * intensity),
        int(color[2] * intensity),
    )
