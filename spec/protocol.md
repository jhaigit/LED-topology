# LED Topology Protocol Specification

**Version**: 0.1.0-draft
**Status**: Draft
**Date**: 2026-01-13

## 1. Overview

The LED Topology Protocol (LTP) defines a system for discovering, describing, and streaming data between display devices (sinks) and data sources. It enables automatic discovery of devices on a local network and provides a flexible format for transmitting display data to various types of LED-based displays.

### 1.1 Goals

- Automatic discovery of display devices and data sources via Zeroconf (mDNS/DNS-SD)
- Support for diverse display types: single LEDs, LED strings, 2D arrays, and irregular topologies
- Flexible data format supporting various color spaces and encodings
- Low-latency streaming for real-time display updates
- Simple enough for microcontroller implementation

### 1.2 Architecture

```
┌─────────────────┐         ┌─────────────────┐
│   Data Source   │         │  Display Device │
│     (Source)    │         │     (Sink)      │
├─────────────────┤         ├─────────────────┤
│ Generates data  │────────▶│ Receives data   │
│ Advertises via  │         │ Advertises via  │
│ mDNS            │         │ mDNS            │
└─────────────────┘         └─────────────────┘
        │                           │
        └───────────┬───────────────┘
                    ▼
           ┌─────────────────┐
           │   Controller    │
           │   (Optional)    │
           ├─────────────────┤
           │ Routes sources  │
           │ to sinks        │
           │ Transforms data │
           └─────────────────┘
```

## 2. Service Discovery

LTP uses DNS-SD (DNS Service Discovery) over mDNS (Multicast DNS) for zero-configuration networking, commonly known as Zeroconf or Bonjour.

### 2.1 Service Types

#### Display Devices (Sinks)

```
_ltp-sink._tcp.local.
```

#### Data Sources

```
_ltp-source._tcp.local.
```

#### Controllers

```
_ltp-controller._tcp.local.
```

### 2.2 TXT Record Fields

#### Common Fields

| Field | Description | Example |
|-------|-------------|---------|
| `ver` | Protocol version | `0.1` |
| `name` | Human-readable name | `Living Room Strip` |
| `desc` | Human-readable description | `RGB strip under kitchen cabinets` |
| `id` | Unique device identifier (UUID) | `550e8400-e29b-41d4-a716-446655440000` |
| `ctrl` | Has configurable controls | `1` (yes) or `0` (no) |

#### Sink-Specific Fields

| Field | Description | Example |
|-------|-------------|---------|
| `type` | Device type | `single`, `string`, `array`, `matrix`, `custom` |
| `pixels` | Total pixel count | `60` |
| `dim` | Dimensions (WxH or length) | `60` or `16x16` |
| `color` | Color format supported | `rgb`, `rgbw`, `w`, `rgb+w` |
| `rate` | Max refresh rate (Hz) | `60` |
| `port` | Data port (if different from SRV) | `5000` |

#### Source-Specific Fields

| Field | Description | Example |
|-------|-------------|---------|
| `output` | Output dimensions | `60` or `16x16` |
| `color` | Color format produced | `rgb` |
| `rate` | Output rate (Hz) | `30` |
| `mode` | `stream`, `static`, `interactive` | `stream` |

### 2.3 Example mDNS Advertisement

**Sink (LED Strip)**:
```
kitchen-strip._ltp-sink._tcp.local. 120 IN SRV 0 0 5000 kitchen-strip.local.
kitchen-strip._ltp-sink._tcp.local. 120 IN TXT "ver=0.1" "name=Kitchen LED Strip" "desc=WS2812B strip under cabinets" "id=550e8400-e29b-41d4-a716-446655440000" "type=string" "pixels=120" "dim=120" "color=rgb" "rate=60" "ctrl=1"
```

**Source (Audio Visualizer)**:
```
audio-viz._ltp-source._tcp.local. 120 IN SRV 0 0 5001 workstation.local.
audio-viz._ltp-source._tcp.local. 120 IN TXT "ver=0.1" "name=Audio Visualizer" "desc=Real-time audio spectrum display" "id=660e8400-e29b-41d4-a716-446655440001" "output=120" "color=rgb" "rate=30" "mode=stream" "ctrl=1"
```

## 3. Transport Protocol

### 3.1 Connection Types

LTP uses two connection types:

1. **Control Channel (TCP)**: Port specified in mDNS SRV record
   - Device capability queries
   - Configuration commands
   - Connection management

