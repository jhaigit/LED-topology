# Media Source Implementation Plan

This document outlines the plan to implement an LTP source for displaying video and images on LED matrices.

## Overview

The Media Source will support:
- **Static images**: PNG, JPG, GIF, BMP, WebP
- **Animated GIFs**: Frame-by-frame playback with timing
- **Video files**: MP4, AVI, MOV, MKV, WebM
- **Video streams**: Webcam, RTSP, HTTP streams, screen capture
- **Procedural input**: Accept frames from external applications

---

## Use Cases

### 1. Video File Playback
```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│  video.mp4  │────▶│  Media Source   │────▶│  LED Matrix │
└─────────────┘     │  (resize/scale) │     │   (16x16)   │
                    └─────────────────┘     └─────────────┘
```

### 2. Live Camera Feed
```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│   Webcam    │────▶│  Media Source   │────▶│  LED Matrix │
│  /dev/video0│     │                 │     │   (32x8)    │
└─────────────┘     └─────────────────┘     └─────────────┘
```

### 3. Screen Capture
```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│  Desktop    │────▶│  Media Source   │────▶│  LED Matrix │
│  Region     │     │                 │     │             │
└─────────────┘     └─────────────────┘     └─────────────┘
```

### 4. Network Stream (RTSP/HTTP)
```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│ RTSP Camera │────▶│  Media Source   │────▶│  LED Matrix │
│ IP Stream   │     │                 │     │             │
└─────────────┘     └─────────────────┘     └─────────────┘
```

### 5. Application Integration
```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│ Python App  │────▶│  Media Source   │────▶│  LED Matrix │
│ (PIL/NumPy) │     │  (API/Pipe)     │     │             │
└─────────────┘     └─────────────────┘     └─────────────┘
```

---

## Library Comparison

| Library | Video | Images | Streams | Screen | Pros | Cons |
|---------|-------|--------|---------|--------|------|------|
| **OpenCV** | ✅ | ✅ | ✅ | ❌ | Fast, versatile, good codec support | Large dependency |
| **Pillow** | ❌ | ✅ | ❌ | ❌ | Simple, lightweight, good GIF support | No video |
| **imageio** | ✅ | ✅ | ❌ | ❌ | Simple API, ffmpeg backend | Limited streaming |
| **ffmpeg-python** | ✅ | ✅ | ✅ | ✅ | Full ffmpeg power | Subprocess-based |
| **mss** | ❌ | ❌ | ❌ | ✅ | Fast screen capture | Single purpose |
| **pygame** | ❌ | ✅ | ❌ | ❌ | Already a dependency | Limited formats |

### Recommendation

**Primary**: OpenCV (`opencv-python-headless`) - Best balance of features and performance
**Fallback**: Pillow + imageio for simpler installations
**Screen capture**: mss (cross-platform, fast)

---

## Input Types

### 1. Static Image
```python
class ImageInput:
    """Single image displayed continuously or with effects."""
    path: str                    # File path or URL
    fit_mode: FitMode           # cover, contain, stretch, tile
    background: tuple[int,int,int]  # Background color for letterboxing
    effects: list[Effect]       # Optional: scroll, fade, zoom
```

### 2. Animated GIF
```python
class GifInput:
    """Animated GIF with frame timing."""
    path: str
    loop: bool = True
    speed: float = 1.0          # Playback speed multiplier
    fit_mode: FitMode
```

### 3. Video File
```python
class VideoInput:
    """Video file playback."""
    path: str
    loop: bool = True
    speed: float = 1.0
    start_time: float = 0.0     # Start position in seconds
    end_time: float | None      # End position (None = end of file)
    fit_mode: FitMode
    audio: bool = False         # Future: audio sync
```

### 4. Camera/Device
```python
class CameraInput:
    """Live camera capture."""
    device: int | str           # Device index or path (/dev/video0)
    resolution: tuple[int,int]  # Capture resolution
    fps: int = 30
    fit_mode: FitMode
```

### 5. Network Stream
```python
class StreamInput:
    """Network video stream."""
    url: str                    # rtsp://, http://, https://
    protocol: str               # auto, rtsp, http, hls
    buffer_size: int = 5        # Frame buffer for smoothing
    reconnect: bool = True
    fit_mode: FitMode
```

