# LTP Example Implementations Specification

**Version**: 0.1.0-draft
**Date**: 2026-01-13

## 1. Overview

This document specifies three example applications that implement the LED Topology Protocol (LTP):

1. **ltp-source** - Data source generator with multiple pattern types
2. **ltp-sink** - Virtual display sink with terminal and GUI visualizations
3. **ltp-controller** - Discovery and routing controller with web interface

All implementations target Linux and are written in Python 3.10+ for clarity and ease of modification.

## 2. Common Architecture

### 2.1 Shared Library: `libltp`

A shared Python package providing:

```
libltp/
├── __init__.py
├── discovery.py      # mDNS advertisement and browsing
├── protocol.py       # Message serialization/deserialization
├── transport.py      # TCP control channel, UDP data channel
├── controls.py       # Control definition and validation
├── topology.py       # Topology description utilities
└── types.py          # Common data types and enums
```

### 2.2 Dependencies

| Package | Purpose |
|---------|---------|
| `zeroconf` | mDNS/DNS-SD implementation |
| `asyncio` | Async networking |
| `numpy` | Efficient pixel buffer manipulation |
| `rich` | Terminal visualization |
| `pygame` | GUI visualization (optional) |
| `flask` | Web interface for controller |
| `pydantic` | Data validation |

### 2.3 Configuration

All applications use YAML configuration files with the following common structure:

```yaml
# Common configuration
device:
  id: "auto"  # "auto" generates UUID, or specify explicit UUID
  name: "My Device"
  description: "Human-readable description"

network:
  interface: "auto"  # "auto" or specific interface name
  control_port: 0    # 0 = auto-assign
  data_port: 0       # 0 = auto-assign

logging:
  level: "info"      # debug, info, warning, error
  file: null         # Optional log file path
```

## 3. ltp-source: Data Source Generator

### 3.1 Purpose

Generates pixel data streams using configurable pattern generators. Supports multiple simultaneous output streams with different configurations.

### 3.2 Command Line Interface

```bash
# Run with config file
ltp-source --config source.yaml

# Run with inline pattern
ltp-source --name "Rainbow" --pattern rainbow --dimensions 60 --rate 30

# List available patterns
ltp-source --list-patterns
```

### 3.3 Configuration Schema

```yaml
device:
  id: "auto"
  name: "Pattern Generator"
  description: "Generates animated LED patterns"

outputs:
  - name: "rainbow-60"
    description: "60-pixel rainbow animation"
    dimensions: [60]
    color_format: "rgb"
    rate: 30
    pattern:
      type: "rainbow"
      params:
        speed: 1.0
        saturation: 1.0
        brightness: 1.0

  - name: "matrix-16x16"
    description: "16x16 matrix plasma effect"
    dimensions: [16, 16]
    color_format: "rgb"
    rate: 24
    pattern:
      type: "plasma"
      params:
        scale: 4.0
        speed: 0.5

controls:
  - id: "master_brightness"
    name: "Master Brightness"
    description: "Global brightness multiplier for all outputs"
    type: "number"
    value: 1.0
    min: 0.0
    max: 1.0
    step: 0.05
    group: "output"
```

### 3.4 Built-in Pattern Types

| Pattern | Description | Parameters |
|---------|-------------|------------|
| `solid` | Static solid color | `color` |
| `rainbow` | Moving rainbow gradient | `speed`, `saturation`, `brightness` |
| `chase` | Chasing dot pattern | `color`, `speed`, `tail_length`, `count` |
| `pulse` | Breathing/pulsing effect | `color`, `speed`, `min_brightness` |
| `gradient` | Static or animated gradient | `colors[]`, `speed`, `direction` |
| `plasma` | Plasma/lava lamp effect | `scale`, `speed`, `palette` |
| `fire` | Fire simulation | `cooling`, `sparking`, `palette` |
| `noise` | Perlin noise patterns | `scale`, `speed`, `octaves` |
| `strobe` | Strobe/flash effect | `color`, `on_time`, `off_time` |
| `segments` | Per-segment colors | `segments[]`, `blend` |
| `clock` | Time display (for matrices) | `format`, `color`, `bg_color` |
| `script` | Custom Python script | `script_path`, `params` |

### 3.5 Custom Pattern Scripts

Users can define custom patterns via Python scripts:

```python
# custom_pattern.py
from libltp.patterns import PatternBase
import numpy as np

class CustomPattern(PatternBase):
    """My custom pattern."""

    def __init__(self, params: dict):
        super().__init__(params)
        self.my_param = params.get("my_param", 1.0)

    def render(self, buffer: np.ndarray, time: float) -> None:
        """Render pattern into RGB buffer.

        Args:
            buffer: numpy array of shape (pixels, 3) for 1D or (height, width, 3) for 2D
            time: current time in seconds (for animation)
        """
        # Fill with custom pattern logic
        buffer[:] = [255, 0, 0]  # All red
```

