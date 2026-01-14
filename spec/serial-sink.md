# LTP Serial Sink Specification

**Version**: 0.1.0-draft
**Date**: 2026-01-13

## 1. Overview

The `ltp-serial-sink` is a data sink that implements the LTP protocol and forwards LED data to a physical device via a serial or USB-serial connection. It acts as a bridge between the LTP network protocol and serial-controlled LED hardware.

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ LTP Source  │────▶│  ltp-serial-sink │────▶│ Serial Device   │
│ or          │ UDP │                  │ TTY │ (Arduino, ESP,  │
│ Controller  │     │ - LTP Protocol   │     │  LED Controller)│
└─────────────┘     │ - Serial Output  │     └─────────────────┘
                    └──────────────────┘
```

## 2. Serial Protocol

### 2.1 Command Format

LED data is sent to the serial device using text commands terminated with carriage return (`\r`) or newline (`\n`):

```
<start>[,<end>]=<RGB><CR|LF>
```

Where:
- `<start>` - Starting pixel index (0-based, inclusive)
- `<end>` - Ending pixel index (inclusive, optional)
- `<RGB>` - Color value in hexadecimal format

The `,<end>` portion is optional. When omitted, only the single pixel at `<start>` is set.

### 2.2 RGB Color Formats

Two hexadecimal formats are supported:

| Format | Example | Description |
|--------|---------|-------------|
| `0xRRGGBB` | `0xFF0000` | C-style hex with `0x` prefix |
| `#RRGGBB` | `#FF0000` | CSS-style hex with `#` prefix |

### 2.3 Command Examples

```
# Set pixels 0-9 to red (10 pixels total)
0,9=0xFF0000

# Set pixels 10-19 to green
10,19=#00FF00

# Set single pixel 50 to blue
50=0x0000FF

# Set entire 160-pixel strip to white
0,159=#FFFFFF

# Set pixels 100-159 to yellow (60 pixels)
100,159=0xFFFF00
```

### 2.4 Serial Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| Baud rate | 38400 | Data transfer rate |
| Data bits | 8 | Bits per character |
| Parity | None | No parity bit |
| Stop bits | 1 | One stop bit |
| Flow control | None | No hardware/software flow control |

### 2.5 Transmission Strategy

To minimize serial traffic and latency, the sink uses an intelligent transmission strategy:

1. **Change Detection**: Only send commands for pixels that changed since last frame
2. **Run-Length Optimization**: Combine consecutive pixels of the same color into single commands
3. **Batch Commands**: Send multiple commands per frame, each on its own line
4. **Rate Limiting**: Respect serial bandwidth limitations

Example optimized transmission for a frame:
```
0,29=0xFF0000
30,59=#00FF00
60,89=0x0000FF
90,159=#000000
```

## 3. Command Line Interface

```bash
# Run with config file
ltp-serial-sink --config serial-sink.yaml

# Run with inline configuration
ltp-serial-sink --name "LED Strip" --port /dev/ttyUSB0 --pixels 160

# Specify baud rate
ltp-serial-sink --port /dev/ttyACM0 --baud 115200 --pixels 300

# List available serial ports
ltp-serial-sink --list-ports

# Test serial connection
ltp-serial-sink --port /dev/ttyUSB0 --test
```

### 3.1 Command Line Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config` | `-c` | - | Path to YAML configuration file |
| `--name` | `-n` | "Serial LED Strip" | Device display name |
| `--port` | `-p` | - | Serial port path (required) |
| `--baud` | `-b` | 38400 | Baud rate |
| `--pixels` | - | 160 | Number of pixels in strip |
| `--dimensions` | `-d` | - | Dimensions (e.g., "160" or "16x10") |
| `--color-format` | - | rgb | Color format (rgb, rgbw) |
| `--hex-format` | - | 0x | Output format: "0x" or "#" |
| `--log-level` | - | info | Logging level |
| `--list-ports` | - | - | List available serial ports |
| `--test` | - | - | Test serial connection and exit |

## 4. Configuration Schema

