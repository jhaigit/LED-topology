"""LTP Media Source - Video and image display for LED matrices."""

from ltp_media_source.source import MediaSource, MediaSourceConfig
from ltp_media_source.inputs.base import MediaInput, FitMode
from ltp_media_source.processing.scaler import FrameScaler
from ltp_media_source.audio_buffer import AudioRingBuffer
from ltp_media_source.shared_context import SharedMediaContext
from ltp_media_source.audio.analyzer import AudioAnalyzer
from ltp_media_source.audio.beat_detector import BeatDetector, MultibandBeatDetector
from ltp_media_source.sources.base import LogicalSource, LogicalSourceConfig
from ltp_media_source.sources.video_source import VideoLogicalSource, VideoLogicalSourceConfig
from ltp_media_source.sources.audio_visualizer import (
    AudioVisualizerSource,
    AudioVisualizerSourceConfig,
)
from ltp_media_source.source_group import (
    MediaSourceGroup,
    SourceGroupConfig,
    SourceDefinition,
    run_source_group,
    run_source_group_from_yaml,
)
from ltp_media_source.multi_channel import (
    MultiChannelSource,
    MultiChannelConfig,
    ChannelConfig,
    Channel,
    run_multi_channel_source,
    run_multi_channel_from_yaml,
)
# Optional audio playback (requires sounddevice)
try:
    from ltp_media_source.audio_playback import (
        AudioPlayback,
        AudioPlaybackContext,
        create_playback_for_context,
        list_output_devices,
        get_default_output_device,
        HAS_SOUNDDEVICE,
    )
except ImportError:
    AudioPlayback = None  # type: ignore
    AudioPlaybackContext = None  # type: ignore
    create_playback_for_context = None  # type: ignore
    list_output_devices = None  # type: ignore
    get_default_output_device = None  # type: ignore
    HAS_SOUNDDEVICE = False
from ltp_media_source.visualizers import (
    # Base classes
    Visualizer,
    LinearVisualizer,
    MatrixVisualizer,
    # Color utilities
    ColorMode,
    # Linear visualizers
    SpectrumBarsLinear,
    VUMeterLinear,
    WaveformLinear,
    BeatPulseLinear,
    BassFillLinear,
    ChasingDotsLinear,
    LevelMeterLinear,
    # Matrix visualizers
    SpectrumBarsMatrix,
    SpectrogramMatrix,
    BeatRipplesMatrix,
    FrequencyHeatmapMatrix,
    VUMeterBarMatrix,
    WaveformScopeMatrix,
    PlasmaMatrix,
    # Factory
    create_visualizer,
    get_visualizer_names,
    VISUALIZERS,
)

__all__ = [
    # Original MediaSource
    "MediaSource",
    "MediaSourceConfig",
    "MediaInput",
    "FitMode",
    "FrameScaler",
    # Phase 1: Shared context
    "AudioRingBuffer",
    "SharedMediaContext",
    # Phase 2: Audio analysis
    "AudioAnalyzer",
    "BeatDetector",
    "MultibandBeatDetector",
    # Phase 3: Logical sources
    "LogicalSource",
    "LogicalSourceConfig",
    "VideoLogicalSource",
    "VideoLogicalSourceConfig",
    "AudioVisualizerSource",
    "AudioVisualizerSourceConfig",
    # Phase 5: Source Group
    "MediaSourceGroup",
    "SourceGroupConfig",
    "SourceDefinition",
    "run_source_group",
    "run_source_group_from_yaml",
    # Phase 7: Multi-Channel Protocol
    "MultiChannelSource",
    "MultiChannelConfig",
    "ChannelConfig",
    "Channel",
    "run_multi_channel_source",
    "run_multi_channel_from_yaml",
    # Audio playback (optional)
    "AudioPlayback",
    "AudioPlaybackContext",
    "create_playback_for_context",
    "list_output_devices",
    "get_default_output_device",
    "HAS_SOUNDDEVICE",
    # Phase 4: Visualizers
    "Visualizer",
    "LinearVisualizer",
    "MatrixVisualizer",
    "ColorMode",
    "SpectrumBarsLinear",
    "VUMeterLinear",
    "WaveformLinear",
    "BeatPulseLinear",
    "BassFillLinear",
    "ChasingDotsLinear",
    "LevelMeterLinear",
    "SpectrumBarsMatrix",
    "SpectrogramMatrix",
    "BeatRipplesMatrix",
    "FrequencyHeatmapMatrix",
    "VUMeterBarMatrix",
    "WaveformScopeMatrix",
    "PlasmaMatrix",
    "create_visualizer",
    "get_visualizer_names",
    "VISUALIZERS",
]