### 3.6 Source Controls

Sources expose controls for real-time adjustment:

```yaml
controls:
  # Pattern-specific controls are auto-generated from pattern params
  # Additional custom controls can be defined:
  - id: "active_pattern"
    name: "Active Pattern"
    description: "Currently running pattern"
    type: "enum"
    value: "rainbow"
    options:
      - value: "rainbow"
        label: "Rainbow"
      - value: "fire"
        label: "Fire"
      - value: "plasma"
        label: "Plasma"
    group: "pattern"
```

## 4. ltp-sink: Virtual Display Sink

### 4.1 Purpose

Receives pixel data and visualizes it using various renderers. Supports multiple visualization backends for development and testing.

### 4.2 Command Line Interface

```bash
# Run with config file
ltp-sink --config sink.yaml

# Run with inline configuration
ltp-sink --name "Test Strip" --type string --pixels 60 --renderer terminal

# List available renderers
ltp-sink --list-renderers
```

### 4.3 Configuration Schema

```yaml
device:
  id: "auto"
  name: "Virtual LED Strip"
  description: "60-pixel WS2812B simulation"

display:
  type: "string"          # string, matrix, custom
  pixels: 60              # Total pixel count
  dimensions: [60]        # [length] or [width, height]
  color_format: "rgb"     # rgb, rgbw, hsv
  max_refresh_hz: 60

  # For matrix type
  topology:
    origin: "top-left"
    order: "row-major"
    serpentine: true

  # For custom type
  coordinates: []         # List of {index, x, y} or path to JSON file

renderer:
  type: "terminal"        # terminal, gui, headless, multi

  # Terminal renderer options
  terminal:
    style: "block"        # block, braille, ascii, bar
    width: 80             # Terminal width (auto = detect)
    show_info: true       # Show FPS, data rate info

  # GUI renderer options
  gui:
    pixel_size: 20        # Pixels per LED
    spacing: 2            # Gap between LEDs
    background: "#1a1a1a"
    window_title: "LTP Sink"

  # Headless renderer (for testing/logging)
  headless:
    log_frames: false
    log_interval: 1.0     # Seconds between frame logs

controls:
  - id: "brightness"
    name: "Global Brightness"
    description: "Master brightness applied to display"
    type: "number"
    value: 255
    min: 0
    max: 255
    group: "output"

  - id: "gamma"
    name: "Gamma Correction"
    description: "Gamma value for color correction"
    type: "number"
    value: 2.2
    min: 1.0
    max: 3.0
    step: 0.1
    group: "output"

  - id: "test_mode"
    name: "Test Mode"
    description: "Display test pattern instead of input"
    type: "boolean"
    value: false
    group: "general"

  - id: "test_pattern"
    name: "Test Pattern"
    description: "Pattern to display in test mode"
    type: "enum"
    value: "rgb_sweep"
    options:
      - value: "rgb_sweep"
        label: "RGB Sweep"
      - value: "white"
        label: "All White"
      - value: "index"
        label: "Index Numbers"
    group: "general"
```

### 4.4 Renderer Types

#### 4.4.1 Terminal Renderer

Displays LED state in terminal using Unicode characters.

**Styles:**

| Style | Description | Example |
|-------|-------------|---------|
| `block` | Full block characters with 24-bit color | `████████` |
| `braille` | Braille dots for higher resolution | `⣿⣿⣿⣿` |
| `ascii` | ASCII characters with intensity mapping | `@@##==--` |
| `bar` | Horizontal bar graph | `▁▂▃▄▅▆▇█` |

**Matrix Layout:**
```
┌─────────────────────────────────┐
│  Virtual LED Matrix (16x16)     │
├─────────────────────────────────┤
│ ████████████████████████████████│
│ ████████████████████████████████│
│ ...                             │
├─────────────────────────────────┤
│ FPS: 30.0 | Data: 1.4 KB/s      │
└─────────────────────────────────┘
```

#### 4.4.2 GUI Renderer

Opens a graphical window using pygame/SDL.

**Features:**
- Resizable window
- Configurable LED appearance (round, square, with bloom effect)
- Click-to-inspect individual pixel values
- Screenshot capability
- Topology visualization overlay

#### 4.4.3 Headless Renderer

No visualization; useful for:
- Automated testing
- Performance benchmarking
- Logging data for analysis

#### 4.4.4 Multi Renderer

Combines multiple renderers simultaneously:

```yaml
renderer:
  type: "multi"
  renderers:
    - type: "terminal"
      terminal:
        style: "block"
    - type: "headless"
      headless:
        log_frames: true
```

