# Art-Net Integration Plan

This document outlines the plan to integrate Art-Net protocol with the LTP ecosystem, supporting both **receiving** Art-Net data and **sending** to Art-Net devices.

## Use Cases

### 1. Art-Net Receiver (Input to LTP)
Receive Art-Net from external controllers and feed into LTP:
```
External Controller (QLC+, Resolume, MadMapper)
    │ Art-Net UDP
    ▼
┌─────────────────┐      LTP       ┌─────────────┐
│ Art-Net Receiver│ ──────────────▶│  LTP Sink   │──▶ LEDs
│ (LTP Source)    │                │  (Serial)   │
└─────────────────┘                └─────────────┘
```

### 2. Art-Net Sender (Output from LTP)
Send LTP data to existing Art-Net devices:
```
┌─────────────────┐      LTP       ┌─────────────────┐
│   LTP Source    │ ──────────────▶│ Art-Net Sender  │
│   (Pattern)     │                │ (LTP Sink)      │
└─────────────────┘                └────────┬────────┘
                                            │ Art-Net UDP
                                            ▼
                              ┌─────────────────────────┐
                              │ Art-Net Device (WLED,   │
                              │ Commercial Controller)  │
                              └─────────────────────────┘
```

### 3. Art-Net Proxy/Bridge
LTP controller routes between Art-Net and other protocols:
```
Art-Net In ──▶ ┌────────────────┐ ──▶ Serial Out
               │ LTP Controller │
sACN In    ──▶ │   (Router)     │ ──▶ Art-Net Out
               └────────────────┘
```

### 4. Direct Source to Art-Net
LTP source sends directly to Art-Net device (no controller):
```
┌─────────────────┐    Art-Net UDP    ┌─────────────────┐
│   LTP Source    │ ─────────────────▶│   WLED Device   │
│   (Pattern)     │    (Direct Mode)  │                 │
└─────────────────┘                   └─────────────────┘
```

---

## Art-Net Protocol Overview

Art-Net is a protocol for transmitting DMX512 data over Ethernet:
- **Transport**: UDP port 6454
- **Universe**: 512 DMX channels (170 RGB pixels or 128 RGBW pixels)
- **Addressing**: 15-bit port-address (Net:SubNet:Universe) supporting 32,768 universes
- **Discovery**: ArtPoll/ArtPollReply broadcast mechanism

### Key Packet Types

| Packet | OpCode | Purpose |
|--------|--------|---------|
| ArtDmx | 0x5000 | DMX channel data |
| ArtPoll | 0x2000 | Discovery request (broadcast) |
| ArtPollReply | 0x2100 | Node announcement |

### Pixels per Universe

| Color Mode | Channels/Pixel | Pixels/Universe |
|------------|----------------|-----------------|
| RGB | 3 | 170 |
| RGBW | 4 | 128 |

---

## Protocol Comparison: LTP vs Art-Net vs sACN

### Overview

| Feature | LTP (Current) | Art-Net | sACN (E1.31) |
|---------|---------------|---------|--------------|
| **Transport** | TCP control + UDP data | UDP only | UDP multicast/unicast |
| **Port** | Dynamic (mDNS) | 6454 fixed | 5568 fixed |
| **Discovery** | mDNS/Zeroconf | ArtPoll broadcast | Universe subscription |
| **Addressing** | Pixel index (0-N) | Universe:Channel | Universe:Channel |
| **Max per packet** | ~1024 bytes | 512 channels | 512 channels |
| **Max pixels/packet** | ~340 RGB | 170 RGB | 170 RGB |
| **Sequencing** | 32-bit in data packet | 8-bit (1-255) | 8-bit (0-255) |
| **Error detection** | XOR checksum | None | CRC (optional) |
| **Compression** | RLE supported | None | None |
| **Priority/Merging** | Via controller | None (last wins) | Built-in priority (0-200) |
| **Sync mechanism** | None (single stream) | ArtSync packet | Sync universe |
| **Color format** | Explicit (RGB/GRB/RGBW) | Implicit (convention) | Implicit (convention) |