### 6. Screen Capture
```python
class ScreenInput:
    """Desktop screen capture."""
    monitor: int = 0            # Monitor index
    region: tuple[int,int,int,int] | None  # x,y,w,h or None for full
    fps: int = 30
    fit_mode: FitMode
```

### 7. Pipe/API Input
```python
class PipeInput:
    """Receive frames from external process."""
    mode: str                   # "pipe", "shm", "socket"
    format: str                 # "rgb", "rgba", "bgr"
    dimensions: tuple[int,int]  # Expected input dimensions
```

---

## Scaling and Fit Modes

LED matrices are typically small (8x8 to 64x64). Proper scaling is critical.

### Fit Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `contain` | Scale to fit, preserve aspect, letterbox | Default, no cropping |
| `cover` | Scale to fill, preserve aspect, crop edges | Fill entire matrix |
| `stretch` | Stretch to exact size, ignore aspect | Distorted but full coverage |
| `tile` | Repeat pattern if smaller | Textures, patterns |
| `center` | No scaling, center in matrix | Pixel-perfect small images |

### Scaling Algorithm

For downscaling (most common), use area averaging for best results:

```python
def scale_frame(frame: np.ndarray, target_size: tuple[int, int],
                fit_mode: FitMode) -> np.ndarray:
    """Scale frame to target matrix dimensions."""
    h, w = frame.shape[:2]
    tw, th = target_size

    if fit_mode == FitMode.CONTAIN:
        # Scale to fit within bounds
        scale = min(tw / w, th / h)
        new_w, new_h = int(w * scale), int(h * scale)
        scaled = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        # Center in output
        result = np.zeros((th, tw, 3), dtype=np.uint8)
        x_off = (tw - new_w) // 2
        y_off = (th - new_h) // 2
        result[y_off:y_off+new_h, x_off:x_off+new_w] = scaled
        return result

    elif fit_mode == FitMode.COVER:
        # Scale to fill, crop excess
        scale = max(tw / w, th / h)
        new_w, new_h = int(w * scale), int(h * scale)
        scaled = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        # Center crop
        x_off = (new_w - tw) // 2
        y_off = (new_h - th) // 2
        return scaled[y_off:y_off+th, x_off:x_off+tw]

    elif fit_mode == FitMode.STRETCH:
        return cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
```

### Color Space Considerations

- OpenCV uses BGR by default → convert to RGB for LTP
- Consider gamma correction for LED perception
- Optional dithering for very small matrices (8x8)

---

## Architecture

### Module Structure

```
src/ltp_media_source/
├── __init__.py
├── __main__.py
├── cli.py                 # Command-line interface
├── source.py              # MediaSource class (LTP source)
├── inputs/
│   ├── __init__.py
│   ├── base.py            # MediaInput base class
│   ├── image.py           # Static image input
│   ├── gif.py             # Animated GIF input
│   ├── video.py           # Video file input
│   ├── camera.py          # Camera capture input
│   ├── stream.py          # Network stream input
│   ├── screen.py          # Screen capture input
│   └── pipe.py            # Pipe/API input
├── processing/
│   ├── __init__.py
│   ├── scaler.py          # Scaling and fit modes
│   ├── effects.py         # Visual effects (optional)
│   └── color.py           # Color space conversion, gamma
└── playlist.py            # Playlist/sequence support
```

### Core Classes

#### MediaInput (Base Class)
```python
class MediaInput(ABC):
    """Base class for all media inputs."""

    input_type: str = "unknown"

    def __init__(self, config: dict):
        self.config = config
        self._running = False

    @abstractmethod
    def open(self) -> None:
        """Open/initialize the input source."""
        pass

    @abstractmethod
    def read_frame(self) -> np.ndarray | None:
        """Read next frame. Returns None if no frame available."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close/cleanup the input source."""
        pass

    @property
    @abstractmethod
    def frame_rate(self) -> float:
        """Native frame rate of the source."""
        pass

    @property
    @abstractmethod
    def duration(self) -> float | None:
        """Duration in seconds (None for live sources)."""
        pass

    @property
    @abstractmethod
    def dimensions(self) -> tuple[int, int]:
        """Native dimensions (width, height)."""
        pass

    def seek(self, position: float) -> bool:
        """Seek to position in seconds. Returns False if not supported."""
        return False

    @property
    def is_live(self) -> bool:
        """True for cameras, streams, screen capture."""
        return self.duration is None
```

