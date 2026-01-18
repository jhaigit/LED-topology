# Multiple Output Sources from Single Media

This document explores approaches for providing multiple synchronized outputs from a single media source (e.g., video frames plus audio visualizations from the same file).

## Motivation

A media source playing a video file may want to provide multiple synchronized outputs:
- **Video frames** scaled to a matrix display
- **Audio visualization** (spectrum, waveform, beat detection) for a linear LED strip
- **Audio visualization** formatted for a secondary matrix display

Currently, a source can have multiple subscribers, but all receive identical frame data. There is no mechanism for a single source to provide fundamentally different output types to different subscribers.

## Use Cases

1. **Media playback with ambient lighting** - Video on main display, audio-reactive effects on peripheral strips
2. **DJ/music visualization** - Multiple displays showing different visualizations of the same audio
3. **Synchronized multi-format output** - Same content rendered for different display topologies
4. **Picture-in-picture style** - Main video plus smaller audio meters or status displays

---

## Three Approaches

### Summary Comparison

| Aspect | Multi-Channel Source | Separate Logical Sources | Separate Process Sources |
|--------|---------------------|-------------------------|-------------------------|
| **Protocol changes** | Yes (channel object) | None | None |
| **Process model** | Single process | Single process | Multiple processes |
| **Media decode** | Shared | Shared | Duplicated or IPC |
| **Sync guarantee** | Built-in | Built-in | Requires coordination |
| **mDNS appearance** | One source, N channels | N independent sources | N independent sources |
| **Implementation** | Complex (new protocol) | Moderate | Complex (IPC) |
| **Resource efficiency** | High | High | Lower |

---

## Approach 1: Multi-Channel Source (Protocol Extension)

### Concept

Introduce **"channel"** as a new protocol-level object. A single source advertises multiple channels, each with its own characteristics. Subscribers explicitly request a specific channel.

### Key Characteristics

- **Channel is a protocol object** - defined in messages, visible in discovery
- **Single device_id** - one source identity with multiple outputs
- **Single mDNS advertisement** - channels listed in TXT records
- **Explicit channel selection** - subscribers specify which channel they want

### Protocol Changes Required

#### CAPABILITY_RESPONSE - Add channels array:
```json
{
  "type": "capability_response",
  "device_id": "uuid",
  "name": "Media Player",
  "channels": [
    {"id": "video", "type": "matrix", "dimensions": [64, 32]},
    {"id": "audio_linear", "type": "linear", "dimensions": [60]},
    {"id": "audio_matrix", "type": "matrix", "dimensions": [16, 16]}
  ],
  "default_channel": "video"
}
```

#### SUBSCRIBE - Add channel field:
```json
{
  "type": "subscribe",
  "channel": "audio_linear",
  "callback": {"host": "...", "port": ...}
}
```

#### mDNS TXT records:
```
channels=3
ch0=video,matrix,64x32
ch1=audio_linear,linear,60
ch2=audio_matrix,matrix,16x16
```

### Advantages

- Clean semantic model (channel is explicit concept)
- Single point of discovery and control
- Guaranteed synchronization
- Efficient resource sharing

### Disadvantages

- Requires protocol changes
- All channels must be defined at source startup
- More complex source implementation
- Subscribers must understand channel concept

---

## Approach 2: Separate Logical Sources (Recommended)

### Concept

Run multiple independent source instances **within the same process**, sharing a common media decoder. Each source has its own identity (device_id, mDNS advertisement, TCP control port) but they share internal state.

**The protocol sees completely normal, independent sources.** The fact that they share a decoder is an internal implementation detail invisible to subscribers.

### Key Characteristics