### sACN (E1.31) Details

sACN (Streaming ACN) is an ANSI standard (E1.31) for DMX over IP:

| Aspect | Details |
|--------|---------|
| **Full Name** | ANSI E1.31 Streaming Architecture for Control Networks |
| **Transport** | UDP multicast (239.255.x.x) or unicast |
| **Port** | 5568 |
| **Universe Range** | 1-63999 |
| **Multicast Address** | 239.255.{universe_high}.{universe_low} |
| **Priority** | 0-200 (higher wins, enables merging) |
| **Sync** | Universe 0 can sync others |
| **Preview** | Preview flag for visualization without output |

### Feature Coverage Analysis

#### What LTP Has That Art-Net/sACN Lack

| LTP Feature | Art-Net | sACN | Notes |
|-------------|---------|------|-------|
| **Bidirectional control channel** | ❌ | ❌ | LTP has TCP control for capabilities, controls |
| **Device discovery with capabilities** | Partial | ❌ | ArtPoll is basic; sACN has no discovery |
| **Named controls (brightness, etc.)** | ❌ | ❌ | Art-Net/sACN are data-only protocols |
| **Input events (buttons, encoders)** | ❌ | ❌ | No upstream from device to controller |
| **Pixel-level addressing** | ❌ | ❌ | Must manually calculate universe/channel |
| **Compression (RLE)** | ❌ | ❌ | Always full 512 bytes per universe |
| **Topology description** | ❌ | ❌ | No concept of matrix/serpentine layout |
| **Device type metadata** | ❌ | ❌ | Just universes, no semantic info |

#### What Art-Net/sACN Have That LTP Lacks

| Feature | Art-Net | sACN | LTP Status |
|---------|---------|------|------------|
| **Industry standard** | ✅ | ✅ | Custom protocol |
| **Wide device support** | ✅ | ✅ | Limited to LTP devices |
| **Multi-universe sync** | ✅ ArtSync | ✅ Sync universe | ❌ Not implemented |
| **Priority merging** | ❌ | ✅ | ❌ Not implemented |
| **Preview mode** | ❌ | ✅ | ❌ Not implemented |
| **Universe concept** | ✅ | ✅ | ❌ Pixel-only addressing |
| **DMX compatibility** | ✅ Direct | ✅ Direct | Requires conversion |

### Recommended LTP Enhancements for Protocol Bridge

To fully support Art-Net/sACN bridging, consider:

1. **Universe Abstraction Layer**
   ```python
   class UniverseAddress:
       universe: int      # 0-32767 for Art-Net, 1-63999 for sACN
       channel: int       # 1-512

       def to_pixel_index(self, color_format: ColorFormat) -> int:
           channels_per_pixel = color_format.bytes_per_pixel
           return (self.universe * 170) + ((self.channel - 1) // channels_per_pixel)
   ```

2. **Multi-Universe Sync**
   - Buffer frames until all universes received
   - Configurable timeout for partial frames
   - Optional sync packet support

3. **Priority Support** (for sACN compatibility)
   ```python
   class StreamPriority:
       priority: int = 100  # 0-200, higher wins
       # When multiple sources send to same sink, highest priority wins
   ```

4. **Preview Mode**
   - Flag to indicate data is for visualization only
   - Sink can choose to display or ignore

---

## Implementation Architecture (Revised)

### Bidirectional Module: `ltp_artnet`

```
src/ltp_artnet/
├── __init__.py
├── __main__.py
├── cli.py                # Command-line interface
├── protocol.py           # Art-Net packet parsing/building
├── receiver.py           # Art-Net input (UDP listener)
├── sender.py             # Art-Net output (UDP sender)
├── discovery.py          # ArtPoll/ArtPollReply
├── universe_mapper.py    # Universe <-> pixel mapping
├── artnet_source.py      # LTP source that receives Art-Net
└── artnet_sink.py        # LTP sink that sends Art-Net
```