2. **Data Channel (UDP)**: Port negotiated via control channel
   - Pixel data streaming
   - Low-latency, best-effort delivery

### 3.2 Control Channel Messages

All control messages use JSON encoding over TCP with newline delimiters.

#### 3.2.1 Capability Query

**Request**:
```json
{
  "type": "capability_request",
  "seq": 1
}
```

**Response**:
```json
{
  "type": "capability_response",
  "seq": 1,
  "device": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "Kitchen LED Strip",
    "description": "WS2812B RGB strip mounted under kitchen cabinets, 120 LEDs at 60/m",
    "type": "string",
    "pixels": 120,
    "dimensions": [120],
    "topology": "linear",
    "color_formats": ["rgb", "hsv"],
    "max_refresh_hz": 60,
    "protocol_version": "0.1",
    "controls": [
      {
        "id": "brightness",
        "name": "Global Brightness",
        "description": "Master brightness level applied to all pixels",
        "type": "number",
        "value": 255,
        "min": 0,
        "max": 255
      },
      {
        "id": "local_mode",
        "name": "Local Control Mode",
        "description": "When enabled, device runs built-in patterns ignoring network input",
        "type": "boolean",
        "value": false
      }
    ]
  }
}
```

#### 3.2.2 Stream Setup

**Request**:
```json
{
  "type": "stream_setup",
  "seq": 2,
  "format": {
    "color": "rgb",
    "encoding": "raw",
    "compression": "none"
  },
  "udp_port": 5001
}
```

**Response**:
```json
{
  "type": "stream_setup_response",
  "seq": 2,
  "status": "ok",
  "udp_port": 5002,
  "stream_id": "abc123"
}
```

#### 3.2.3 Stream Control

```json
{
  "type": "stream_control",
  "seq": 3,
  "stream_id": "abc123",
  "action": "start" | "stop" | "pause"
}
```

#### 3.2.4 Control Get

Retrieve current values of one or more device controls.

**Request**:
```json
{
  "type": "control_get",
  "seq": 4,
  "ids": ["brightness", "local_mode"]
}
```

**Response**:
```json
{
  "type": "control_get_response",
  "seq": 4,
  "status": "ok",
  "values": {
    "brightness": 255,
    "local_mode": false
  }
}
```

Omit `ids` to retrieve all controls.

#### 3.2.5 Control Set

Set values of one or more device controls.

**Request**:
```json
{
  "type": "control_set",
  "seq": 5,
  "values": {
    "brightness": 128,
    "local_mode": true
  }
}
```

**Response**:
```json
{
  "type": "control_set_response",
  "seq": 5,
  "status": "ok",
  "applied": {
    "brightness": 128,
    "local_mode": true
  }
}
```

If a value cannot be applied (out of range, invalid type), the response includes an error:

```json
{
  "type": "control_set_response",
  "seq": 5,
  "status": "partial",
  "applied": {
    "local_mode": true
  },
  "errors": {
    "brightness": {
      "code": 6,
      "message": "Value 300 exceeds maximum of 255"
    }
  }
}
```

#### 3.2.6 Control Changed Notification

Devices MAY send unsolicited notifications when controls change (e.g., via physical button).

```json
{
  "type": "control_changed",
  "values": {
    "local_mode": true
  }
}
```

### 3.3 Data Channel Format

Data packets are sent via UDP using a binary format for efficiency.

#### 3.3.1 Packet Header (8 bytes)

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|     Magic (0x4C54)    |  Ver  |     Flags     |   Reserved    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                       Sequence Number                         |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

- **Magic**: `0x4C54` ("LT" in ASCII)
- **Ver**: Protocol version (4 bits, currently `0x0`)
- **Flags**:
  - Bit 0: Fragment flag (more fragments follow)
  - Bit 1: Priority frame
  - Bit 2-7: Reserved
- **Sequence Number**: 32-bit incrementing sequence for ordering

#### 3.3.2 Frame Header (4 bytes, follows packet header)

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|  Color Fmt    |   Encoding    |         Pixel Count           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

- **Color Format**:
  - `0x01`: RGB (3 bytes per pixel)
  - `0x02`: RGBW (4 bytes per pixel)
  - `0x03`: HSV (3 bytes per pixel)
  - `0x04`: Grayscale (1 byte per pixel)

- **Encoding**:
  - `0x00`: Raw (uncompressed)
  - `0x01`: RLE (run-length encoded)
  - `0x02`: Delta (difference from previous frame)

#### 3.3.3 Pixel Data

