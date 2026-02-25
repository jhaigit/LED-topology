# Proposal: Extended Pixel Formats — Monochrome, Grayscale, and Bit Depths

## Problem

The LTP protocol currently sends all pixel data as RGB (3 bytes/pixel), even to
sinks that can only display monochrome.  A 72x40 OLED needs 8640 bytes per
frame in RGB but only 360 bytes as 1-bit packed — a **24x bandwidth reduction**.
This wastes bandwidth, increases latency, and forces every monochrome sink to
implement its own RGB-to-mono conversion.

The existing `ColorFormat` enum has a `GRAYSCALE` entry (1 byte/pixel) but it is
never used in practice.  There is no 1-bit packed format at all.

## Current State

```
ColorFormat (uint8, in frame header byte 0):
  0x01 = RGB        3 bytes/pixel
  0x02 = RGBW       4 bytes/pixel
  0x03 = HSV        3 bytes/pixel
  0x04 = GRAYSCALE  1 byte/pixel  (defined but unused)
```

All sinks advertise `color_formats: ["rgb"]`.  The controller always sends RGB
regardless of what the sink supports.  Format negotiation exists in
`stream_setup` but is ignored.

## Proposed Changes

### 1. New ColorFormat Values

Add two new formats for sub-byte and single-channel pixel data:

```
  0x04 = GRAYSCALE    1 byte/pixel   (8-bit luminance, already defined)
  0x05 = MONO_PACKED  1 bit/pixel    (MSB-first, rows padded to byte boundary)
```

The `bytes_per_pixel` property becomes a `bits_per_pixel` property for
`MONO_PACKED`, with a helper for computing packed byte counts:

```python
class ColorFormat(IntEnum):
    RGB         = 0x01
    RGBW        = 0x02
    HSV         = 0x03
    GRAYSCALE   = 0x04
    MONO_PACKED = 0x05

    @property
    def bits_per_pixel(self) -> int:
        return {
            ColorFormat.RGB: 24,
            ColorFormat.RGBW: 32,
            ColorFormat.HSV: 24,
            ColorFormat.GRAYSCALE: 8,
            ColorFormat.MONO_PACKED: 1,
        }[self]

    @property
    def bytes_per_pixel(self) -> int:
        """Bytes per pixel (for formats >= 8 bpp).  Raises for packed formats."""
        bpp = self.bits_per_pixel
        if bpp < 8:
            raise ValueError(f"{self.name} is sub-byte; use packed_byte_count()")
        return bpp // 8

    @staticmethod
    def packed_byte_count(pixel_count: int, bits_per_pixel: int) -> int:
        """Byte count for bit-packed formats."""
        return (pixel_count * bits_per_pixel + 7) // 8
```

### 2. MONO_PACKED Wire Format

For `MONO_PACKED`, pixel data is a bit array packed MSB-first:

```
Byte 0:  [px0 px1 px2 px3 px4 px5 px6 px7]
Byte 1:  [px8 px9 px10 ...]
...
```

- Bit = 1 means pixel is ON (white/lit), 0 means OFF (black).
- If the pixel count is not a multiple of 8, the trailing bits in the last
  byte are zero-padded.
- For a 72x40 display: `ceil(2880 / 8) = 360 bytes` per frame.
- For a 72x40 display using row-padded packing (each row starts on a byte
  boundary): `ceil(72 / 8) * 40 = 9 * 40 = 360 bytes` (happens to be the same
  because 72 is divisible by 8).

**Row padding**: For matrix topologies, each row starts on a byte boundary.
This simplifies receiver code at the cost of at most `height` bytes.  For
widths divisible by 8 (like 72) there is zero waste.

### 3. Capability Advertisement

Sinks already advertise `color_formats` as a list.  Monochrome sinks would
advertise their native format alongside RGB:

```json
{
  "color_formats": ["rgb", "mono_packed"],
  "preferred_format": "mono_packed"
}
```

The new optional `preferred_format` field tells the controller which format
to use when multiple are supported.  If omitted, the controller picks the
first entry in `color_formats`.

### 4. Stream Setup Negotiation

`stream_setup` already carries a `format.color` field.  The controller should
choose the sink's preferred format (or native format) when setting up a stream:

```json
{"type": "stream_setup", "seq": 1,
 "format": {"color": "mono_packed", "encoding": "raw"}}
```

The sink confirms or rejects in `stream_setup_response`.  If the sink doesn't
support the requested format, it responds with an error and the controller
falls back to RGB.

### 5. Controller-Side Format Conversion

The controller (router and sink_control) already operates on RGB numpy arrays
internally.  Conversion happens at send time in `DataSender.send()`:

```python
def send(self, pixels, color_format=ColorFormat.RGB, ...):
    if color_format == ColorFormat.MONO_PACKED:
        pixels = self._rgb_to_mono(pixels)
    elif color_format == ColorFormat.GRAYSCALE:
        pixels = self._rgb_to_grayscale(pixels)
    # ... chunk and send
```

Conversion functions:

```python
def _rgb_to_grayscale(self, rgb: np.ndarray) -> np.ndarray:
    """RGB (N,3) -> Grayscale (N,1).  ITU-R BT.601 luma."""
    return (rgb[:, 0] * 77 + rgb[:, 1] * 150 + rgb[:, 2] * 29) >> 8

def _rgb_to_mono(self, rgb: np.ndarray, threshold: int = 128) -> np.ndarray:
    """RGB (N,3) -> packed bit array."""
    luma = self._rgb_to_grayscale(rgb)
    bits = (luma >= threshold).astype(np.uint8)
    return np.packbits(bits, bitorder='big')
```

### 6. Chunking Interaction

The existing chunk system (chunk_index in reserved byte) works with all
formats.  `MAX_CHUNK_PIXELS` stays at 480 for RGB but can be adjusted per
format since the bottleneck is MTU, not pixel count:

```python
MAX_CHUNK_BYTES = 1440  # payload bytes per chunk (MTU-safe)

def max_chunk_pixels(color_format: ColorFormat) -> int:
    if color_format == ColorFormat.MONO_PACKED:
        return MAX_CHUNK_BYTES * 8  # 11520 pixels per chunk
    return MAX_CHUNK_BYTES // color_format.bytes_per_pixel
```

For the 72x40 OLED at MONO_PACKED: 360 bytes total, fits in a single packet —
no chunking needed.  For GRAYSCALE: 2880 bytes, needs 2 chunks.

### 7. Firmware-Side Changes

#### OLED sink (MONO_PACKED)

The receiver already parses `colorFormat` from the frame header.  Add a branch
for `MONO_PACKED`:

```c
if (colorFormat == COLOR_FMT_MONO_PACKED) {
    // Read packed bytes directly, pass to u8g2 drawXBM or manual unpack
    uint16_t byteCount = (pixelCount + 7) / 8;
    udp.read(oledFramebuffer, byteCount);
    // Map directly into u8g2 buffer (same bit layout)
}
```

This is dramatically simpler than the current RGB→luminance→threshold path,
and the u8g2 framebuffer is already a packed bit array in the same format.

#### LED sinks (RGB, unchanged)

No changes needed.  They continue to advertise `color_formats: ["rgb"]` and
receive RGB data as before.

### 8. Backward Compatibility

| Change | Impact |
|--------|--------|
| New `ColorFormat` values (0x05) | Old code ignores unknown values; `stream_setup` negotiation prevents sending unsupported formats |
| `preferred_format` in capability response | New optional field; old controllers ignore it |
| `stream_setup` format selection | Old controllers always request RGB; new controllers check sink capabilities |
| Chunking with sub-byte formats | `chunk_index * MAX_CHUNK_PIXELS` math still works; `pixel_count` in frame header is in pixels, not bytes |

Old sinks never receive MONO_PACKED because the controller only sends it when
the sink advertises support.  Old controllers never send it because they always
request RGB.

### 9. Bandwidth Comparison (72x40 OLED)

| Format | Bytes/frame | Chunks needed | vs RGB |
|--------|-------------|---------------|--------|
| RGB | 8640 | 6 | 1x |
| GRAYSCALE | 2880 | 2 | 3x smaller |
| MONO_PACKED | 360 | 1 | 24x smaller |

### 10. Implementation Order

1. **Phase 1 — GRAYSCALE support** (low risk, high value)
   - Add conversion in `DataSender`
   - Wire up `stream_setup` format negotiation in controller
   - OLED sink: accept GRAYSCALE, skip luminance calculation, threshold to 1-bit
   - Any future grayscale e-ink or OLED sinks benefit immediately

2. **Phase 2 — MONO_PACKED support** (optimal for OLED)
   - Add packing in `DataSender`
   - OLED sink: receive packed bits, write directly to u8g2 framebuffer
   - Single-packet frames for 72x40, zero conversion overhead on MCU

3. **Phase 3 — Dithering option** (quality improvement)
   - Add optional Floyd-Steinberg dithering in the controller's RGB→mono path
   - Exposed as a per-sink or per-route transform option
   - Better visual quality for photographic images on 1-bit displays

## Open Questions

1. **Row padding vs linear packing for MONO_PACKED?**  Row padding (each row
   starts on a byte boundary) maps cleanly to display framebuffers but wastes
   bits for non-multiple-of-8 widths.  Linear packing is more compact but
   requires the receiver to extract bits across byte boundaries.
   *Recommendation: row-padded, since most displays have widths divisible by 8.*

2. **Should GRAYSCALE support variable bit depth (4-bit, 2-bit)?**  Some
   e-ink displays use 4-bit grayscale (16 levels).  This could be encoded as
   a separate `GRAY4_PACKED` format or as a parameter on GRAYSCALE.
   *Recommendation: defer to a future proposal; 8-bit grayscale covers
   most use cases.*

3. **Threshold as a control?**  The RGB→mono threshold (default 128) could
   be exposed as a per-sink control via the existing control channel.
   *Recommendation: yes, add it as an optional control on mono-capable sinks.*

4. **Should sources output non-RGB formats?**  Currently all sources produce
   RGB.  A source designed for mono displays could output MONO_PACKED directly.
   *Recommendation: keep sources as RGB; conversion is cheap and centralized
   in the controller.*
