# LTP Serial Sink Specification

**Version**: 2.0
**Date**: 2026-04-28

## 1. Overview

The `ltp-serial-sink` bridges the LTP network protocol to physical LED
hardware over a serial or USB-serial connection. It communicates with devices
using the LTP Serial Protocol v2 binary packet format (see
`serial-protocol-v2.md`).

```
                         ┌──────────────────────────────────┐
                         │         ltp-serial-sink          │
                         │                                  │
┌─────────────┐   UDP    │  ┌────────────┐  ┌───────────┐  │   Serial    ┌──────────────┐
│ LTP Source  │─────────▶│  │   Data     │  │  Control  │  │   (v2)     │   Arduino /  │
│ or          │          │  │  Receiver  │  │  Server   │  │───────────▶│   ESP32 /    │
│ Controller  │──────────│─▶│   (UDP)    │  │  (TCP)    │  │            │   Teensy     │
└─────────────┘   TCP    │  └──────┬─────┘  └─────┬─────┘  │            └──────────────┘
                         │         │              │         │
                         │         ▼              ▼         │
                         │  ┌─────────────────────────────┐ │
                         │  │       V2Renderer            │ │
                         │  │  - LtpDevice (serial I/O)   │ │
                         │  │  - Reader thread             │ │
                         │  │  - Controls / inputs cache   │ │
                         │  └─────────────────────────────┘ │
                         │         │                        │
                         │  ┌──────┴─────┐  ┌───────────┐  │
                         │  │    mDNS    │  │  Render   │  │
                         │  │ Advertiser │  │  Thread   │  │
                         │  └────────────┘  └───────────┘  │
                         └──────────────────────────────────┘
```

## 2. Serial Protocol

The sink uses LTP Serial Protocol v2, a binary packet protocol. See
`serial-protocol-v2.md` for the full wire format.

### 2.1 Packet Structure

```
[START 0xAA] [FLAGS] [LENGTH_LO] [LENGTH_HI] [CMD] [PAYLOAD...] [CHECKSUM]
```

Checksum is XOR of all bytes from FLAGS through last PAYLOAD byte.

### 2.2 Serial Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| Baud rate | 115200 | Data transfer rate |
| Data bits | 8 | |
| Parity | None | |
| Stop bits | 1 | |
| Flow control | None | |

### 2.3 Connection Handshake

1. Sink opens serial port (toggling DTR resets the device)
2. Reader thread starts consuming bytes
3. Device sends unsolicited **HELLO** (0x04) when ready
4. Sink queries **GET_INFO**(INFO_ALL) to get device identity, pixel count,
   capabilities, control count, input count, and matrix dimensions
5. Sink queries **GET_INFO**(INFO_BUILD) for firmware name, git commit, build date
6. Sink queries **GET_INFO**(INFO_CONTROLS) for control definitions
7. Sink queries **GET_INFO**(INFO_INPUTS) if device reports input capability
8. Connection established

### 2.4 DTR Reset Behavior

Opening the serial port toggles DTR, which resets Arduino-compatible devices
via the capacitor on the RESET pin. Old bootloaders (ATmega328P Nano) take
~2 seconds before the sketch runs, so the HELLO wait timeout must accommodate
this.

## 3. Architecture

### 3.1 Class Hierarchy

```
SerialSink                  # Top-level sink with network + serial
├── V2RendererConfig        # Serial configuration
├── V2Renderer              # Binary protocol renderer
│   └── LtpDevice           # Low-level serial packet I/O
│       └── LtpProtocol     # Packet framing and checksum
├── DataReceiver            # UDP pixel data receiver
├── ControlServer           # TCP control message handler
├── SinkAdvertiser          # mDNS service advertisement
└── ControlRegistry         # Local + device control management
```

### 3.2 V2Renderer

The renderer manages the serial device lifecycle and translates high-level
operations into binary protocol packets.

**Configuration** (`V2RendererConfig`):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `port` | str | (required) | Serial port path |
| `baudrate` | int | 115200 | Baud rate |
| `timeout` | float | 2.0 | Response timeout (seconds) |
| `auto_show` | bool | True | Auto-display after pixel commands |
| `use_frame_ack` | bool | False | Wait for frame acknowledgment |
| `debug` | bool | False | Log raw packet bytes |