Immediately follows the frame header. Format depends on color format and encoding.

**Raw RGB Example** (3 bytes per pixel):
```
[R0][G0][B0][R1][G1][B1][R2][G2][B2]...
```

**RLE Encoded** (count + pixel):
```
[Count][R][G][B][Count][R][G][B]...
```
Where Count is 1-255 pixels of the same color.

## 4. Device Controls Specification

Devices (both sinks and sources) can expose arbitrary controls for configuration. Controls are defined with enough metadata that clients can automatically generate appropriate UI elements without prior knowledge of the specific control.

### 4.1 Control Definition Schema

Each control is defined as a JSON object with the following fields:

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier for the control (alphanumeric, underscores) |
| `name` | Yes | Human-readable short name for display |
| `description` | Yes | Human-readable description explaining what the control does |
| `type` | Yes | Data type (see section 4.2) |
| `value` | Yes | Current value |
| `readonly` | No | If `true`, control is informational only (default: `false`) |
| `group` | No | Grouping category for UI organization |

Additional fields depend on the `type`.

### 4.2 Control Types

#### 4.2.1 Boolean

A true/false toggle.

```json
{
  "id": "local_mode",
  "name": "Local Control Mode",
  "description": "When enabled, device runs built-in patterns ignoring network input",
  "type": "boolean",
  "value": false
}
```

#### 4.2.2 Number

Numeric value with optional range constraints.

| Field | Required | Description |
|-------|----------|-------------|
| `min` | No | Minimum allowed value |
| `max` | No | Maximum allowed value |
| `step` | No | Increment step (default: 1) |
| `unit` | No | Unit label for display (e.g., "Hz", "%", "ms") |

```json
{
  "id": "brightness",
  "name": "Global Brightness",
  "description": "Master brightness level applied to all pixels",
  "type": "number",
  "value": 255,
  "min": 0,
  "max": 255,
  "step": 1,
  "unit": ""
}
```

```json
{
  "id": "gamma",
  "name": "Gamma Correction",
  "description": "Gamma correction factor for color output",
  "type": "number",
  "value": 2.2,
  "min": 1.0,
  "max": 3.0,
  "step": 0.1
}
```

#### 4.2.3 String

Free-form text input.

| Field | Required | Description |
|-------|----------|-------------|
| `minLength` | No | Minimum string length |
| `maxLength` | No | Maximum string length |
| `pattern` | No | Regex pattern for validation |

```json
{
  "id": "device_name",
  "name": "Device Name",
  "description": "Human-readable name for this device",
  "type": "string",
  "value": "Kitchen Strip",
  "maxLength": 64
}
```

#### 4.2.4 Enum

Selection from a predefined list of options.

| Field | Required | Description |
|-------|----------|-------------|
| `options` | Yes | Array of allowed values with labels |

Each option has:
- `value`: The actual value stored/transmitted
- `label`: Human-readable display label
- `description`: Optional explanation of this option

```json
{
  "id": "color_order",
  "name": "Color Order",
  "description": "Physical wiring order of color channels",
  "type": "enum",
  "value": "grb",
  "options": [
    {"value": "rgb", "label": "RGB", "description": "Red, Green, Blue"},
    {"value": "grb", "label": "GRB", "description": "Green, Red, Blue (WS2812)"},
    {"value": "bgr", "label": "BGR", "description": "Blue, Green, Red"},
    {"value": "rbg", "label": "RBG", "description": "Red, Blue, Green"}
  ]
}
```

#### 4.2.5 Color

RGB or RGBA color value.

| Field | Required | Description |
|-------|----------|-------------|
| `alpha` | No | Whether alpha channel is supported (default: `false`) |

Value is a string in hex format: `"#RRGGBB"` or `"#RRGGBBAA"`.

```json
{
  "id": "fallback_color",
  "name": "Fallback Color",
  "description": "Color displayed when no data source is connected",
  "type": "color",
  "value": "#000000",
  "alpha": false
}
```

#### 4.2.6 Action

A trigger button that performs an action. Has no persistent value.

| Field | Required | Description |
|-------|----------|-------------|
| `confirm` | No | If `true`, UI should confirm before triggering |

```json
{
  "id": "factory_reset",
  "name": "Factory Reset",
  "description": "Reset all settings to factory defaults",
  "type": "action",
  "confirm": true
}
```

To trigger an action, send a `control_set` with the action id and value `true`:

```json
{
  "type": "control_set",
  "seq": 10,
  "values": {
    "factory_reset": true
  }
}
```