#### MediaSource (LTP Source)
```python
class MediaSource:
    """LTP source that outputs media content."""

    def __init__(self, config: MediaSourceConfig):
        self.config = config
        self._input: MediaInput | None = None
        self._scaler = FrameScaler(config.dimensions, config.fit_mode)

        # LTP components
        self._advertiser: SourceAdvertiser
        self._control_server: ControlServer
        self._stream_manager: StreamManager
        self._data_senders: dict[str, DataSender]

        # State
        self._running = False
        self._current_frame: np.ndarray | None = None
        self._frame_count = 0

    async def start(self) -> None:
        """Start the media source."""
        pass

    async def stop(self) -> None:
        """Stop the media source."""
        pass

    def set_input(self, input_config: dict) -> None:
        """Change the current input source."""
        pass

    async def _render_loop(self) -> None:
        """Main render loop - read frames, scale, send."""
        while self._running:
            frame = self._input.read_frame()
            if frame is not None:
                scaled = self._scaler.scale(frame)
                self._current_frame = scaled
                await self._send_frame(scaled)
            await asyncio.sleep(1.0 / self.config.rate)
```

---

## Controls

### Basic Controls
| ID | Type | Description |
|----|------|-------------|
| `brightness` | number (0-1) | Output brightness |
| `speed` | number (0.1-10) | Playback speed multiplier |
| `loop` | boolean | Loop video/GIF |
| `pause` | boolean | Pause playback |
| `fit_mode` | enum | contain, cover, stretch, tile |

### Playback Controls
| ID | Type | Description |
|----|------|-------------|
| `position` | number | Current position (seconds) |
| `seek` | action | Seek to position |
| `next` | action | Next in playlist |
| `previous` | action | Previous in playlist |

### Input Selection
| ID | Type | Description |
|----|------|-------------|
| `input_type` | enum | image, gif, video, camera, stream, screen |
| `input_path` | string | Path/URL for current input |

### Effects (Optional)
| ID | Type | Description |
|----|------|-------------|
| `gamma` | number (1-3) | Gamma correction |
| `saturation` | number (0-2) | Color saturation |
| `contrast` | number (0-2) | Contrast adjustment |
| `mirror_h` | boolean | Horizontal mirror |
| `mirror_v` | boolean | Vertical mirror |
| `rotate` | enum | 0, 90, 180, 270 |

---

## CLI Interface

```bash
# Static image
ltp-media-source --image logo.png --dimensions 16x16

# Animated GIF
ltp-media-source --gif animation.gif --dimensions 32x8 --loop

# Video file
ltp-media-source --video movie.mp4 --dimensions 64x64 --fit cover

# Webcam
ltp-media-source --camera 0 --dimensions 16x16

# Network stream
ltp-media-source --stream rtsp://camera.local/live --dimensions 32x32

# Screen capture
ltp-media-source --screen --region 0,0,1920,1080 --dimensions 60x16

# Screen capture of specific window (future)
ltp-media-source --window "Firefox" --dimensions 32x32

# Playlist
ltp-media-source --playlist videos.m3u --dimensions 16x16

# Common options
--name "Media Source"           # mDNS name
--rate 30                       # Output frame rate
--fit contain|cover|stretch     # Fit mode
--brightness 0.8                # Initial brightness
--gamma 2.2                     # Gamma correction
-v, --verbose                   # Verbose logging
```

---

## Playlist Support

### M3U Playlist
```m3u
#EXTM3U
#EXTINF:10,Logo Display
/path/to/logo.png
#EXTINF:30,Welcome Animation
/path/to/welcome.gif
#EXTINF:-1,Background Video
/path/to/background.mp4
```

### JSON Playlist
```json
{
  "name": "My Playlist",
  "loop": true,
  "items": [
    {
      "type": "image",
      "path": "/path/to/logo.png",
      "duration": 10,
      "transition": "fade"
    },
    {
      "type": "video",
      "path": "/path/to/video.mp4",
      "loop": false
    },
    {
      "type": "camera",
      "device": 0,
      "duration": 60
    }
  ]
}
```

---

## Streaming Frame Input

For integration with external applications, support receiving frames via:

### 1. Named Pipe (Unix)
```python
# External app writes frames to pipe
mkfifo /tmp/led_frames
# Media source reads from pipe
ltp-media-source --pipe /tmp/led_frames --dimensions 16x16 --format rgb
```

