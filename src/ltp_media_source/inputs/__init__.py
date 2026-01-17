"""Media input implementations."""

from ltp_media_source.inputs.base import MediaInput, FitMode
from ltp_media_source.inputs.image import ImageInput
from ltp_media_source.inputs.gif import GifInput
from ltp_media_source.inputs.video import VideoInput
from ltp_media_source.inputs.camera import CameraInput
from ltp_media_source.inputs.screen import ScreenInput

# Registry of input types
INPUT_TYPES = {
    "image": ImageInput,
    "gif": GifInput,
    "video": VideoInput,
    "camera": CameraInput,
    "screen": ScreenInput,
}


def create_input(input_type: str, **kwargs) -> MediaInput:
    """Create a media input by type."""
    if input_type not in INPUT_TYPES:
        raise ValueError(f"Unknown input type: {input_type}. Available: {list(INPUT_TYPES.keys())}")
    return INPUT_TYPES[input_type](**kwargs)


__all__ = [
    "MediaInput",
    "FitMode",
    "ImageInput",
    "GifInput",
    "VideoInput",
    "CameraInput",
    "ScreenInput",
    "INPUT_TYPES",
    "create_input",
]