- **No protocol changes** - each source is a standard single-output source
- **Multiple device_ids** - each logical source has its own identity
- **Multiple mDNS advertisements** - appear as separate sources
- **Shared internal state** - same decoder, timeline, audio buffers
- **Implicit relationship** - sources are related by naming convention only

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                 Single Process: MediaSourceGroup                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                  SharedMediaContext                         │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │ │
│  │  │ MediaReader │  │ AudioBuffer │  │ PlaybackController  │ │ │
│  │  │ (single     │  │ (decoded    │  │ (play/pause/seek    │ │ │
│  │  │  decoder)   │  │  samples)   │  │  state)             │ │ │
│  │  └─────────────┘  └─────────────┘  └─────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                   │
│              ┌───────────────┼───────────────┐                  │
│              ▼               ▼               ▼                  │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │
│  │ LogicalSource   │ │ LogicalSource   │ │ LogicalSource   │   │
│  │ "MyVideo"       │ │ "MyVideo-Audio" │ │ "MyVideo-Spec"  │   │
│  ├─────────────────┤ ├─────────────────┤ ├─────────────────┤   │
│  │ device_id: A    │ │ device_id: B    │ │ device_id: C    │   │
│  │ type: matrix    │ │ type: linear    │ │ type: matrix    │   │
│  │ dim: [64,32]    │ │ dim: [60]       │ │ dim: [16,16]    │   │
│  │                 │ │                 │ │                 │   │
│  │ Own mDNS advert │ │ Own mDNS advert │ │ Own mDNS advert │   │
│  │ Own TCP server  │ │ Own TCP server  │ │ Own TCP server  │   │
│  │ Own UDP senders │ │ Own UDP senders │ │ Own UDP senders │   │
│  └────────┬────────┘ └────────┬────────┘ └────────┬────────┘   │
│           │                   │                   │             │
│           ▼                   ▼                   ▼             │
│     [Subscribers]       [Subscribers]       [Subscribers]       │
│                                                                  │
│  Protocol: Standard LTP (no changes)                            │
│  Sync: Guaranteed (shared internal timeline)                    │
└─────────────────────────────────────────────────────────────────┘
```

### How Sync Works

1. All logical sources reference the same `SharedMediaContext`
2. When video source reads a frame, it also updates the audio buffer
3. Audio sources read from the shared buffer (no separate decode)
4. Play/pause/seek on any source propagates to all via shared controller
5. Timeline position is authoritative - all sources render for same timestamp

### Discovery Appearance

Subscribers see three independent sources:
```
_ltp-source._tcp.local:
  - "MyVideo"       (matrix, 64x32)
  - "MyVideo-Audio" (linear, 60)
  - "MyVideo-Spec"  (matrix, 16x16)
```

Naming convention indicates relationship, but protocol treats them as unrelated.

### Advantages

- **No protocol changes** - works with existing infrastructure
- **Guaranteed sync** - shared internal state
- **Resource efficient** - single decode
- **Simple subscriber experience** - just normal sources
- **Flexible** - can add/remove logical sources at runtime
- **Independent configuration** - each source fully configurable

### Disadvantages

- Relationship between sources is implicit (naming only)
- Multiple mDNS advertisements (minor overhead)
- Control commands must be coordinated internally
- Subscriber must discover and connect to each source separately

---

## Approach 3: Separate Process Sources (IPC Coordination)

### Concept

Run each source as a completely independent process. Coordinate via IPC mechanisms (shared memory, message queues, or network sync).

### Key Characteristics

- **No protocol changes**
- **Multiple processes** - can run on different machines
- **Explicit coordination layer** - IPC for sync
- **Independent or shared decode** - depends on implementation

### Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Process A       │     │ Process B       │     │ Process C       │
│ VideoSource     │     │ AudioLinSource  │     │ AudioMtxSource  │
│                 │     │                 │     │                 │
│ Own decoder     │     │ Shared mem read │     │ Shared mem read │
│ Leader role     │     │ Follower role   │     │ Follower role   │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Shared Memory Region  │
                    │   - Audio samples       │
                    │   - Playback position   │
                    │   - Control state       │
                    └─────────────────────────┘
```

### Advantages