#### 4.2.7 Array

A list of values of a single type.

| Field | Required | Description |
|-------|----------|-------------|
| `items` | Yes | Type definition for array elements |
| `minItems` | No | Minimum array length |
| `maxItems` | No | Maximum array length |

```json
{
  "id": "segment_lengths",
  "name": "Segment Lengths",
  "description": "Length of each logical segment for multi-zone effects",
  "type": "array",
  "items": {"type": "number", "min": 1, "max": 1000},
  "value": [30, 30, 30, 30],
  "minItems": 1,
  "maxItems": 16
}
```

### 4.3 Control Groups

Controls can be organized into groups for UI presentation:

```json
{
  "id": "brightness",
  "name": "Global Brightness",
  "description": "Master brightness level",
  "type": "number",
  "value": 255,
  "min": 0,
  "max": 255,
  "group": "output"
}
```

Common group names (not enforced, but recommended):

| Group | Description |
|-------|-------------|
| `general` | Basic device settings |
| `output` | Output/display settings |
| `input` | Input/source settings |
| `network` | Network configuration |
| `hardware` | Hardware-specific settings |
| `advanced` | Advanced/expert settings |

### 4.4 Read-Only Controls

Controls can be marked read-only to expose status information:

```json
{
  "id": "temperature",
  "name": "Device Temperature",
  "description": "Current temperature of the LED controller",
  "type": "number",
  "value": 45.2,
  "unit": "°C",
  "readonly": true,
  "group": "hardware"
}
```

```json
{
  "id": "firmware_version",
  "name": "Firmware Version",
  "description": "Currently installed firmware version",
  "type": "string",
  "value": "1.2.3",
  "readonly": true,
  "group": "general"
}
```

### 4.5 Source-Specific Controls Example

Sources can also expose controls:

```json
{
  "controls": [
    {
      "id": "sensitivity",
      "name": "Audio Sensitivity",
      "description": "Sensitivity of audio level detection",
      "type": "number",
      "value": 50,
      "min": 0,
      "max": 100,
      "unit": "%",
      "group": "input"
    },
    {
      "id": "frequency_band",
      "name": "Frequency Band",
      "description": "Audio frequency range to visualize",
      "type": "enum",
      "value": "full",
      "options": [
        {"value": "bass", "label": "Bass", "description": "20-250 Hz"},
        {"value": "mid", "label": "Mid", "description": "250-4000 Hz"},
        {"value": "treble", "label": "Treble", "description": "4000-20000 Hz"},
        {"value": "full", "label": "Full Spectrum", "description": "All frequencies"}
      ],
      "group": "input"
    },
    {
      "id": "color_palette",
      "name": "Color Palette",
      "description": "Color scheme for visualization",
      "type": "enum",
      "value": "rainbow",
      "options": [
        {"value": "rainbow", "label": "Rainbow"},
        {"value": "fire", "label": "Fire"},
        {"value": "ocean", "label": "Ocean"},
        {"value": "custom", "label": "Custom"}
      ],
      "group": "output"
    }
  ]
}
```

## 5. Device Topology Description

### 5.1 Linear (1D)

Simple string of pixels indexed 0 to N-1.

```json
{
  "topology": "linear",
  "dimensions": [60]
}
```

### 5.2 Matrix (2D)

Rectangular grid with defined pixel ordering.

```json
{
  "topology": "matrix",
  "dimensions": [16, 16],
  "origin": "top-left",
  "order": "row-major",
  "serpentine": true
}
```

- **origin**: Starting corner (`top-left`, `top-right`, `bottom-left`, `bottom-right`)
- **order**: Pixel ordering (`row-major`, `column-major`)
- **serpentine**: Whether alternate rows/columns reverse direction

### 5.3 Custom Topology

For irregular arrangements, a coordinate map can be provided.

```json
{
  "topology": "custom",
  "pixels": 25,
  "coordinates": [
    {"index": 0, "x": 0.0, "y": 0.0},
    {"index": 1, "x": 0.5, "y": 0.1},
    {"index": 2, "x": 1.0, "y": 0.0}
  ]
}
```

Coordinates are normalized to 0.0-1.0 range. This allows sources to generate spatially-aware patterns for non-standard arrangements.

## 6. Data Source Protocol

### 6.1 Source Registration

Sources advertise via mDNS and accept connections from controllers or sinks.

### 6.2 Source Output Negotiation

When a controller or sink connects to a source:

**Request**:
```json
{
  "type": "subscribe",
  "seq": 1,
  "target": {
    "dimensions": [120],
    "color": "rgb",
    "rate": 30
  }
}
```

**Response**:
```json
{
  "type": "subscribe_response",
  "seq": 1,
  "status": "ok",
  "actual": {
    "dimensions": [120],
    "color": "rgb",
    "rate": 30
  },
  "stream_id": "xyz789"
}
```

The source may adjust dimensions or rate based on its capabilities.

### 6.3 Built-in Source Types

Sources should advertise their type for UI purposes:

| Type | Description |
|------|-------------|
| `audio` | Audio-reactive visualization |
| `video` | Video/image display |
| `pattern` | Algorithmic patterns |
| `clock` | Time-based display |
| `sensor` | Sensor data visualization |
| `network` | Network activity display |
| `custom` | User-defined source |

## 7. Controller Protocol

Controllers act as intermediaries that:

1. Discover sources and sinks
2. Route source data to appropriate sinks
3. Transform data (scaling, color conversion, mapping)

### 7.1 Routing Configuration

```json
{
  "type": "route_create",
  "seq": 1,
  "route": {
    "source_id": "660e8400-e29b-41d4-a716-446655440001",
    "sink_id": "550e8400-e29b-41d4-a716-446655440000",
    "transform": {
      "scale": "fit",
      "color_map": "none"
    }
  }
}
```

### 7.2 Transform Options

| Option | Values | Description |
|--------|--------|-------------|
| `scale` | `none`, `fit`, `fill`, `stretch` | How to adapt source dimensions to sink |
| `color_map` | `none`, `brightness`, `custom` | Color transformation |
| `mirror` | `none`, `horizontal`, `vertical`, `both` | Mirroring |

## 8. Security Considerations

### 8.1 Network Scope

LTP is designed for trusted local networks. Implementations SHOULD:

- Bind only to local network interfaces
- Implement rate limiting to prevent abuse
- Validate all input data

### 8.2 Future Considerations

Future versions may add:

- TLS for control channel encryption
- Authentication tokens
- Access control lists

## 9. Implementation Notes

### 9.1 Microcontroller Considerations

For resource-constrained devices:

- Control channel JSON parsing can use streaming parser
- Data channel can use fixed-size buffers
- mDNS can use existing libraries (e.g., ESP8266mDNS)

### 9.2 Recommended Defaults

| Parameter | Default |
|-----------|---------|
| Control port | 5353 (after mDNS) |
| Data port | 5354 |
| Max packet size | 1400 bytes (MTU-safe) |
| Default refresh rate | 30 Hz |

## 10. Example Flows

### 10.1 Direct Source to Sink

```
1. Sink advertises via mDNS: kitchen-strip._ltp-sink._tcp.local
2. Source discovers sink via mDNS browse
3. Source connects to sink control port (TCP)
4. Source queries sink capabilities
5. Source sends stream_setup request
6. Sink responds with UDP port
7. Source streams pixel data to sink UDP port
```

### 10.2 Controller-Mediated

```
1. Controller discovers all sources and sinks via mDNS
2. User configures route: audio-viz -> kitchen-strip
3. Controller subscribes to audio-viz source
4. Controller sets up stream to kitchen-strip sink
5. Controller receives data from source, transforms, forwards to sink
```

## Appendix A: Color Format Specifications

### RGB
- 8 bits per channel
- Order: Red, Green, Blue
- Range: 0-255 per channel

### RGBW
- 8 bits per channel
- Order: Red, Green, Blue, White
- Range: 0-255 per channel

### HSV
- H: 0-255 (mapped from 0-360 degrees)
- S: 0-255 (0-100%)
- V: 0-255 (0-100%)

## Appendix B: Reserved Port Ranges

| Port Range | Purpose |
|------------|---------|
| 5353 | mDNS (standard) |
| 5354-5399 | LTP control channels |
| 5400-5499 | LTP data channels |

## Appendix C: Error Codes

| Code | Name | Description |
|------|------|-------------|
| 0 | OK | Success |
| 1 | INVALID_FORMAT | Unsupported format requested |
| 2 | BUSY | Device busy with another stream |
| 3 | RATE_LIMIT | Rate limit exceeded |
| 4 | NOT_FOUND | Stream or device not found |
| 5 | INTERNAL | Internal device error |
| 6 | INVALID_VALUE | Control value out of range or wrong type |
| 7 | READONLY | Attempted to set a read-only control |
