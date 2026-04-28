# Section Routing

**Version**: 2.0
**Date**: 2026-04-27

## 1. Overview

Section routing allows controlling specific pixel ranges on LED strips rather
than treating each sink as a single indivisible unit. LTP implements this at
the **sink level** through two complementary mechanisms:

- **Section fills** — direct pixel-range control via the `SinkController`
- **Sink groups** — ganging multiple physical sinks into one logical strip

Routes remain whole-device: a route connects a source to a sink (or group)
without per-route offset or count fields.

## 2. Section Fills

The `SinkController.fill_sections()` method fills arbitrary pixel ranges on
a single sink. Each section specifies a start index, end index, and RGB color.
Unfilled pixels receive a configurable background color.

### 2.1 API

```
POST /api/sinks/<sink_id>/fill
```

```json
{
  "type": "sections",
  "sections": [
    {"start": 0,  "end": 30, "color": [255, 0, 0]},
    {"start": 30, "end": 60, "color": [0, 255, 0]},
    {"start": 60, "end": 90, "color": [0, 0, 255]}
  ],
  "background": [0, 0, 0]
}
```

The same endpoint also supports `"type": "solid"` and `"type": "gradient"`.

### 2.2 Implementation

`fill_sections()` in `sink_control.py`:

1. Acquires or reuses a UDP stream to the sink
2. Allocates a pixel buffer filled with the background color
3. Paints each section's color into the corresponding index range
4. Sends the complete frame over UDP

```python
pixels = np.full((pixel_count, 3), background, dtype=np.uint8)
for section in sections:
    start = max(0, int(section["start"]))
    end = min(pixel_count, int(section["end"]))
    pixels[start:end] = section["color"]
sender.send(pixels, color_format, Encoding.RAW)
```

Sections are applied in order, so later sections overwrite earlier ones where
ranges overlap.

### 2.3 Related Fill Modes

The `SinkController` also provides:

| Method | Description |
|--------|-------------|
| `fill_solid(sink_id, color)` | Fill entire sink with one color |
| `fill_gradient(sink_id, colors)` | Linear gradient across all pixels |
| `set_pixel(sink_id, index, color)` | Set a single pixel |
| `paint_pixels(sink_id, data)` | Sparse pixel map, coordinate, range modes |
| `clear(sink_id)` | Fill with black |

## 3. Sink Groups

A `SinkGroup` gangs multiple physical sinks into one continuous logical strip.
Each member has a computed `pixel_offset` representing its position in the
virtual strip.

### 3.1 Data Model

```python
@dataclass
class SinkGroupMember:
    sink_id: str
    pixel_offset: int = 0     # computed by recompute()
    pixel_count: int = 0      # from live sink data
    reversed: bool = False     # reverse pixel order for this member

@dataclass
class SinkGroup:
    id: str                    # "sg-<hex>"
    name: str
    members: list[SinkGroupMember]
    total_pixels: int = 0      # sum of all member pixel_counts
```

### 3.2 Offset Computation

`SinkGroup.recompute(controller)` walks the member list in order, queries
each sink's live pixel count, and assigns sequential offsets:

```
Member 0: pixel_offset=0,   pixel_count=60   (sink A, 60 LEDs)
Member 1: pixel_offset=60,  pixel_count=100  (sink B, 100 LEDs)
Member 2: pixel_offset=160, pixel_count=60   (sink C, 60 LEDs)
total_pixels = 220
```

Offline sinks retain their last-known `pixel_count`. When a member comes
online or changes pixel count, `on_member_sink_changed()` triggers
recomputation.

### 3.3 Member Ordering and Reversal

Members are ordered by their position in the list. The `reversed` flag
inverts pixel order for a member, useful when a physical strip runs in the
opposite direction from its logical position in the group.

### 3.4 API

