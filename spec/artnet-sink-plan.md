# Art-Net Sink Implementation Plan

This document outlines the plan to implement an Art-Net sink that receives Art-Net data over Ethernet and integrates with the LTP ecosystem.

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

## Implementation Architecture

### Component: `ltp_artnet_sink`

```
┌─────────────────────────────────────────────────────────────┐
│                    Art-Net Sink                              │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  ArtNet      │    │   Universe   │    │    Output    │  │
│  │  Receiver    │───▶│   Router     │───▶│   Renderer   │  │
│  │  (UDP 6454)  │    │              │    │              │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│         │                                       │           │
│         ▼                                       ▼           │
│  ┌──────────────┐                      ┌──────────────┐    │
│  │  ArtPoll     │                      │  Serial/     │    │
│  │  Responder   │                      │  Network     │    │
│  └──────────────┘                      └──────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### Module Structure

```
src/ltp_artnet_sink/
├── __init__.py
├── __main__.py
├── cli.py              # Command-line interface
├── sink.py             # Main ArtNetSink class
├── artnet_receiver.py  # UDP receiver, packet parsing
├── universe_router.py  # Map universes to pixel buffers
└── discovery.py        # ArtPoll/ArtPollReply handling
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

## References

- [Art-Net 4 Specification](https://art-net.org.uk/downloads/art-net.pdf)
- [stupidArtnet Library](https://github.com/cpvalente/stupidArtnet)
- [QLC+ (Open Source Lighting Controller)](https://www.qlcplus.org/)
- [WLED Art-Net Implementation](https://kno.wled.ge/interfaces/e1.31-dmx/)
