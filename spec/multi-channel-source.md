# Multi-Channel Source Proposal

This document proposes an extension to the LTP source protocol to support multiple output channels from a single source, and compares this approach to alternatives.

## Motivation

A media source playing a video file may want to provide multiple synchronized outputs:
- **Video frames** scaled to a matrix display
- **Audio visualization** (spectrum, waveform, beat detection) for a linear LED strip
- **Audio visualization** formatted for a secondary matrix display

Currently, a source can have multiple subscribers, but all receive identical frame data. There is no mechanism for a single source to provide fundamentally different output types.

## Use Cases

1. **Media playback with ambient lighting** - Video on main display, audio-reactive effects on peripheral strips
2. **DJ/music visualization** - Multiple displays showing different visualizations of the same audio
3. **Synchronized multi-format output** - Same content rendered for different display topologies
4. **Picture-in-picture style** - Main video plus smaller audio meters or status displays

---

## Approach 1: Multi-Channel Source Extension

### Concept

Extend the source protocol so a single source can advertise and serve multiple named **channels**, each with its own:
- Output type (linear vs matrix)
- Dimensions
- Frame buffer
- Subscriber list

All channels share the same media timeline, ensuring synchronization.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Multi-Channel Media Source                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐     ┌─────────────────────────────────────┐   │
│  │ Media Input  │────▶│           Channel Manager           │   │
│  │ (video+audio)│     └─────────────────────────────────────┘   │
│  └──────────────┘                      │                        │
│                          ┌─────────────┼─────────────┐          │
│                          ▼             ▼             ▼          │
│                    ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│                    │ Channel: │  │ Channel: │  │ Channel: │     │
│                    │ "video"  │  │ "audio_  │  │ "audio_  │     │
│                    │          │  │  linear" │  │  matrix" │     │
│                    ├──────────┤  ├──────────┤  ├──────────┤     │
│                    │ 64x32    │  │ 60 pixels│  │ 16x16    │     │
│                    │ matrix   │  │ linear   │  │ matrix   │     │
│                    │ video    │  │ spectrum │  │ spectro- │     │
│                    │ frames   │  │ bars     │  │ gram     │     │
│                    └────┬─────┘  └────┬─────┘  └────┬─────┘     │
│                         │             │             │           │
│                         ▼             ▼             ▼           │
│                    [Subscribers] [Subscribers] [Subscribers]    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Protocol Changes

#### 1. CAPABILITY_RESPONSE Extension

Add optional `channels` array to capability response:

```json
{
  "type": "capability_response",
  "seq": 1,
  "device_id": "uuid",
  "device_type": "source",
  "name": "Media Player",
  "channels": [
    {
      "id": "video",
      "name": "Video Output",
      "type": "matrix",
      "dimensions": [64, 32],
      "color_format": "RGB",
      "rate": 30,
      "description": "Scaled video frames"
    },
    {
      "id": "audio_linear",
      "name": "Audio Spectrum (Linear)",
      "type": "linear",
      "dimensions": [60],
      "color_format": "RGB",
      "rate": 60,
      "description": "FFT spectrum for LED strip"
    },
    {
      "id": "audio_matrix",
      "name": "Audio Spectrogram",
      "type": "matrix",
      "dimensions": [16, 16],
      "color_format": "RGB",
      "rate": 30,
      "description": "Scrolling spectrogram display"
    }
  ],
  "default_channel": "video"
}
```

For backward compatibility:
- If `channels` is absent, source behaves as single-channel (current behavior)
- `default_channel` specifies which channel to use if subscriber doesn't specify

#### 2. SUBSCRIBE Extension

Add optional `channel` field to subscribe request:

```json
{
  "type": "subscribe",
  "seq": 2,
  "channel": "audio_linear",
  "dimensions": [60],
  "color_format": "RGB",
  "rate": 60,
  "callback": {
    "host": "192.168.1.100",
    "port": 5555
  }
}
```

- If `channel` is omitted, use `default_channel`
- Subscriber's requested dimensions must be compatible with channel type

#### 3. SUBSCRIBE_RESPONSE Extension

Include channel confirmation:

```json
{
  "type": "subscribe_response",
  "seq": 2,
  "success": true,
  "stream_id": "abc123",
  "channel": "audio_linear",
  "dimensions": [60],
  "color_format": "RGB",
  "rate": 60
}
```

#### 4. mDNS Advertisement

Extend TXT records to advertise channel count:

```
channels=3
ch0=video,matrix,64x32
ch1=audio_linear,linear,60
ch2=audio_matrix,matrix,16x16
```

### Implementation Requirements

1. **Channel Manager** - Tracks channel definitions and their subscribers
2. **Per-channel frame buffers** - Each channel maintains its own current frame
3. **Per-channel render pipelines** - Video scaling vs audio FFT processing
4. **Shared timeline** - All channels reference same media position
5. **Independent rates** - Channels can run at different frame rates

### Advantages

- Single process, single media file handle
- Guaranteed synchronization (shared timeline)
- Clean protocol extension (backward compatible)
- Efficient resource sharing (one audio decode, multiple visualizations)
- Single point of control (play/pause/seek affects all channels)

### Disadvantages

- More complex source implementation
- Protocol changes required
- All channels must be defined at source startup
- Channel configuration is source-side (not subscriber-configurable)

---

## Approach 2: Separate Sources from Shared Media

### Concept