### 4.5 Virtual Sink Networks

Multiple sinks can be defined in a single config to simulate complex installations:

```yaml
sinks:
  - name: "Strip 1"
    display:
      type: "string"
      pixels: 60
    renderer:
      type: "terminal"

  - name: "Strip 2"
    display:
      type: "string"
      pixels: 120
    renderer:
      type: "terminal"

  - name: "Matrix"
    display:
      type: "matrix"
      dimensions: [16, 16]
    renderer:
      type: "gui"
```

## 5. ltp-controller: Discovery and Routing Controller

### 5.1 Purpose

Central hub for discovering, monitoring, and routing between sources and sinks. Provides web-based UI for configuration.

### 5.2 Command Line Interface

```bash
# Run controller
ltp-controller --config controller.yaml

# Run with web UI on specific port
ltp-controller --web-port 8080

# CLI mode (no web UI)
ltp-controller --cli
```

### 5.3 Configuration Schema

```yaml
device:
  id: "auto"
  name: "LTP Controller"
  description: "Central routing controller"

web:
  enabled: true
  port: 8080
  host: "0.0.0.0"

discovery:
  browse_interval: 5.0    # Seconds between mDNS browse refreshes
  timeout: 10.0           # Device timeout (mark offline)

routes:
  # Pre-configured routes (loaded on startup)
  - name: "Kitchen Animation"
    source: "rainbow-generator"      # Source name or ID
    sink: "kitchen-strip"            # Sink name or ID
    enabled: true
    transform:
      scale: "fit"
      brightness: 0.8

persistence:
  enabled: true
  file: "~/.config/ltp/routes.yaml"  # Save routes to file
```

### 5.4 Web Interface

#### 5.4.1 Dashboard (`/`)

Overview showing:
- Discovered sources (online/offline status)
- Discovered sinks (online/offline status)
- Active routes with data flow indicators
- System statistics (total data rate, active streams)

#### 5.4.2 Sources Page (`/sources`)

- List all discovered sources
- View source details (capabilities, controls)
- Preview source output (if terminal allows)
- Adjust source controls

#### 5.4.3 Sinks Page (`/sinks`)

- List all discovered sinks
- View sink details (capabilities, topology, controls)
- Adjust sink controls
- Direct fill controls:
  - Solid color fill with color picker
  - Gradient fill between two or more colors
  - Section-based fills with start/end pixel ranges
  - Clear (fill with black)

#### 5.4.4 Routes Page (`/routes`)

- Create/edit/delete routes
- Drag-and-drop source-to-sink connections
- Real-time status display:
  - Source and sink dimensions (e.g., "60 → 30")
  - Scaling indicator when dimensions differ
  - No-data warning if no frames received
  - Frame counter
- Configure transforms:
  - Dimension scaling modes:
    - `fit` - Interpolate to match (default)
    - `pad_black` - Extend shorter source with black pixels
    - `pad_repeat` - Tile/repeat source pattern to fill
    - `truncate` - Cut longer source to fit
    - `stretch` - Stretch to match dimensions
    - `none` - No scaling, truncate/pad as needed
  - Brightness adjustment
  - Gamma correction
  - Mirroring (horizontal/vertical)
- Enable/disable routes
- Auto-reconnection when devices restart

#### 5.4.5 API Endpoints

RESTful API for programmatic control:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sources` | GET | List all sources |
| `/api/sources/{id}` | GET | Get source details |
| `/api/sources/{id}/controls` | GET/PUT | Get/set source controls |
| `/api/sinks` | GET | List all sinks |
| `/api/sinks/{id}` | GET | Get sink details |
| `/api/sinks/{id}/controls` | GET/PUT | Get/set sink controls |
| `/api/sinks/{id}/fill` | POST | Fill sink with color/pattern |
| `/api/sinks/{id}/clear` | POST | Clear sink (fill with black) |
| `/api/routes` | GET/POST | List/create routes |
| `/api/routes/{id}` | GET/PUT/DELETE | Get/update/delete route |
| `/api/routes/{id}/enable` | POST | Enable route |
| `/api/routes/{id}/disable` | POST | Disable route |
| `/api/status` | GET | Get system status summary |

**Sink Fill API:**

```json
// POST /api/sinks/{id}/fill

// Solid color fill
{"type": "solid", "color": [255, 0, 0]}

// Gradient fill
{"type": "gradient", "colors": [[255, 0, 0], [0, 0, 255]]}

