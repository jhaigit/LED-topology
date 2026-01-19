"""Media input implementations."""

from ltp_media_source.inputs.base import MediaInput, FitMode
from ltp_media_source.inputs.image import ImageInput
from ltp_media_source.inputs.gif import GifInput
from ltp_media_source.inputs.video import VideoInput
from ltp_media_source.inputs.camera import CameraInput
from ltp_media_source.inputs.screen import ScreenInput

# Optional audio inputs (require sounddevice and/or PyAV)
try:
    from ltp_media_source.inputs.audio import AudioFileInput, AudioOnlyContext, HAS_PYAV
    from ltp_media_source.inputs.microphone import (
        MicrophoneInput,
        SystemAudioInput,
        list_audio_devices,
        get_default_input_device,
        HAS_SOUNDDEVICE as _HAS_SOUNDDEVICE,
    )
    # HAS_AUDIO_INPUTS is True only if at least one audio library is available
    HAS_AUDIO_INPUTS = HAS_PYAV or _HAS_SOUNDDEVICE
except ImportError:
    HAS_AUDIO_INPUTS = False
    HAS_PYAV = False
    _HAS_SOUNDDEVICE = False
    AudioFileInput = None  # type: ignore
    AudioOnlyContext = None  # type: ignore
    MicrophoneInput = None  # type: ignore
    SystemAudioInput = None  # type: ignore
    list_audio_devices = None  # type: ignore
    get_default_input_device = None  # type: ignore

# Registry of input types
INPUT_TYPES = {
    "image": ImageInput,
    "gif": GifInput,
    "video": VideoInput,
    "camera": CameraInput,
    "screen": ScreenInput,
}

# Add audio inputs if available
if HAS_AUDIO_INPUTS:
    INPUT_TYPES["audio_file"] = AudioFileInput
    INPUT_TYPES["microphone"] = MicrophoneInput
    INPUT_TYPES["system_audio"] = SystemAudioInput


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
    # Audio inputs (optional)
    "HAS_AUDIO_INPUTS",
    "AudioFileInput",
    "AudioOnlyContext",
    "MicrophoneInput",
    "SystemAudioInput",
    "list_audio_devices",
    "get_default_input_device",
]