```yaml
device:
  id: "auto"                    # "auto" or explicit UUID
  name: "Workshop LED Strip"
  description: "160-pixel WS2812B strip via Arduino"

display:
  pixels: 160                   # Total pixel count
  dimensions: [160]             # [length] or [width, height]
  color_format: "rgb"           # rgb or rgbw
  max_refresh_hz: 30            # Maximum refresh rate

serial:
  port: "/dev/ttyUSB0"          # Serial port path
  baud: 38400                   # Baud rate
  timeout: 1.0                  # Read timeout in seconds
  write_timeout: 1.0            # Write timeout in seconds

  # Advanced serial settings (usually not needed)
  data_bits: 8
  parity: "none"                # none, even, odd
  stop_bits: 1
  flow_control: "none"          # none, rts_cts, xon_xoff

protocol:
  hex_format: "0x"              # "0x" or "#"
  line_ending: "\n"             # "\n", "\r", or "\r\n"
  command_delay: 0.001          # Delay between commands (seconds)
  frame_delay: 0.0              # Delay between frames (seconds)

optimization:
  change_detection: true        # Only send changed pixels
  run_length: true              # Combine consecutive same-color pixels
  min_run_length: 1             # Minimum pixels to combine
  max_commands_per_frame: 100   # Limit commands per frame

controls:
  - id: "brightness"
    name: "Brightness"
    description: "Master brightness (applied before serial output)"
    type: "number"
    value: 1.0
    min: 0.0
    max: 1.0
    step: 0.05
    group: "output"

  - id: "gamma"
    name: "Gamma Correction"
    type: "number"
    value: 2.2
    min: 1.0
    max: 3.0
    step: 0.1
    group: "output"

logging:
  level: "info"
  file: null
```

## 5. Architecture

### 5.1 Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     ltp-serial-sink                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │   mDNS       │    │   Control    │    │    Data      │  │
│  │  Advertiser  │    │   Server     │    │   Receiver   │  │
│  │              │    │   (TCP)      │    │    (UDP)     │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│         │                   │                   │           │
│         │                   ▼                   ▼           │
│         │            ┌──────────────────────────────┐       │
│         │            │       Frame Buffer          │       │
│         │            │    (numpy array NxRGB)      │       │
│         │            └──────────────────────────────┘       │
│         │                        │                          │
│         │                        ▼                          │
│         │            ┌──────────────────────────────┐       │
│         │            │     Serial Renderer         │       │
│         │            │  - Change detection         │       │
│         │            │  - Run-length encoding      │       │
│         │            │  - Command generation       │       │
│         │            └──────────────────────────────┘       │
│         │                        │                          │
│         │                        ▼                          │
│         │            ┌──────────────────────────────┐       │
│         │            │     Serial Port             │       │
│         │            │   (pyserial)                │       │
│         └───────────▶└──────────────────────────────┘       │
│                                  │                          │
└──────────────────────────────────│──────────────────────────┘
                                   │
                                   ▼
                        ┌──────────────────┐
                        │  Physical Device │
                        │  /dev/ttyUSB0    │
                        └──────────────────┘
```

### 5.2 Class Structure

```python
# serial_sink.py

@dataclass
class SerialSinkConfig:
    """Configuration for serial sink."""
    device_id: UUID
    name: str
    description: str
    pixels: int
    dimensions: list[int]
    color_format: ColorFormat
    max_refresh_hz: int

    # Serial settings
    port: str
    baud: int
    timeout: float
    write_timeout: float

    # Protocol settings
    hex_format: str  # "0x" or "#"
    line_ending: str
    command_delay: float

    # Optimization
    change_detection: bool
    run_length: bool


class SerialRenderer:
    """Renders pixel data to serial commands."""

    def __init__(self, config: SerialSinkConfig):
        self.config = config
        self._serial: serial.Serial | None = None
        self._last_frame: np.ndarray | None = None

    def open(self) -> None:
        """Open serial port connection."""

    def close(self) -> None:
        """Close serial port connection."""

    def render(self, pixels: np.ndarray) -> None:
        """Render pixel data to serial device."""

    def _detect_changes(self, pixels: np.ndarray) -> list[tuple[int, int, tuple]]:
        """Detect changed pixel regions."""

    def _generate_commands(self, changes: list) -> list[str]:
        """Generate serial commands for changes."""

    def _format_color(self, r: int, g: int, b: int) -> str:
        """Format RGB color as hex string."""

    def _send_command(self, command: str) -> None:
        """Send command to serial port."""


class SerialSink:
    """LTP sink with serial output backend."""

    def __init__(self, config: SerialSinkConfig):
        self.config = config
        self._advertiser: SinkAdvertiser | None = None
        self._control_server: ControlServer | None = None
        self._data_receiver: DataReceiver | None = None
        self._renderer: SerialRenderer | None = None
        self._buffer: np.ndarray
        self._running: bool = False

    async def start(self) -> None:
        """Start the serial sink."""

    async def stop(self) -> None:
        """Stop the serial sink."""

    def _handle_frame(self, packet: DataPacket) -> None:
        """Handle incoming frame data."""
