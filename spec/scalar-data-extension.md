# LTP Protocol Extension: Scalar Data Channels

## Overview

This extension adds support for non-visual data sources (sensors) and non-RGB outputs (dimmers, switches, relays) to the LED Topology Protocol.

## Motivation

The core LTP protocol is optimized for LED pixel data streaming. However, many installations need to:

1. **Ingest sensor data** - Temperature, motion, light level, sound level, etc.
2. **Control non-visual outputs** - Dimmers, switches, relays, motors
3. **Map sensor values to visualizations** - Display temperature as a color gradient on LEDs

## Design Principles

1. **Backward compatible** - Existing devices continue to work unchanged
2. **Unified transport** - Use existing TCP control + UDP data channels
3. **Type-safe** - Explicit channel types with validation
4. **Discoverable** - Capabilities advertised via mDNS like other devices

---

## 1. New Data Formats

### 1.1 Extended ColorFormat Enum

Add new format types to the existing enum:

```python
class DataFormat(IntEnum):
    # Visual formats (existing)
    RGB = 0x01        # 3 bytes per pixel
    RGBW = 0x02       # 4 bytes per pixel
    HSV = 0x03        # 3 bytes per pixel
    GRAYSCALE = 0x04  # 1 byte per pixel (also used for single-channel dimmers)

    # Scalar formats (new)
    FLOAT32 = 0x10    # 4 bytes per channel, IEEE 754 float
    INT16 = 0x11      # 2 bytes per channel, signed integer
    UINT8 = 0x12      # 1 byte per channel, unsigned (same as GRAYSCALE but semantic)
    BOOLEAN = 0x13    # 1 bit per channel, packed into bytes
```

### 1.2 Channel Metadata

New optional field in CAPABILITY_RESPONSE for non-visual devices.

#### Individual Channels (heterogeneous data)

```json
{
  "channels": [
    {
      "index": 0,
      "id": "temperature",
      "name": "Temperature",
      "type": "float32",
      "unit": "°C",
      "min": -40.0,
      "max": 85.0,
      "readonly": true
    },
    {
      "index": 1,
      "id": "humidity",
      "name": "Humidity",
      "type": "float32",
      "unit": "%",
      "min": 0.0,
      "max": 100.0,
      "readonly": true
    },
    {
      "index": 2,
      "id": "motion",
      "name": "Motion Detected",
      "type": "boolean",
      "readonly": true
    }
  ]
}
```

#### Channel Arrays (homogeneous data)

For arrays of similar values (CPU cores, multi-zone sensors, etc.), use `channel_arrays`:

```json
{
  "channel_arrays": [
    {
      "id": "cpu_cores",
      "name": "CPU Core Usage",
      "type": "float32",
      "unit": "%",
      "min": 0.0,
      "max": 100.0,
      "count": 8,
      "start_index": 0,
      "readonly": true
    },
    {
      "id": "zone_temps",
      "name": "Zone Temperatures",
      "type": "float32",
      "unit": "°C",
      "min": -40.0,
      "max": 85.0,
      "count": 4,
      "start_index": 8,
      "readonly": true
    }
  ]
}
```

This defines:
- Channels 0-7: CPU core usage (8 × float32)
- Channels 8-11: Zone temperatures (4 × float32)
- Total: 12 channels, 48 bytes per packet

**Dynamic array sizes**: The `count` can change between capability queries (e.g., different CPU counts). Controllers should re-query capabilities when a device reconnects.

#### Mixed Channels and Arrays

Both `channels` and `channel_arrays` can coexist:

```json
{
  "channels": [
    {"index": 12, "id": "ambient_temp", "name": "Ambient", "type": "float32", "unit": "°C"}
  ],
  "channel_arrays": [
    {"id": "cpu_cores", "count": 8, "start_index": 0, "type": "float32"},
    {"id": "zone_temps", "count": 4, "start_index": 8, "type": "float32"}
  ]
}
```