**Key methods**:

| Method | Signature | Description |
|--------|-----------|-------------|
| `open()` | `→ None` | Connect, HELLO handshake, query info/controls/inputs |
| `close()` | `→ None` | Close device and release serial port |
| `render(pixels)` | `(np.ndarray) → int` | Send pixel frame, return bytes sent |
| `fill(r, g, b)` | `(int, int, int) → bool` | Fill all pixels with solid color |
| `show()` | `→ bool` | Trigger display update |
| `set_control(id, value)` | `(int, Any) → bool` | Set a device control |
| `get_control(id)` | `(int) → Any` | Read a device control value |
| `get_pixels(start, count)` | `(int, int) → list` | Read pixel data from device |
| `is_connected()` | `→ bool` | Connection status |

### 3.3 LtpDevice

Low-level serial I/O with a background reader thread.

**Reader thread** (`_reader_loop`): Runs as a daemon thread, continuously
reading bytes from the serial port and feeding them to the protocol parser.
Complete packets are either queued for synchronous callers (`_wait_for_response`)
or dispatched as events (input events, unsolicited HELLO).

**Unsolicited HELLO detection**: If the device sends a HELLO after the initial
connection, this indicates a device reset. The reader thread calls
`_reset_callback` so the sink can re-query controls and inputs.

**close()**: Sets `_stop_reader` event, joins the reader thread (1s timeout),
closes the serial port.

### 3.4 SerialSink

The top-level class that integrates network services with the serial renderer.

**Startup sequence** (`start()`):
1. Open serial port via V2Renderer (with reconnection fallback)
2. Auto-detect pixel count and matrix dimensions from device
3. Populate control registry from device controls
4. Start DataReceiver (UDP) for pixel data
5. Start ControlServer (TCP) for control messages
6. Start SinkAdvertiser (mDNS) for discovery
7. Start render thread for frame processing
8. Start serial monitor for reconnection
9. Start stats monitor for logging

**Render thread** (`_render_loop`): Dedicated thread that waits for frame
events, pulls the latest pending frame (with frame dropping for slow
connections), and calls `renderer.render(frame)`.

**Serial monitor** (`_serial_monitor`): Async loop that detects disconnection
and attempts reconnection with exponential backoff (1s to 30s). On reconnect,
re-queries device info, refreshes controls, and broadcasts state change to
connected clients.

## 4. Reconnection and Error Handling

### 4.1 Resource Lifecycle

Every error path that detects a lost connection calls `_close_device()`, which:
1. Calls `device.close()` — stops the reader thread and closes the serial port
2. Sets `_device = None`
3. Sets `_connected = False`

This prevents zombie reader threads from holding the serial port open and
consuming bytes that should go to the next connection attempt.

### 4.2 Reconnection Strategy

```
_serial_monitor loop:
  if not connected:
    try renderer.open()
    on success → reset backoff to 1s, refresh controls, broadcast state
    on failure → sleep(backoff), backoff = min(backoff * 2, 30s)
  else:
    sleep 1s
```

### 4.3 Error Table

| Error | Handling |
|-------|----------|
| Port not found | Log error, retry with backoff |
| Permission denied | Log error with fix hint (`usermod -a -G dialout`) |
| Port busy | Log error, retry with backoff |
| Timeout waiting for ACK | Close device, reconnect |
| Device reset (unsolicited HELLO) | Re-query controls and inputs |
| Reader thread exception | Log warning, break loop (triggers reconnect) |

## 5. Command Line Interface

```bash
python -m ltp_serial_sink -p /dev/ttyUSB0 -n "LED Strip"
```

