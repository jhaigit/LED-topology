# Section Routing - Future Directions

This document outlines potential approaches for routing data to specific sections of LED strips, rather than whole-device-to-whole-device routing.

## Current Architecture

Routes currently operate at the whole-device level:
- A route connects an entire source to an entire sink
- DataPackets contain pixel data starting at index 0
- Sinks display all incoming pixels from the beginning of the strip

```
Source (60 pixels) → Route → Sink (160 pixels)
                              ↓
                     Pixels 0-159 all updated
```

## Use Cases for Section Routing

1. **Multiple sources to one strip**: Different sources control different sections
2. **Partial updates**: Update only a portion of a large installation
3. **Logical grouping**: Treat physical strips as multiple logical displays
4. **Redundancy**: Same source to multiple sections for mirroring

## Option 1: Sink-Side Offset Configuration

**Complexity**: Low
**Protocol Changes**: None

Add `pixel_offset` to sink configuration. The sink writes incoming data starting at the configured offset instead of 0.

```python
class SerialSinkConfig:
    pixel_offset: int = 0  # Starting pixel for incoming data
```

**Pros**:
- No protocol changes
- Simple implementation
- Works with existing sources and controller

**Cons**:
- Requires multiple sink instances for multiple sections on one strip
- Or sink needs internal "slot" management
- Offset is static, not per-route

**Implementation**:
```python
# In sink's _handle_data_packet:
start = self.config.pixel_offset
end = start + len(incoming_pixels)
self._pixel_buffer[start:end] = incoming_pixels
```

## Option 2: Route-Level Section Configuration

**Complexity**: Medium
**Protocol Changes**: Minor (DataPacket offset field)

Add section parameters to Route configuration:

```python
@dataclass
class Route:
    # ... existing fields ...
    sink_offset: int = 0           # Starting pixel on sink
    sink_count: int | None = None  # Number of pixels (None = all)
```

Controller handles mapping when forwarding data:
1. Scale source data to `sink_count` pixels (not full sink)
2. Add offset to DataPacket
3. Sink applies offset when writing to buffer

**DataPacket Extension**:
```
Header (8 bytes): unchanged
Frame Header (6 bytes):  # Was 4 bytes
  color_format: uint8
  encoding: uint8
  pixel_count: uint16
  pixel_offset: uint16   # NEW: starting pixel index
```

**Pros**:
- Per-route configuration
- Scaling respects section size
- Multiple routes can target different sections

**Cons**:
- Protocol change (backward compatible with offset=0 default)
- Controller complexity increases
- Sink must handle offset in packet

**Web UI Addition**:
```
Route Configuration:
  Source: [dropdown]
  Sink: [dropdown]
  Section: [ ] Enable
    Offset: [___] pixels
    Count:  [___] pixels
```

## Option 3: Full Protocol Support

**Complexity**: High
**Protocol Changes**: Significant

Add section/range support throughout the protocol:

**subscribe message extension**:
```json
{
  "type": "subscribe",
  "target": {
    "dimensions": [160],
    "pixel_range": {
      "offset": 30,
      "count": 60
    },
    "color": "rgb",
    "rate": 30
  }
}
```

**stream_setup extension**:
```json
{
  "type": "stream_setup",
  "format": {"color": "rgb"},
  "section": {
    "offset": 30,
    "count": 60
  }
}
```

**Sink capability advertisement**:
```json
{
  "capabilities": {
    "sections": true,
    "max_concurrent_sections": 4
  }
}
```

**Pros**:
- Full flexibility
- Sources can be section-aware
- Sinks advertise section support
- Enables advanced features (section priorities, blending)

**Cons**:
- Significant protocol changes
- All components need updates
- Backward compatibility concerns
- Increased complexity throughout

## Option 4: Virtual Sink Abstraction

**Complexity**: Medium
**Protocol Changes**: None (controller internal)

Create "virtual sinks" in the controller that map to sections of physical sinks:

```python
# Controller configuration
virtual_sinks:
  - id: "living-room-left"
    physical_sink: "led-strip-001"
    offset: 0
    count: 80

  - id: "living-room-right"
    physical_sink: "led-strip-001"
    offset: 80
    count: 80
```

Sources and routes see virtual sinks as independent devices. Controller handles the mapping internally.

**Pros**:
- No protocol changes
- Clean abstraction
- Sources unaware of physical layout
- Easy to reconfigure

**Cons**:
- Controller must composite frames
- All data flows through controller (no direct mode)
- Latency for multi-section updates
- Controller memory usage for frame buffers

## Recommendation

For initial implementation, **Option 2 (Route-Level Section Configuration)** provides the best balance:

1. Minimal protocol changes (one new field)
2. Per-route flexibility
3. Backward compatible
4. Reasonable implementation complexity

**Implementation order**:
1. Add `sink_offset` and `sink_count` to Route dataclass
2. Add `pixel_offset` field to DataPacket (default 0)
3. Update controller proxy routing to apply section config
4. Update sink to respect packet offset
5. Add UI controls for section configuration

## Related Files

- `src/libltp/protocol.py` - DataPacket structure
- `src/libltp/types.py` - Route, message types
- `src/ltp_controller/router.py` - Routing engine
- `src/ltp_serial_sink/sink.py` - Sink data handling
- `src/libltp/topology.py` - Existing topology abstractions
