"""Color processing utilities."""

import numpy as np


def apply_gamma(frame: np.ndarray, gamma: float = 2.2) -> np.ndarray:
    """Apply gamma correction to frame.

    Args:
        frame: Input frame (H, W, 3) uint8
        gamma: Gamma value (1.0 = no change, 2.2 = typical for LEDs)

    Returns:
        Gamma-corrected frame
    """
    if gamma == 1.0:
        return frame

    # Build lookup table for efficiency
    inv_gamma = 1.0 / gamma
    lut = np.array(
        [((i / 255.0) ** inv_gamma) * 255 for i in range(256)], dtype=np.uint8
    )

    return lut[frame]


def apply_brightness(frame: np.ndarray, brightness: float = 1.0) -> np.ndarray:
    """Apply brightness adjustment to frame.

    Args:
        frame: Input frame (H, W, 3) uint8
        brightness: Brightness multiplier (0.0 = black, 1.0 = unchanged)

    Returns:
        Brightness-adjusted frame
    """
    if brightness == 1.0:
        return frame

    if brightness == 0.0:
        return np.zeros_like(frame)

    # Scale and clip
    result = (frame.astype(np.float32) * brightness).clip(0, 255).astype(np.uint8)
    return result


def apply_saturation(frame: np.ndarray, saturation: float = 1.0) -> np.ndarray:
    """Apply saturation adjustment to frame.

    Args:
        frame: Input frame (H, W, 3) RGB uint8
        saturation: Saturation multiplier (0.0 = grayscale, 1.0 = unchanged)

    Returns:
        Saturation-adjusted frame
    """
    if saturation == 1.0:
        return frame

    # Convert to float for calculation
    f = frame.astype(np.float32)

    # Calculate luminance (grayscale)
    gray = 0.299 * f[:, :, 0] + 0.587 * f[:, :, 1] + 0.114 * f[:, :, 2]
    gray = gray[:, :, np.newaxis]

    # Interpolate between grayscale and original
    result = gray + saturation * (f - gray)
    return result.clip(0, 255).astype(np.uint8)


def apply_contrast(frame: np.ndarray, contrast: float = 1.0) -> np.ndarray:
    """Apply contrast adjustment to frame.

    Args:
        frame: Input frame (H, W, 3) uint8
        contrast: Contrast multiplier (1.0 = unchanged)

    Returns:
        Contrast-adjusted frame
    """
    if contrast == 1.0:
        return frame

    f = frame.astype(np.float32)
    result = 128 + contrast * (f - 128)
    return result.clip(0, 255).astype(np.uint8)


def bgr_to_rgb(frame: np.ndarray) -> np.ndarray:
    """Convert BGR to RGB (for OpenCV frames).

    Args:
        frame: Input frame in BGR format

    Returns:
        Frame in RGB format
    """
    return frame[:, :, ::-1].copy()


def rgba_to_rgb(frame: np.ndarray, background: tuple = (0, 0, 0)) -> np.ndarray:
    """Convert RGBA to RGB with alpha blending.

    Args:
        frame: Input frame (H, W, 4) RGBA
        background: Background color for alpha blending

    Returns:
        RGB frame (H, W, 3)
    """
    if frame.shape[2] == 3:
        return frame

    rgb = frame[:, :, :3].astype(np.float32)
    alpha = frame[:, :, 3:4].astype(np.float32) / 255.0

    bg = np.array(background, dtype=np.float32)
    result = rgb * alpha + bg * (1 - alpha)

    return result.astype(np.uint8)