### Component: Art-Net Sender (LTP Sink → Art-Net Device)

```
┌─────────────────────────────────────────────────────────────┐
│                  Art-Net Sender Sink                         │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  LTP Data    │    │   Universe   │    │   Art-Net    │  │
│  │  Receiver    │───▶│   Splitter   │───▶│   Sender     │  │
│  │  (UDP)       │    │              │    │  (UDP 6454)  │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│                                                 │           │
│                                                 ▼           │
│                                    ┌────────────────────┐  │
│                                    │ WLED / Commercial  │  │
│                                    │ Art-Net Device     │  │
│                                    └────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Component: Art-Net Receiver (Art-Net → LTP Source)

```
┌─────────────────────────────────────────────────────────────┐
│                  Art-Net Receiver Source                     │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  ArtNet      │    │   Universe   │    │  LTP Source  │  │
│  │  Receiver    │───▶│   Aggregator │───▶│   (TCP+UDP)  │  │
│  │  (UDP 6454)  │    │              │    │              │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│         │                                       │           │
│         ▼                                       ▼           │
│  ┌──────────────┐                      ┌──────────────┐    │
│  │  ArtPoll     │                      │ LTP Controller│    │
│  │  Responder   │                      │ or Direct Sink│    │
│  └──────────────┘                      └──────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation Tasks

### Phase 1: Core Art-Net Receiver

1. **UDP Socket Handler**
   - Bind to port 6454
   - Support both broadcast and unicast reception
   - Async handling with asyncio

2. **Packet Parser**
   - Validate "Art-Net\0" magic header
   - Parse OpCode (little-endian)
   - Extract ArtDmx fields: sequence, universe, length, data

3. **ArtDmx Handler**
   ```python
   class ArtDmxPacket:
       sequence: int       # 1-255, 0 = disabled
       physical: int       # Physical port (informational)
       universe: int       # 15-bit port-address
       length: int         # 2-512 channels
       data: bytes         # DMX channel values
   ```

### Phase 2: Universe Routing

1. **Universe Configuration**
   ```python
   class UniverseConfig:
       universe: int           # Art-Net universe number
       pixel_offset: int       # Starting pixel in output buffer
       pixel_count: int        # Pixels in this universe (max 170 RGB)
       color_order: str        # RGB, GRB, BGR, etc.
   ```

2. **Multi-Universe Aggregation**
   - Buffer incoming universe data
   - Combine multiple universes into single pixel buffer
   - Handle out-of-order packets using sequence numbers

3. **Pixel Mapping**
   ```
   Universe 0: pixels 0-169
   Universe 1: pixels 170-339
   Universe 2: pixels 340-509
   ...
   ```

### Phase 3: Output Integration

1. **Serial Output** (existing V2Renderer)
   - Forward pixel data to serial device
   - Apply brightness/gamma if configured

2. **LTP Network Output** (new)
   - Act as LTP source, advertising to controller
   - Forward Art-Net data to LTP sinks

3. **Direct GPIO** (future)
   - For Raspberry Pi with direct LED control

### Phase 4: Discovery Support

1. **ArtPoll Response**
   - Listen for ArtPoll broadcasts
   - Respond with ArtPollReply containing:
     - Node IP address
     - Short/long name
     - Supported universes
     - Node type (0x00 = Art-Net to DMX node)

2. **Node Configuration**
   ```python
   class ArtNetNodeConfig:
       short_name: str         # 18 chars max
       long_name: str          # 64 chars max
       oem_code: int           # OEM identifier
       esta_code: int          # ESTA manufacturer code
       universes: list[int]    # Subscribed universes
   ```

---

## Configuration

### Command-Line Interface