Run multiple source instances that share access to the same media file, coordinating via:
- Shared memory for decoded frames/audio
- IPC for synchronization
- Or simply independent access with timestamp-based sync

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Media Source Manager                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐                                               │
│  │ Media File   │                                               │
│  │ (shared)     │                                               │
│  └──────┬───────┘                                               │
│         │                                                        │
│         ├──────────────────┬──────────────────┐                 │
│         ▼                  ▼                  ▼                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │ Source:     │    │ Source:     │    │ Source:     │         │
│  │ "Video"     │    │ "AudioLin"  │    │ "AudioMtx"  │         │
│  │             │    │             │    │             │         │
│  │ Advertises  │    │ Advertises  │    │ Advertises  │         │
│  │ separately  │    │ separately  │    │ separately  │         │
│  │ via mDNS    │    │ via mDNS    │    │ via mDNS    │         │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘         │
│         │                  │                  │                 │
│         ▼                  ▼                  ▼                 │
│   [Subscribers]      [Subscribers]      [Subscribers]           │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Coordination Layer (IPC/Shared State)       │    │
│  │  - Play/Pause/Seek synchronization                       │    │
│  │  - Shared media position                                 │    │
│  │  - Shared decoded audio buffer                           │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

### Implementation Options

#### Option A: Process-per-source with IPC

Each source runs as a separate process:
- Shared memory region for decoded audio samples
- Message queue for play/pause/seek commands
- Leader source controls media, followers sync to position

```python
# Pseudo-code
class VideoSource(MediaSource):
    def __init__(self, media_file, shared_state):
        self.media = MediaReader(media_file)
        self.shared = shared_state  # mmap or similar

class AudioLinearSource(MediaSource):
    def __init__(self, shared_state):
        self.shared = shared_state
        # Reads audio from shared buffer, not file

    def _render_frame(self):
        audio = self.shared.get_audio_buffer()
        return self.compute_spectrum(audio)
```

#### Option B: Single process, multiple source instances

One process manages multiple `MediaSource` objects:
- Shared `MediaReader` instance
- Each source has its own advertiser and subscriber management
- Coordinator routes control commands to all sources

```python
class MultiSourceManager:
    def __init__(self, media_file):
        self.reader = MediaReader(media_file)
        self.sources = [
            VideoSource(self.reader),
            AudioLinearSource(self.reader),
            AudioMatrixSource(self.reader),
        ]

    async def play(self):
        for source in self.sources:
            await source.play()
```

#### Option C: Timestamp-based loose sync

Independent sources, each opens same file:
- No explicit coordination
- Sync based on media timestamps
- Drift possible but often acceptable

### Advantages

- No protocol changes required
- Works with existing infrastructure
- Each source can be independently configured
- Flexible deployment (can run on different machines)
- Subscribers see familiar single-channel sources

### Disadvantages

- Coordination complexity for tight sync
- Resource duplication (multiple file handles, decoders)
- More processes/threads to manage
- Control commands must be routed to all sources
- Discovery shows multiple separate sources (user must know they're related)

---

## Comparison

| Aspect | Multi-Channel Source | Separate Sources |
|--------|---------------------|------------------|
| **Protocol changes** | Required | None |
| **Sync guarantee** | Built-in (shared timeline) | Requires coordination |
| **Resource efficiency** | High (shared decode) | Lower (potential duplication) |
| **Implementation complexity** | Medium (source changes) | Medium (coordination layer) |
| **Deployment flexibility** | Single host only | Can distribute |
| **Discovery UX** | Single source, multiple channels | Multiple sources (related by name) |
| **Backward compatibility** | Yes (channels optional) | Full |
| **Per-output configuration** | Limited (source-defined) | Full flexibility |
| **Control routing** | Single endpoint | Must fan-out |

---

## Recommendation

**For tightly synchronized outputs** (video + audio visualization from same file):
- **Multi-channel source** is cleaner and guarantees sync
- Worth the protocol extension for this use case

**For loosely related outputs** (same media, but independent operation acceptable):
- **Separate sources** work fine with existing protocol
- Simpler initial implementation

**Hybrid approach**:
- Implement **separate sources** first (no protocol changes)
- Use **single-process multi-source manager** for coordination
- Add **multi-channel protocol** later if tight sync proves essential

---

## Audio Animation Types

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
- **gain** - Input amplification
- **smoothing** - Temporal smoothing factor
- **color_mode** - Static, gradient, frequency-mapped
- **frequency_range** - Min/max Hz to analyze

---

## Implementation Phases

### Phase 1: Audio Analysis Foundation
- Extract audio from video files (ffmpeg/av)
- Implement FFT spectrum analysis
- Implement beat detection
- Buffer management for real-time analysis

### Phase 2: Separate Sources (No Protocol Changes)
- Create `AudioVisualizerSource` class
- Implement linear visualizations
- Implement matrix visualizations
- Multi-source manager for coordination

### Phase 3: Multi-Channel Protocol (Optional)
- Extend capability/subscribe messages
- Update mDNS advertisement
- Implement channel manager in source
- Update controller for channel-aware routing

---

## References

- `/spec/protocol.md` - Base LTP protocol specification
- `/spec/section-routing.md` - Related work on routing to display sections
- `/src/ltp_media_source/source.py` - Current media source implementation
- `/src/ltp_controller/virtual_sources/visualizers.py` - Existing audio visualization patterns
