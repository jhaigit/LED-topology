"""LTP Media Source - Video and image display for LED matrices."""

from ltp_media_source.source import MediaSource, MediaSourceConfig
from ltp_media_source.inputs.base import MediaInput, FitMode
from ltp_media_source.processing.scaler import FrameScaler
from ltp_media_source.audio_buffer import AudioRingBuffer
from ltp_media_source.shared_context import SharedMediaContext
from ltp_media_source.audio.analyzer import AudioAnalyzer
from ltp_media_source.audio.beat_detector import BeatDetector, MultibandBeatDetector

__all__ = [
    "MediaSource",
    "MediaSourceConfig",
    "MediaInput",
    "FitMode",
    "FrameScaler",
    "AudioRingBuffer",
    "SharedMediaContext",
    "AudioAnalyzer",
    "BeatDetector",
    "MultibandBeatDetector",
]