```bash
# Basic usage - 170 pixels, universe 0
ltp-artnet-sink --pixels 170 --port /dev/ttyACM0

# Multi-universe for 500 pixels (3 universes)
ltp-artnet-sink --pixels 500 --universe-start 0 --port /dev/ttyACM0

# Matrix configuration
ltp-artnet-sink --pixels 960 --dimensions 60x16 --port /dev/ttyACM0

# Network output (as LTP source)
ltp-artnet-sink --pixels 500 --output ltp --name "ArtNet Bridge"

# Custom color order
ltp-artnet-sink --pixels 170 --color-order GRB --port /dev/ttyACM0
```

### YAML Configuration

```yaml
# artnet-sink.yaml
device:
  name: "Art-Net LED Strip"
  short_name: "ArtNet-Strip"

artnet:
  bind_address: "0.0.0.0"
  universes:
    - universe: 0
      pixel_offset: 0
      pixel_count: 170
    - universe: 1
      pixel_offset: 170
      pixel_count: 170
  color_order: rgb

output:
  type: serial  # or "ltp" or "gpio"
  port: /dev/ttyACM0
  baudrate: 115200

display:
  pixels: 340
  dimensions: [340]
```

---

## LTP Protocol Considerations

### Current Limitations

1. **No Universe Concept**
   - LTP addresses pixels directly (index 0-N)
   - Art-Net uses universe:channel addressing
   - **Impact**: Art-Net sink must handle the mapping internally

2. **Color Order in Protocol**
   - LTP protocol specifies color format (RGB, GRB, RGBW)
   - Art-Net assumes RGB order by convention
   - **Impact**: Sink must handle color reordering

3. **No DMX Channel Concept**
   - LTP sends pixel data (3-4 bytes per pixel)
   - Art-Net sends raw DMX channels (1 byte each)
   - **Impact**: Sink must group channels into pixels

### Potential Protocol Enhancements

These changes would improve Art-Net integration if implemented:

#### 1. Universe/Port-Address Support

Add optional universe field to LTP for Art-Net compatibility:

```
CAPABILITY_RESPONSE extended fields:
  "artnet": {
    "universes": [0, 1, 2],
    "pixels_per_universe": 170
  }
```

#### 2. Channel-Based Addressing

Optional channel mode for DMX compatibility:

```
New addressing mode in PIXEL_FRAME:
  mode: 0 = pixel (current)
  mode: 1 = channel (DMX-style, 1 byte per channel)
```

#### 3. External Protocol Bridge Type

New device type for protocol bridges:

```python
class DeviceType(Enum):
    ...
    ARTNET_BRIDGE = "artnet_bridge"
    SACN_BRIDGE = "sacn_bridge"  # For future sACN support
```

#### 4. Universe Routing in Controller

Add universe-aware routing to controller:

```yaml
route:
  source: artnet-receiver
  sink: led-strip-001
  universe_mapping:
    - artnet_universe: 0
      sink_offset: 0
    - artnet_universe: 1
      sink_offset: 170
```

---

## Issues and Considerations

### 1. Timing Synchronization

**Issue**: Art-Net has no frame sync across universes. Multiple universes may arrive at different times.

**Solutions**:
- Buffer all universes, output when all received
- Use sequence numbers to detect complete frames
- Configurable timeout for partial frames

### 2. Large Pixel Counts

**Issue**: 1000+ pixels requires 6+ universes, increasing complexity.

**Calculation**:
```
Pixels  | Universes (RGB) | Universes (RGBW)
--------|-----------------|------------------
170     | 1               | 2
500     | 3               | 4
1000    | 6               | 8
2000    | 12              | 16
10000   | 59              | 79
```

**Solution**: Efficient multi-universe buffering with configurable strategies.

### 3. Discovery Conflicts

**Issue**: Multiple Art-Net nodes on network may cause confusion.

**Solution**:
- Unique node names
- Configurable universe subscriptions
- Option to disable ArtPoll responses

### 4. Color Order Variations

**Issue**: Different LED controllers expect different color orders (RGB, GRB, BGR).