### 2. Shared Memory
```python
# For high-performance local integration
ltp-media-source --shm /led_matrix --dimensions 16x16
```

### 3. HTTP POST Endpoint
```python
# Accept frames via HTTP
ltp-media-source --http-input :8080 --dimensions 16x16

# External app posts frames
curl -X POST http://localhost:8080/frame \
  -H "Content-Type: application/octet-stream" \
  --data-binary @frame.raw
```

### 4. WebSocket
```python
# Real-time frame streaming
ltp-media-source --ws-input :8081 --dimensions 16x16
```

---

## Implementation Phases

### Phase 1: Core Infrastructure
1. MediaInput base class
2. FrameScaler with fit modes
3. MediaSource LTP integration
4. Basic CLI

### Phase 2: Static Inputs
1. Image input (Pillow)
2. GIF input (Pillow)
3. Controls for brightness, fit mode

### Phase 3: Video Inputs
1. Video file input (OpenCV)
2. Seek, pause, speed controls
3. Loop handling

### Phase 4: Live Inputs
1. Camera input (OpenCV)
2. Screen capture (mss)
3. Network stream (OpenCV/ffmpeg)

### Phase 5: Advanced Features
1. Playlist support
2. Transitions between sources
3. Effects pipeline
4. Pipe/API input

### Phase 6: Virtual Source Integration
1. Add to controller as virtual source type
2. Web UI for media selection
3. Persistent configuration

---

## Dependencies

```toml
[project.optional-dependencies]
media = [
    "opencv-python-headless>=4.8.0",  # Video/camera/stream
    "Pillow>=10.0.0",                  # Images/GIF
    "mss>=9.0.0",                      # Screen capture
    "imageio>=2.31.0",                 # Alternative video backend
    "imageio-ffmpeg>=0.4.8",           # FFmpeg for imageio
]
```

### Minimal Installation (images only)
```bash
pip install ltp[media-lite]  # Just Pillow
```

### Full Installation
```bash
pip install ltp[media]  # All media dependencies
```

---

## Performance Considerations

### Memory Management
- Don't load entire video into memory
- Use frame buffer for streams (configurable size)
- Release frames after sending

### CPU Usage
- Scaling is the main CPU cost
- Use INTER_AREA for quality downscaling
- Consider hardware acceleration (OpenCV CUDA) for high-res sources

### Frame Rate Matching
- Source FPS may differ from output FPS
- Options:
  - Drop frames if source > output
  - Duplicate frames if source < output
  - Variable rate (match source)

### Threading Model
```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Input Thread   │────▶│   Frame Queue   │────▶│  Output Thread  │
│  (read frames)  │     │  (buffer 2-5)   │     │  (scale & send) │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| File not found | Error message, black output |
| Video end | Loop or stop based on setting |
| Camera disconnect | Attempt reconnect, show error pattern |
| Stream timeout | Reconnect with backoff |
| Decode error | Skip frame, log warning |
| Invalid dimensions | Use defaults, log warning |

---

## Integration with Existing Sources

The media source can coexist with existing pattern sources:

```
Controller
├── Sources
│   ├── Pattern Source (rainbow)
│   ├── Pattern Source (fire)
│   └── Media Source (video.mp4)  ← New
└── Routes
    ├── Pattern → Matrix 1
    └── Media → Matrix 2
```

### Virtual Source Integration

Add media virtual source types to controller:

```python
VIRTUAL_SOURCE_TYPES = {
    # ... existing patterns ...
    "media_image": MediaImageSource,
    "media_video": MediaVideoSource,
    "media_camera": MediaCameraSource,
    "media_screen": MediaScreenSource,
}
```

---

## Web UI Integration (Future)

### Media Upload
- Upload images/videos via web interface
- Store in controller's media directory
- Preview in browser before routing

### Input Selection
- Dropdown to select input type
- File browser for local files
- URL input for streams
- Camera selector

### Playback Controls
- Play/Pause button
- Seek slider
- Speed control
- Loop toggle

---

## References

- [OpenCV Video I/O](https://docs.opencv.org/4.x/d8/dfe/classcv_1_1VideoCapture.html)
- [Pillow Image Module](https://pillow.readthedocs.io/en/stable/reference/Image.html)
- [mss Screen Capture](https://python-mss.readthedocs.io/)
- [FFmpeg Streaming](https://trac.ffmpeg.org/wiki/StreamingGuide)