For output devices (sinks):

```json
{
  "channels": [
    {
      "index": 0,
      "id": "dimmer1",
      "name": "Living Room Dimmer",
      "type": "uint8",
      "unit": "%",
      "min": 0,
      "max": 255,
      "readonly": false
    },
    {
      "index": 1,
      "id": "relay1",
      "name": "Fan Relay",
      "type": "boolean",
      "readonly": false
    }
  ]
}
```

---

## 2. Source Types for Sensors

### 2.1 Sensor Source Advertisement

mDNS service type: `_ltp-source._tcp.local.` (existing)

New TXT record fields:
- `data=scalar` (vs `data=visual` for LED sources)
- `channels=3` (number of data channels)
- `format=float32` (primary data format)

### 2.2 Sensor Source Capability Response

```json
{
  "id": "uuid-here",
  "name": "Environment Sensor",
  "description": "Temperature, humidity, and motion sensor",
  "source_type": "sensor",
  "data_type": "scalar",
  "output_dimensions": [3],
  "data_format": "float32",
  "rate": 1,
  "channels": [
    {"index": 0, "id": "temp", "name": "Temperature", "unit": "°C", "min": -40, "max": 85},
    {"index": 1, "id": "humidity", "name": "Humidity", "unit": "%", "min": 0, "max": 100},
    {"index": 2, "id": "motion", "name": "Motion", "type": "boolean"}
  ]
}
```

### 2.3 Sensor Data Packet

Binary format (extends existing DataPacket):

```
Packet Header (8 bytes) - unchanged
Frame Header (4 bytes):
  - Byte 0: DataFormat (0x10 = FLOAT32)
  - Byte 1: Encoding (0x00 = RAW)
  - Bytes 2-3: Channel count (16-bit)
Channel Data:
  - N × 4 bytes for FLOAT32
  - N × 2 bytes for INT16
  - N × 1 bytes for UINT8
  - ceil(N/8) bytes for BOOLEAN (bit-packed)
```

---

## 3. Sink Types for Non-Visual Outputs

### 3.1 Output Sink Advertisement

mDNS service type: `_ltp-sink._tcp.local.` (existing)

New TXT record fields:
- `output=scalar` (vs `output=visual` for LED sinks)
- `channels=4` (number of output channels)
- `type=dimmer` or `type=switch` or `type=mixed`

### 3.2 Output Sink Capability Response

```json
{
  "id": "uuid-here",
  "name": "4-Channel Dimmer",
  "description": "4-zone PWM dimmer controller",
  "device_type": "custom",
  "output_type": "scalar",
  "pixels": 4,
  "dimensions": [4],
  "data_formats": ["uint8", "grayscale"],
  "max_refresh_hz": 60,
  "channels": [
    {"index": 0, "id": "zone1", "name": "Zone 1", "type": "uint8"},
    {"index": 1, "id": "zone2", "name": "Zone 2", "type": "uint8"},
    {"index": 2, "id": "zone3", "name": "Zone 3", "type": "uint8"},
    {"index": 3, "id": "zone4", "name": "Zone 4", "type": "uint8"}
  ]
}
```

### 3.3 Switch/Relay Sink

```json
{
  "id": "uuid-here",
  "name": "8-Channel Relay",
  "description": "8 independent relay outputs",
  "device_type": "custom",
  "output_type": "scalar",
  "pixels": 8,
  "dimensions": [8],
  "data_formats": ["boolean"],
  "channels": [
    {"index": 0, "id": "relay1", "name": "Relay 1", "type": "boolean"},
    {"index": 1, "id": "relay2", "name": "Relay 2", "type": "boolean"},
    // ... etc
  ]
}
```

---

## 4. Controller Integration

### 4.1 Sensor → Visualization Pipeline

The controller can create routes from scalar sources to visual sinks with transformations:

```json
{
  "source_id": "sensor-uuid",
  "sink_id": "led-strip-uuid",
  "transform": {
    "type": "scalar_to_visual",
    "mapping": {
      "channel": "temperature",
      "visualization": "bar_graph",
      "color_map": "thermal",
      "range": {"min": 15, "max": 35}
    }
  }
}
```

### 4.2 Visual → Scalar Pipeline

For LED sources controlling dimmers:

```json
{
  "source_id": "pattern-uuid",
  "sink_id": "dimmer-uuid",
  "transform": {
    "type": "visual_to_scalar",
    "mapping": "brightness",
    "channels": [0, 1, 2, 3]
  }
}
```

This extracts brightness from RGB pixels and sends to dimmer channels.

### 4.3 Scalar → Scalar Pipeline

Direct sensor-to-output mapping:

```json
{
  "source_id": "motion-sensor-uuid",
  "sink_id": "relay-uuid",
  "transform": {
    "type": "scalar_to_scalar",
    "mappings": [
      {
        "source_channel": "motion",
        "sink_channel": 0,
        "condition": {"type": "threshold", "value": true}
      }
    ]
  }
}
```

---

## 5. Implementation Phases

### Phase 1: Core Protocol Extension
- [ ] Add new DataFormat enum values
- [ ] Add channel metadata to capability responses
- [ ] Update DataPacket to handle new formats
- [ ] Add `data_type` field to source/sink advertisements

### Phase 2: Sensor Source Support
- [ ] Create SensorSource base class
- [ ] Implement example sensor sources (system metrics, mock sensors)
- [ ] Add sensor discovery and display in controller UI

### Phase 3: Scalar Sink Support
- [ ] Create ScalarSink base class
- [ ] Implement example sinks (GPIO dimmer, relay controller)
- [ ] Add scalar sink display in controller UI

### Phase 4: Transformation Pipeline
- [ ] Implement scalar_to_visual transforms
- [ ] Implement visual_to_scalar transforms
- [ ] Implement scalar_to_scalar transforms
- [ ] Add transformation configuration UI

### Phase 5: Example Implementations
- [ ] ESP32 temperature/humidity sensor (MicroPython)
- [ ] ESP32 4-channel PWM dimmer
- [ ] Raspberry Pi GPIO relay controller
- [ ] Integration with Home Assistant

---

## 6. Backward Compatibility

### 6.1 Existing Devices
- All existing sources and sinks continue to work
- `data_type` defaults to "visual" if not specified
- Missing `channels` metadata means treat as pixel data

### 6.2 Mixed Networks
- Controllers can route between visual and scalar devices
- Transformations handle format conversion automatically
- UI clearly distinguishes device types

---

## 7. Alternative: Control-Based Approach

For low-frequency sensor data (< 1 Hz), the existing control system works:

```python
# Sensor exposes read-only controls
controls = [
    NumberControl(
        id="temperature",
        name="Temperature",
        value=23.5,
        min=-40, max=85,
        unit="°C",
        readonly=True,
        group="sensors"
    ),
    BooleanControl(
        id="motion",
        name="Motion Detected",
        value=False,
        readonly=True,
        group="sensors"
    )
]
```

Controller can poll these via CONTROL_GET or receive CONTROL_CHANGED notifications.

**Pros**: No protocol changes needed
**Cons**: Not suitable for high-frequency data, polling overhead

---

## 8. Summary

| Feature | Approach | Status |
|---------|----------|--------|
| High-frequency sensor data | New DataFormat types | Proposed |
| Low-frequency sensor data | Read-only controls | Already works |
| Dimmer outputs | GRAYSCALE format | Already works |
| Switch/relay outputs | BOOLEAN DataFormat | Proposed |
| Channel metadata | Capability extension | Proposed |
| Sensor → LED visualization | Transform pipeline | Proposed |
| LED → Dimmer extraction | Transform pipeline | Proposed |

The protocol extension maintains backward compatibility while enabling a rich ecosystem of sensor inputs and non-visual outputs, all managed through the unified LTP controller.