**Solution**:
- Configurable color order per universe
- Support common orders: RGB, RBG, GRB, GBR, BGR, BRG

### 5. Integration with LTP Controller

**Issue**: Art-Net sink could be standalone or integrated with LTP controller.

**Options**:
- **Standalone**: Art-Net → Serial (like current serial sink)
- **Bridge**: Art-Net → LTP network → Any sink
- **Controller Plugin**: Art-Net as virtual source in controller

### 6. sACN (E1.31) Compatibility

**Issue**: Some systems use sACN instead of Art-Net.

**Consideration**: Design receiver to potentially support both protocols.

```
sACN differences:
- Port 5568
- Different packet format
- Native multicast support
- Priority field for merging
```

---

## Dependencies

```
# requirements.txt additions
stupidArtnet>=1.4.0  # Or implement native receiver
```

Or implement native Art-Net parsing (recommended for full control):

```python
# No external dependency needed
# ~200 lines for basic ArtDmx parsing
```

---

## Testing Strategy

### Unit Tests
- Packet parsing (valid/invalid packets)
- Universe routing calculations
- Color order conversion

### Integration Tests
- Receive from Art-Net controller software (QLC+, MadMapper, Resolume)
- Multi-universe synchronization
- Serial output verification

### Test Tools
```bash
# Send test Art-Net data
python -m ltp_artnet_sink.test_sender --universe 0 --pattern rainbow

# Monitor Art-Net traffic
python -m ltp_artnet_sink.monitor --verbose
```

---

## Implementation Order

1. **Phase 1** (Core): UDP receiver, ArtDmx parsing, single universe
2. **Phase 2** (Multi-universe): Universe routing, aggregation, sequence handling
3. **Phase 3** (Output): Serial integration, LTP bridge mode
4. **Phase 4** (Discovery): ArtPoll/ArtPollReply
5. **Phase 5** (Polish): Web UI integration, statistics, error handling

---

## Summary: Why Bidirectional?

The Art-Net integration must support **both directions** to maximize utility:

### Receiving Art-Net (Art-Net → LTP)
- **Use case**: Leverage existing lighting software (QLC+, Resolume, MadMapper)
- **Benefit**: No need to rewrite pattern generators; use proven tools
- **Implementation**: Art-Net receiver acting as an LTP source

### Sending Art-Net (LTP → Art-Net Devices)
- **Use case**: Control WLED, commercial Art-Net controllers, and DMX fixtures
- **Benefit**: LTP sources can drive any Art-Net device without custom firmware
- **Implementation**: Art-Net sender acting as an LTP sink

### sACN Comparison Summary

| Aspect | LTP's Position |
|--------|----------------|
| **Discovery** | LTP uses mDNS (better semantic info); sACN has none; Art-Net is basic |
| **Addressing** | LTP uses pixel indices (simpler); sACN/Art-Net use universe:channel |
| **Merging** | sACN has native priority; LTP would need controller logic |
| **Multicast** | sACN native; LTP unicast (could add multicast) |
| **Compression** | LTP has RLE; sACN/Art-Net have none |
| **Control** | LTP has bidirectional control channel; neither protocol does |

**Conclusion**: LTP fills a different niche than sACN/Art-Net. Those protocols are optimized for lighting industry compatibility but lack the device abstraction, discovery, and control features that LTP provides. The Art-Net integration bridges LTP to the existing ecosystem without abandoning LTP's advantages.

---

## References

- [Art-Net 4 Specification](https://art-net.org.uk/downloads/art-net.pdf)
- [sACN (E1.31) Standard](https://tsp.esta.org/tsp/documents/docs/ANSI_E1-31-2018.pdf)
- [stupidArtnet Library](https://github.com/cpvalente/stupidArtnet)
- [QLC+ (Open Source Lighting Controller)](https://www.qlcplus.org/)
- [WLED Art-Net Implementation](https://kno.wled.ge/interfaces/e1.31-dmx/)