// Section-based fill
{
  "type": "sections",
  "sections": [
    {"start": 0, "end": 30, "color": [255, 0, 0]},
    {"start": 30, "end": 60, "color": [0, 255, 0]}
  ],
  "background": [0, 0, 0]
}
```

### 5.5 Routing Engine

The controller manages data flow between sources and sinks:

```
┌────────────┐     ┌─────────────────┐     ┌────────────┐
│   Source   │────▶│   Controller    │────▶│    Sink    │
│            │     │                 │     │            │
│ UDP stream │     │ - Receives data │     │ UDP stream │
│            │     │ - Transforms    │     │            │
│            │     │ - Forwards      │     │            │
└────────────┘     └─────────────────┘     └────────────┘
```

**Transform Pipeline:**

1. **Receive** - Decode incoming frame from source
2. **Scale** - Resize to match sink dimensions
3. **Color Map** - Apply brightness, gamma, color adjustments
4. **Mirror** - Apply mirroring if configured
5. **Encode** - Encode for sink's preferred format
6. **Send** - Forward to sink

### 5.6 Direct Mode

For low-latency applications, controller can configure direct source-to-sink connections:

```yaml
routes:
  - name: "Direct Route"
    source: "audio-viz"
    sink: "led-strip"
    mode: "direct"  # Source sends directly to sink, controller only monitors
```

### 5.7 Direct Sink Control

The controller can send data directly to sinks without requiring a source or route. This is useful for:

- Setting static colors or patterns
- Testing sink connectivity
- User-controlled "painting" of LEDs
- Default/fallback displays

**SinkController** manages direct sink control:

```python
# Programmatic usage
sink_controller = SinkController(controller)

# Fill with solid color (sends once, no continuous streaming)
await sink_controller.fill_solid(sink_id, (255, 0, 0))

# Fill with gradient
await sink_controller.fill_gradient(sink_id, [(255, 0, 0), (0, 0, 255)])

# Fill specific sections
await sink_controller.fill_sections(sink_id, [
    {"start": 0, "end": 30, "color": [255, 0, 0]},
    {"start": 30, "end": 60, "color": [0, 255, 0]},
])

# Clear (fill with black)
await sink_controller.clear(sink_id)
```

The web UI provides color pickers and section editors for interactive control.

### 5.8 Size Mismatch Handling

When source and sink have different dimensions, the controller:

1. **Detects** the mismatch and displays it in the web UI
2. **Applies** the configured scale mode to adapt the data
3. **Warns** if no data is being received (e.g., incompatible formats)

Routes track dimension information and display warnings:
- Dimensions shown as "60 → 30" with an "S" badge when scaling is active
- "No data" warning appears if frames stop flowing after connection

## 6. Project Structure

```
LED-topology/
├── spec/
│   ├── protocol.md
│   └── implementations.md
├── src/
│   ├── libltp/
│   │   ├── __init__.py
│   │   ├── discovery.py
│   │   ├── protocol.py
│   │   ├── transport.py
│   │   ├── controls.py
│   │   ├── topology.py
│   │   └── types.py
│   ├── ltp_source/
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── cli.py
│   │   ├── source.py
│   │   └── patterns/
│   │       ├── __init__.py
│   │       ├── base.py
│   │       ├── solid.py
│   │       ├── rainbow.py
│   │       ├── chase.py
│   │       ├── plasma.py
│   │       ├── fire.py
│   │       └── ...
│   ├── ltp_sink/
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── cli.py
│   │   ├── sink.py
│   │   └── renderers/
│   │       ├── __init__.py
│   │       ├── base.py
│   │       ├── terminal.py
│   │       ├── gui.py
│   │       └── headless.py
│   └── ltp_controller/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── controller.py
│       ├── router.py
│       ├── sink_control.py    # Direct sink fill control
│       ├── web/
│       │   ├── __init__.py
│       │   ├── app.py
│       │   └── templates/
│       │       ├── base.html
│       │       ├── dashboard.html
│       │       ├── sources.html
│       │       ├── sinks.html
│       │       ├── routes.html
│       │       └── preview.html
│       └── static/
│           ├── css/
│           └── js/
├── configs/
│   ├── source-example.yaml
│   ├── sink-example.yaml
│   └── controller-example.yaml
├── tests/
│   ├── test_libltp/
│   ├── test_source/
│   ├── test_sink/
│   └── test_controller/
├── pyproject.toml
├── requirements.txt
└── README.md
```

## 7. Development Priorities

### Phase 1: Core Library
- Protocol message handling
- mDNS discovery
- TCP/UDP transport
- Control system

### Phase 2: Basic Sink
- Terminal renderer
- Basic control channel
- Test patterns

### Phase 3: Basic Source
- Pattern framework
- Rainbow and solid patterns
- mDNS advertisement

### Phase 4: Integration
- Source-to-sink streaming
- Multiple pattern types
- GUI renderer

### Phase 5: Controller
- Discovery aggregation
- Route management
- Web interface

### Phase 6: Polish
- Additional patterns
- Documentation
- Example configs
- Tests
