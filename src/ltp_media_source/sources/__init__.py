"""Logical source implementations for multi-output media streaming."""

from ltp_media_source.sources.base import LogicalSource, LogicalSourceConfig
from ltp_media_source.sources.video_source import VideoLogicalSource
from ltp_media_source.sources.audio_visualizer import AudioVisualizerSource

__all__ = [
    "LogicalSource",
    "LogicalSourceConfig",
    "VideoLogicalSource",
    "AudioVisualizerSource",
]