```

## 6. Serial Renderer Implementation

### 6.1 Change Detection Algorithm

```python
def _detect_changes(self, pixels: np.ndarray) -> list[tuple[int, int, tuple]]:
    """Detect regions that changed from last frame.

    Returns:
        List of (start, end, color) tuples for changed regions
    """
    if self._last_frame is None:
        # First frame - everything changed
        self._last_frame = pixels.copy()
        return self._find_runs(pixels)

    # Find pixels that changed
    changed = np.any(pixels != self._last_frame, axis=1)
    self._last_frame = pixels.copy()

    if not np.any(changed):
        return []  # No changes

    # Find runs of changed pixels
    changes = []
    i = 0
    while i < len(changed):
        if changed[i]:
            # Start of changed region
            start = i
            color = tuple(pixels[i])

            # Extend while same color and changed
            while i < len(changed) and changed[i] and tuple(pixels[i]) == color:
                i += 1

            changes.append((start, i, color))
        else:
            i += 1

    return changes
```

### 6.2 Run-Length Optimization

```python
def _find_runs(self, pixels: np.ndarray) -> list[tuple[int, int, tuple]]:
    """Find runs of consecutive same-color pixels.

    Returns:
        List of (start, end, color) tuples
    """
    if len(pixels) == 0:
        return []

    runs = []
    start = 0
    current_color = tuple(pixels[0])

    for i in range(1, len(pixels)):
        color = tuple(pixels[i])
        if color != current_color:
            runs.append((start, i, current_color))
            start = i
            current_color = color

    # Final run
    runs.append((start, len(pixels), current_color))

    return runs
```

### 6.3 Command Generation

```python
def _generate_commands(self, changes: list[tuple[int, int, tuple]]) -> list[str]:
    """Generate serial commands for pixel changes."""
    commands = []

    for start, end, color in changes:
        r, g, b = color

        if self.config.hex_format == "0x":
            color_str = f"0x{r:02X}{g:02X}{b:02X}"
        else:
            color_str = f"#{r:02X}{g:02X}{b:02X}"

        command = f"{start},{end}={color_str}"
        commands.append(command)

    return commands
```

## 7. LTP Protocol Integration

### 7.1 mDNS Advertisement

The serial sink advertises itself as a standard LTP sink:

```
Service Type: _ltp-sink._tcp.local.
Service Name: <device-name>._ltp-sink._tcp.local.

TXT Records:
  id=<uuid>
  name=<display-name>
  desc=<description>
  type=string
  pixels=160
  dim=160
  color=rgb
  rate=30
  proto=0.1
```

### 7.2 Capability Response

```json
{
  "type": "capability_response",
  "seq": 1,
  "device": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "Workshop LED Strip",
    "description": "160-pixel WS2812B strip via Arduino",
    "type": "string",
    "pixels": 160,
    "dimensions": [160],
    "topology": {
      "type": "linear",
      "dimensions": [160]
    },
    "color_formats": ["rgb"],
    "max_refresh_hz": 30,
    "protocol_version": "0.1",
    "controls": [
      {
        "id": "brightness",
        "name": "Brightness",
        "type": "number",
        "value": 1.0,
        "min": 0.0,
        "max": 1.0
      }
    ],
    "backend": {
      "type": "serial",
      "port": "/dev/ttyUSB0",
      "baud": 38400
    }
  }
}
```

## 8. Error Handling

### 8.1 Serial Port Errors

| Error | Handling |
|-------|----------|
| Port not found | Log error, retry with backoff |
| Permission denied | Log error with fix suggestion (`sudo usermod -a -G dialout $USER`) |
| Port busy | Log error, retry after delay |
| Write timeout | Log warning, continue |
| Device disconnected | Attempt reconnection |

### 8.2 Reconnection Strategy

```python
async def _serial_monitor(self) -> None:
    """Monitor serial connection and reconnect if needed."""
    reconnect_delay = 1.0
    max_delay = 30.0

    while self._running:
        if not self._renderer.is_connected():
            try:
                self._renderer.open()
                logger.info(f"Serial port {self.config.port} connected")
                reconnect_delay = 1.0  # Reset delay on success
            except SerialException as e:
                logger.warning(f"Serial connection failed: {e}")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_delay)
        else:
            await asyncio.sleep(1.0)
