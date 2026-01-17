"""Media processing utilities."""

from ltp_media_source.processing.scaler import FrameScaler
from ltp_media_source.processing.color import apply_gamma, apply_brightness

__all__ = [
    "FrameScaler",
    "apply_gamma",
    "apply_brightness",
]