- Can distribute across machines
- Process isolation (crash doesn't affect others)
- Can mix languages/implementations

### Disadvantages

- Complex IPC coordination
- Potential sync drift
- Resource duplication (unless shared memory)
- Harder to implement correctly

---

## Recommendation

**Implement Approach 2 (Separate Logical Sources)** for the following reasons:

1. **No protocol changes** - works immediately with existing sinks and controllers
2. **Guaranteed sync** - shared process state is simpler than IPC
3. **Resource efficient** - single decoder
4. **Incremental** - can evolve to multi-channel protocol later if needed
5. **Simpler** - no IPC complexity, no protocol negotiation

The multi-channel protocol (Approach 1) can be added later as an optimization if the multiple-advertisement overhead becomes problematic or if explicit channel semantics become valuable.

---

## Audio Visualization Types

Regardless of approach, the following audio visualizations should be supported:

### Linear (1D) Animations

| Name | Description | Parameters |
|------|-------------|------------|
| VU Meter | Volume level as filled bar | color, peak_hold, decay |
| Spectrum Bars | FFT bands as bar heights | num_bands, color_mode, smoothing |
| Waveform | Audio waveform display | color, scale, trigger_mode |
| Beat Pulse | Flash/fade on beat | color, decay_time, sensitivity |
| Bass Fill | Low-freq fills from center | threshold, color, spread |
| Chasing Dots | Dots move with beat | num_dots, speed_multiplier |

### Matrix (2D) Animations

| Name | Description | Parameters |
|------|-------------|------------|
| Spectrogram | Scrolling spectrum history | direction, color_map, scroll_speed |
| Circular Spectrum | Radial frequency display | center, max_radius |
| Waveform Scope | Oscilloscope-style display | color, persistence |
| Beat Ripples | Ripples emanate on beat | origin, color, decay |
| Frequency Heatmap | 2D frequency intensity | color_map, smoothing |
| VU Meter Bar | Horizontal or vertical bar | orientation, segments |

### Shared Parameters

All visualizations should support:
- **gain** - Input amplification (0.0 - 5.0)
- **smoothing** - Temporal smoothing factor (0.0 - 1.0)
- **color_mode** - Static, gradient, frequency-mapped
- **frequency_range** - Min/max Hz to analyze (20 - 20000)

---

## Implementation Plan: Separate Logical Sources

### Phase 1: Shared Media Context

Create the foundation for sharing media decode across multiple sources.

#### 1.1 SharedMediaContext Class

**Location:** `src/ltp_media_source/shared_context.py`

```python
class SharedMediaContext:
    """Shared state for multiple logical sources from same media."""

    # Media access
    media_path: str
    reader: MediaReader  # Single decoder instance

    # Shared buffers
    video_frame: np.ndarray | None  # Current decoded video frame
    audio_buffer: AudioRingBuffer   # Recent audio samples for analysis

    # Playback state
    position: float          # Current position in seconds
    duration: float          # Total duration
    playing: bool
    loop: bool
    speed: float

    # Synchronization
    lock: asyncio.Lock
    frame_event: asyncio.Event  # Signals new frame available

    # Methods
    async def seek(self, position: float)
    async def play()
    async def pause()
    async def get_video_frame() -> np.ndarray
    async def get_audio_samples(count: int) -> np.ndarray
```

#### 1.2 AudioRingBuffer Class

**Location:** `src/ltp_media_source/audio_buffer.py`

```python
class AudioRingBuffer:
    """Thread-safe ring buffer for audio samples."""

    buffer: np.ndarray      # Sample storage
    sample_rate: int        # e.g., 44100
    channels: int           # e.g., 2 for stereo
    write_pos: int

    def write(self, samples: np.ndarray)
    def read_recent(self, num_samples: int) -> np.ndarray
    def read_at_time(self, time: float, num_samples: int) -> np.ndarray
```

#### 1.3 Modify MediaReader

**Location:** `src/ltp_media_source/inputs/video.py`

- Add audio extraction during video decode
- Populate AudioRingBuffer alongside video frames
- Handle audio-only and video-only files

### Phase 2: Audio Analysis Engine

Build the DSP components for audio visualization.

#### 2.1 AudioAnalyzer Class

**Location:** `src/ltp_media_source/audio/analyzer.py`

```python
class AudioAnalyzer:
    """Real-time audio analysis for visualization."""

    sample_rate: int
    fft_size: int           # e.g., 2048
    hop_size: int           # e.g., 512

    # Analysis results (updated each frame)
    spectrum: np.ndarray    # FFT magnitude bins
    waveform: np.ndarray    # Recent samples
    rms: float              # Volume level
    peak: float             # Peak level
    beat: bool              # Beat detected this frame

    def analyze(self, samples: np.ndarray)
    def get_spectrum_bands(self, num_bands: int) -> np.ndarray
    def get_frequency_range(self, low_hz: float, high_hz: float) -> np.ndarray
```

#### 2.2 BeatDetector Class

**Location:** `src/ltp_media_source/audio/beat_detector.py`

```python
class BeatDetector:
    """Onset/beat detection for reactive effects."""

    sensitivity: float
    decay: float

    # State
    energy_history: collections.deque
    last_beat_time: float
    beat_intensity: float   # Decaying value after beat

    def update(self, spectrum: np.ndarray) -> bool  # Returns True on beat
    def get_intensity(self) -> float  # Current beat intensity (0-1)
```

### Phase 3: Logical Source Infrastructure

Create the framework for running multiple sources from shared context.

#### 3.1 LogicalSource Base Class

**Location:** `src/ltp_media_source/logical_source.py`

```python
class LogicalSource:
    """A source instance that shares media context with others."""

    context: SharedMediaContext
    config: SourceConfig

    # Own network identity
    device_id: str
    name: str
    advertiser: SourceAdvertiser
    control_server: ControlServer
    stream_manager: StreamManager
    data_senders: dict[str, DataSender]

    # Abstract method - subclasses implement rendering
    async def render_frame(self) -> np.ndarray

    # Lifecycle
    async def start()
    async def stop()
```

#### 3.2 VideoLogicalSource

**Location:** `src/ltp_media_source/sources/video_source.py`

```python
class VideoLogicalSource(LogicalSource):
    """Logical source that outputs scaled video frames."""

    scaler: FrameScaler

    async def render_frame(self) -> np.ndarray:
        frame = await self.context.get_video_frame()
        return self.scaler.scale(frame)
```

#### 3.3 AudioVisualizerSource

**Location:** `src/ltp_media_source/sources/audio_visualizer.py`

```python
class AudioVisualizerSource(LogicalSource):
    """Logical source that outputs audio visualizations."""

    analyzer: AudioAnalyzer
    visualizer: Visualizer  # Selected visualization mode

    async def render_frame(self) -> np.ndarray:
        samples = await self.context.get_audio_samples(self.analyzer.fft_size)
        self.analyzer.analyze(samples)
        return self.visualizer.render(self.analyzer)
```

### Phase 4: Visualizer Implementations

Create the actual visualization renderers.

#### 4.1 Visualizer Base Class

**Location:** `src/ltp_media_source/visualizers/base.py`

```python
class Visualizer(ABC):
    """Base class for audio visualizations."""

    dimensions: list[int]   # [width] or [width, height]
    color_mode: ColorMode

    @abstractmethod
    def render(self, analyzer: AudioAnalyzer) -> np.ndarray:
        """Render visualization frame from current analysis."""
        pass
```

#### 4.2 Linear Visualizers

**Location:** `src/ltp_media_source/visualizers/linear.py`

```python
class SpectrumBarsLinear(Visualizer):
    """Frequency spectrum as colored bars."""
    num_bands: int

class VUMeterLinear(Visualizer):
    """Volume meter with peak hold."""
    peak_hold_time: float

class WaveformLinear(Visualizer):
    """Audio waveform display."""

class BeatPulseLinear(Visualizer):
    """Flash on beat detection."""
```

#### 4.3 Matrix Visualizers

**Location:** `src/ltp_media_source/visualizers/matrix.py`

```python
class SpectrogramMatrix(Visualizer):
    """Scrolling spectrogram."""
    scroll_direction: str  # 'up', 'down', 'left', 'right'

class SpectrumBarsMatrix(Visualizer):
    """Vertical bars from bottom."""

class BeatRipplesMatrix(Visualizer):
    """Ripples emanate from center on beat."""

class FrequencyHeatmapMatrix(Visualizer):
    """2D frequency intensity map."""
```

### Phase 5: Source Group Manager

Coordinate multiple logical sources from single entry point.

#### 5.1 MediaSourceGroup Class

**Location:** `src/ltp_media_source/source_group.py`

```python
class MediaSourceGroup:
    """Manages multiple logical sources from shared media."""

    context: SharedMediaContext
    sources: list[LogicalSource]

    def __init__(self, media_path: str, source_configs: list[SourceConfig]):
        self.context = SharedMediaContext(media_path)
        self.sources = self._create_sources(source_configs)

    async def start(self):
        """Start all sources."""
        await self.context.start()
        for source in self.sources:
            await source.start()

    async def stop(self):
        """Stop all sources."""
        for source in self.sources:
            await source.stop()
        await self.context.stop()
```

#### 5.2 Configuration Format

```yaml
# media_source_group.yaml
media_path: /path/to/video.mp4

sources:
  - name: "MyVideo"
    type: video
    dimensions: [64, 32]
    fit_mode: contain

  - name: "MyVideo-Spectrum"
    type: audio_visualizer
    visualizer: spectrum_bars
    dimensions: [60]
    color_mode: frequency_gradient

  - name: "MyVideo-Spectrogram"
    type: audio_visualizer
    visualizer: spectrogram
    dimensions: [16, 16]
    scroll_direction: up
```

#### 5.3 CLI Entry Point

**Location:** `src/ltp_media_source/__main__.py` (extend existing)

```bash
# Single video source (existing behavior)
ltp-media-source --input video.mp4 --dimensions 64x32

# Multi-source group (new)
ltp-media-source-group --config media_group.yaml

# Or inline specification
ltp-media-source-group --input video.mp4 \
  --video "MyVideo:64x32" \
  --audio-viz "MyVideo-Spectrum:spectrum_bars:60" \
  --audio-viz "MyVideo-Spectrogram:spectrogram:16x16"
```

### Phase 6: Control Coordination

Ensure play/pause/seek affects all sources.

#### 6.1 Shared Control Handling

When any logical source receives a control command:

1. **Play/Pause/Seek** - Forward to SharedMediaContext
2. **Source-specific controls** (brightness, gain) - Handle locally
3. **SharedMediaContext** propagates state change to all sources

```python
class LogicalSource:
    async def handle_control_set(self, control_id: str, value: Any):
        if control_id in ('play', 'pause', 'seek', 'position'):
            # Shared control - affects all sources
            await self.context.handle_control(control_id, value)
        else:
            # Local control - this source only
            await self._handle_local_control(control_id, value)
```

#### 6.2 State Synchronization

All sources report consistent state:
- Position, duration, playing status come from SharedMediaContext
- Source-specific controls (brightness, visualizer settings) are local

### Implementation Order

1. **Phase 1** (Shared Media Context) - Foundation, required for everything
2. **Phase 2** (Audio Analysis) - Can develop in parallel with Phase 1
3. **Phase 3** (Logical Source) - Depends on Phase 1
4. **Phase 4** (Visualizers) - Depends on Phase 2
5. **Phase 5** (Source Group) - Depends on Phase 3
6. **Phase 6** (Control Coordination) - Polish after basics work

### Testing Strategy

1. **Unit tests** for AudioRingBuffer, AudioAnalyzer, BeatDetector
2. **Integration test** - Single video source using new shared context
3. **Integration test** - Video + one audio visualizer
4. **Full test** - Multiple visualizers, control coordination
5. **Performance test** - Verify no frame drops with multiple sources

---

## Future: Migration to Multi-Channel Protocol

If Separate Logical Sources proves successful but the multiple-mDNS-advertisement overhead becomes problematic, the multi-channel protocol (Approach 1) can be added:

1. Keep existing LogicalSource infrastructure
2. Add ChannelManager that wraps multiple LogicalSources
3. Single mDNS advertisement with channel metadata
4. SUBSCRIBE message routes to appropriate LogicalSource
5. Backward compatibility: sources without channels work as before

This would be a protocol v2.1 or v3.0 change.

---

## References

- `/spec/protocol.md` - Base LTP protocol specification
- `/spec/section-routing.md` - Related work on routing to display sections
- `/src/ltp_media_source/source.py` - Current media source implementation
- `/src/ltp_controller/virtual_sources/visualizers.py` - Existing audio visualization patterns