```

## 9. Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `pyserial` | >=3.5 | Serial port communication |
| `zeroconf` | >=0.80.0 | mDNS advertisement |
| `numpy` | >=1.24.0 | Pixel buffer manipulation |
| `pydantic` | >=2.0.0 | Configuration validation |
| `pyyaml` | >=6.0 | Configuration file parsing |

## 10. Example Arduino Receiver

Simple Arduino sketch to receive and display serial LED commands:

```cpp
#include <FastLED.h>

#define LED_PIN     6
#define NUM_LEDS    160
#define BAUD_RATE   38400

CRGB leds[NUM_LEDS];
String inputBuffer = "";

void setup() {
    Serial.begin(BAUD_RATE);
    FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
    FastLED.setBrightness(255);
    FastLED.clear();
    FastLED.show();
}

void loop() {
    while (Serial.available()) {
        char c = Serial.read();

        if (c == '\n' || c == '\r') {
            if (inputBuffer.length() > 0) {
                processCommand(inputBuffer);
                inputBuffer = "";
            }
        } else {
            inputBuffer += c;
        }
    }
}

void processCommand(String cmd) {
    // Parse: start[,end]=0xRRGGBB or start[,end]=#RRGGBB
    // end is optional and inclusive (last LED to set)
    int commaPos = cmd.indexOf(',');
    int equalsPos = cmd.indexOf('=');

    if (equalsPos < 0) return;

    int start, end;
    String colorStr;

    if (commaPos >= 0 && commaPos < equalsPos) {
        // Format: start,end=color
        start = cmd.substring(0, commaPos).toInt();
        end = cmd.substring(commaPos + 1, equalsPos).toInt();
    } else {
        // Format: start=color (single pixel)
        start = cmd.substring(0, equalsPos).toInt();
        end = start;
    }
    colorStr = cmd.substring(equalsPos + 1);

    // Parse color (skip 0x or #)
    uint32_t color = 0;
    if (colorStr.startsWith("0x") || colorStr.startsWith("0X")) {
        color = strtoul(colorStr.substring(2).c_str(), NULL, 16);
    } else if (colorStr.startsWith("#")) {
        color = strtoul(colorStr.substring(1).c_str(), NULL, 16);
    }

    uint8_t r = (color >> 16) & 0xFF;
    uint8_t g = (color >> 8) & 0xFF;
    uint8_t b = color & 0xFF;

    // Apply to LED range (end is inclusive)
    start = constrain(start, 0, NUM_LEDS - 1);
    end = constrain(end, 0, NUM_LEDS - 1);

    for (int i = start; i <= end; i++) {
        leds[i] = CRGB(r, g, b);
    }

    FastLED.show();
}
```

## 11. Usage Examples

### 11.1 Basic Usage

```bash
# Start serial sink on USB0
ltp-serial-sink --port /dev/ttyUSB0 --pixels 160

# With custom name and baud rate
ltp-serial-sink --port /dev/ttyACM0 --baud 115200 --pixels 300 \
    --name "Living Room Strip"
```

### 11.2 With Configuration File

```yaml
# serial-sink.yaml
device:
  name: "Workshop LED Strip"
  description: "Main workbench lighting"

display:
  pixels: 160
  max_refresh_hz: 30

serial:
  port: "/dev/ttyUSB0"
  baud: 38400

optimization:
  change_detection: true
  run_length: true
```

```bash
ltp-serial-sink --config serial-sink.yaml
```

### 11.3 Testing Connection

```bash
# List available ports
ltp-serial-sink --list-ports

# Output:
# Available serial ports:
#   /dev/ttyUSB0 - USB Serial Device
#   /dev/ttyACM0 - Arduino Uno

# Test connection
ltp-serial-sink --port /dev/ttyUSB0 --test

# Output:
# Testing serial connection to /dev/ttyUSB0 at 38400 baud...
# Sending test pattern...
# Success: Serial connection working
```

## 12. Integration with LTP Controller

The serial sink appears as a standard sink in the LTP controller:

1. **Discovery**: Controller discovers serial sink via mDNS
2. **Routing**: Routes can be created from any source to serial sink
3. **Control**: Brightness and other controls accessible via web UI
4. **Fill**: Direct sink fill commands work (solid, gradient, sections)

The serial sink handles all protocol translation transparently.