### 5.1 Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--port` | `-p` | (required) | Serial port path |
| `--name` | `-n` | "Serial LED Strip" | Device display name |
| `--description` | | | Device description |
| `--baudrate` | `-b` | 115200 | Baud rate |
| `--pixels` | | auto-detect | Number of pixels |
| `--dimensions` | `-d` | | Dimensions (e.g. "16x10") |
| `--color-format` | | rgb | Color format (rgb, rgbw) |
| `--timeout` | | 2.0 | Serial timeout (seconds) |
| `--config` | `-c` | | YAML config file path |
| `--log-level` | | info | debug, info, warning, error |
| `--verbose` | `-v` | | Same as --log-level debug |
| `--debug` | | | Show raw serial packets |
| `--no-serial` | | | Run without serial (network test) |
| `--list-ports` | | | List available ports and exit |
| `--test` | | | Test connection and exit |

### 5.2 Test Mode

`--test` connects to the device, displays info (name, firmware, pixels,
capabilities), sends red/green/blue test patterns, clears the strip, and
exits.

### 5.3 Configuration File

```yaml
device:
  name: "Workshop Strip"
  description: "160-pixel WS2812B via Arduino Nano"

display:
  pixels: 160              # or omit for auto-detect
  dimensions: [160]         # [length] or [width, height]
  color_format: "rgb"

serial:
  port: "/dev/ttyUSB0"
  baudrate: 115200
  timeout: 2.0
```

## 6. Network Protocol Integration

### 6.1 mDNS Advertisement

```
Service Type: _ltp-sink._tcp.local.
Service Name: <device-name>._ltp-sink._tcp.local.

TXT Records:
  id=<uuid>
  name=<display-name>
  type=string
  pixels=<count>
  dim=<width>x<height> (if matrix)
  color=rgb
  proto=2.0
```

### 6.2 Capability Response

```json
{
  "type": "capability_response",
  "device": {
    "id": "550e8400-...",
    "name": "Workshop Strip",
    "pixels": 160,
    "dimensions": [160],
    "color_formats": ["rgb"],
    "max_refresh_hz": 30,
    "controls": [...],
    "inputs": [...],
    "backend": {
      "type": "serial",
      "port": "/dev/ttyUSB0",
      "baud": 115200,
      "connected": true
    },
    "firmware": {
      "name": "ltp_serial_v2",
      "git_commit": "abc1234",
      "build_date": "2026-04-01"
    }
  }
}
```

### 6.3 Control Flow

Controls are proxied between the network (TCP control messages from the
controller) and the serial device (binary control packets):

```
Controller ──TCP──▶ ControlServer ──▶ V2Renderer.set_control()
                                       └── LtpDevice.set_control()
                                             └── Serial CMD_SET_CONTROL
                                                   └── Device ACK
```

Device controls (brightness, gamma, local_mode, etc.) are forwarded to
hardware. Local controls (managed by the sink itself) are handled in the
control registry without serial traffic.

### 6.4 Input Events

Device inputs (buttons, encoders) generate events that are broadcast to
connected clients:

```
Device ──Serial CMD_INPUT_EVENT──▶ LtpDevice._reader_loop
                                     └── V2Renderer._handle_input_event
                                           └── SerialSink._handle_input_event
                                                 └── ControlServer.broadcast()
                                                       └── Controller
```

## 7. Data Flow

### 7.1 Pixel Frame Path

```
Source ──UDP DataPacket──▶ DataReceiver._handle_data_packet
                            └── Chunk assembly (if multi-packet)
                            └── _submit_frame(pixels)
                                  └── Render thread: _render_loop
                                        └── V2Renderer.render(np.ndarray)
                                              └── LtpDevice.set_pixels(bytes)
                                              └── LtpDevice.show()
```

The render thread uses frame dropping: if a new frame arrives before the
current one finishes rendering, the older frame is discarded. This prevents
unbounded queue growth on slow serial links.

### 7.2 Direct Fill Path

```
Controller ──TCP fill request──▶ ControlServer
                                   └── _handle_control_set / fill API
                                         └── V2Renderer.fill(r, g, b)
                                               └── LtpDevice.fill(r, g, b)
```

## 8. Dependencies

| Package | Purpose |
|---------|---------|
| `pyserial` | Serial port communication |
| `zeroconf` | mDNS advertisement and discovery |
| `numpy` | Pixel buffer manipulation |