| Method | Endpoint | Description |
|--------|----------|-------------|
| Create | `POST /api/sink-groups` | Create group with member sink IDs |
| List | `GET /api/sink-groups` | List all groups with computed offsets |
| Get | `GET /api/sink-groups/<id>` | Single group detail |
| Update | `PUT /api/sink-groups/<id>` | Rename or change members |
| Delete | `DELETE /api/sink-groups/<id>` | Remove group |

### 3.5 Persistence

Groups are serialized to the controller's config file:

```yaml
sink_groups:
  - id: "sg-a1b2c3d4"
    name: "Living Room Strip"
    members:
      - sink_id: "550e8400-..."
        pixel_count: 60
        reversed: false
      - sink_id: "6ba7b810-..."
        pixel_count: 100
        reversed: false
    total_pixels: 160
```

## 4. How Routes Interact

Routes operate at the whole-device level. The `Route` dataclass has no
`sink_offset` or `sink_count` fields:

```python
@dataclass
class Route:
    source_id: str
    sink_id: str       # can reference a sink group ID
    mode: RouteMode
    transform: RouteTransform
    # ... no section fields
```

When a route targets a sink group, the router splits the source frame across
group members using each member's `pixel_offset` and `pixel_count`.

Section fills and routes serve different purposes:
- **Routes** carry streaming pixel data from sources (animations, effects)
- **Section fills** are one-shot direct commands (set this range to this color)

## 5. Use Cases

### 5.1 Multi-Zone Solid Colors

Fill different sections of a single strip with different colors for ambient
lighting zones, without running a source:

```json
{
  "type": "sections",
  "sections": [
    {"start": 0,   "end": 50,  "color": [255, 180, 100]},
    {"start": 50,  "end": 120, "color": [100, 100, 255]},
    {"start": 120, "end": 160, "color": [255, 180, 100]}
  ]
}
```

### 5.2 Ganged Strips

Three separate Arduino-driven strips mounted as one continuous run behind a
shelf. Create a sink group so sources see one 300-pixel strip:

```
Sink Group "Shelf LEDs" (300 pixels)
├── Arduino A: pixels 0-99
├── Arduino B: pixels 100-199 (reversed)
└── Arduino C: pixels 200-299
```

### 5.3 Sequence Automation

Sequence steps can use `fill_solid` actions targeting individual sinks.
Combined with schedule triggers on rules, this enables time-based zone
lighting without custom source code.

---

## Appendix: Alternative Considered — Route-Level Section Configuration

An alternative design was considered but not implemented: adding `sink_offset`
and `sink_count` fields to the `Route` dataclass so that each route could
target a specific pixel range on the sink.

### Design

```python
@dataclass
class Route:
    # ... existing fields ...
    sink_offset: int = 0           # starting pixel on sink
    sink_count: int | None = None  # number of pixels (None = all)
```

The controller would scale the source data to `sink_count` pixels and add an
offset to the `DataPacket`. This would also require a protocol extension:

```
Frame Header (6 bytes):  # was 4 bytes
  color_format: uint8
  encoding: uint8
  pixel_count: uint16
  pixel_offset: uint16   # new: starting pixel index
```

### Web UI (proposed)

```
Route Configuration:
  Source: [dropdown]
  Sink: [dropdown]
  Section: [ ] Enable
    Offset: [___] pixels
    Count:  [___] pixels
```

### Why It Was Not Chosen

The sink-level approach (section fills + sink groups) covers the primary use
cases without protocol changes or added complexity in the routing layer. The
route-level approach would be warranted if per-route pixel-range streaming
becomes necessary — for example, routing two different animation sources to
different sections of the same strip simultaneously. This remains an option
for future implementation if the need arises.

## 6. Related Files

| File | Purpose |
|------|---------|
| `src/ltp_controller/sink_control.py` | `SinkController` with `fill_sections()` |
| `src/ltp_controller/sink_group.py` | `SinkGroup`, `SinkGroupMember`, `SinkGroupManager` |
| `src/ltp_controller/router.py` | Route class (no section fields) |
| `src/ltp_controller/web/app.py` | `/api/sinks/<id>/fill` endpoint |
| `src/libltp/protocol.py` | DataPacket structure |
