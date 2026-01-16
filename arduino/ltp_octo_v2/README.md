# LTP OctoWS2811 - Teensy 3.2 LED Controller

LED strip controller for Teensy 3.2 with OctoWS2811 adapter, supporting 8 parallel WS2812B outputs using the LTP Serial Protocol v2.

## Hardware Requirements

- Teensy 3.2 (or 3.1, 3.5, 3.6, 4.0, 4.1)
- OctoWS2811 adapter board
- Up to 8 WS2812B LED strips
- 5V power supply adequate for your LED count

## Features

- 8 parallel LED strip outputs via OctoWS2811
- Three operational modes:
  - **8 Strips**: Independent strips, each addressable separately
  - **8x Matrix**: All strips as one matrix display
  - **16x Matrix**: Folded matrix with serpentine addressing
- All serpentine/matrix mapping handled internally
- LTP Serial Protocol v2 compatible
- Hardware brightness control

## Configuration Modes

### Mode 1: 8 Independent Strips (default)

```cpp
#define MODE_STRIPS     1
```

- 8 separate strips, each with `PIXELS_PER_STRIP` pixels
- Strips addressed with IDs 0-7
- Useful when strips are physically separate

### Mode 2: 8-Row Matrix

```cpp
#define MODE_MATRIX_8   1
```

- All 8 strips combined into one matrix
- Dimensions: `PIXELS_PER_STRIP` x 8 (width x height)
- Linear addressing: pixel N at row `N/width`, col `N%width`
- Clients see a single display of `PIXELS_PER_STRIP * 8` pixels

### Mode 3: 16-Row Folded Matrix

```cpp
#define MODE_MATRIX_16  1
```

- 8 physical strips presented as 16 logical rows
- Each physical strip folded in half with serpentine addressing
- Dimensions: `PIXELS_PER_STRIP/2` x 16
- Example: 120 pixels/strip → 60 x 16 matrix (960 pixels)

**Serpentine Layout:**
```
Physical strip 0 (120 pixels):
  Row 0: pixels 0-59   (left to right)
  Row 1: pixels 119-60 (right to left, reversed)

Physical strip 1 (120 pixels):
  Row 2: pixels 0-59   (left to right)
  Row 3: pixels 119-60 (right to left, reversed)

... and so on
```

The serpentine addressing is handled entirely in firmware. Clients send linear pixel data and the Teensy maps it correctly.

## Configuration

Edit `config.h` to set:

```cpp
// Pixels per physical strip
#define PIXELS_PER_STRIP    120

// LED color order (usually GRB for WS2812B)
#define LED_COLOR_ORDER     WS2811_GRB

// Serial baud rate
#define SERIAL_BAUD         115200

// Select ONE mode (uncomment)
#define MODE_STRIPS         1
// #define MODE_MATRIX_8    1
// #define MODE_MATRIX_16   1
```

## Pin Mapping

OctoWS2811 uses fixed pins on Teensy 3.x:

| Strip | Pin | Strip | Pin |
|-------|-----|-------|-----|
| 1     | 2   | 5     | 6   |
| 2     | 14  | 6     | 20  |
| 3     | 7   | 7     | 21  |
| 4     | 8   | 8     | 5   |

These are directly connected via the OctoWS2811 adapter board.

## Building

1. Install [Teensyduino](https://www.pjrc.com/teensy/teensyduino.html)
2. Open `ltp_octo_v2.ino` in Arduino IDE
3. Select your Teensy board from Tools → Board
4. Select USB Type: "Serial"
5. Click Upload

## Protocol

Uses LTP Serial Protocol v2. See `spec/serial-protocol-v2.md` for details.

Key commands:
- `CMD_HELLO` (0x04): Device identification
- `CMD_PIXEL_FRAME` (0x33): Send pixel data
- `CMD_SHOW` (0x05): Latch pixels to LEDs
- `CMD_PIXEL_SET_ALL` (0x30): Fill with color

## Usage with LTP

```bash
# Test connection
PYTHONPATH=src python3 -m ltp_serial_cli --port /dev/ttyACM0 --test

# Interactive mode
PYTHONPATH=src python3 -m ltp_serial_cli --port /dev/ttyACM0 -i

# Use as network sink
PYTHONPATH=src python3 -m ltp_serial_sink --port /dev/ttyACM0
```

## Memory Usage

| Mode | Pixels | RAM Usage |
|------|--------|-----------|
| 8 Strips × 120 | 960 | ~12KB |
| 8 Strips × 170 | 1360 | ~16KB |
| 8 Strips × 240 | 1920 | ~23KB |

Teensy 3.2 has 64KB RAM, so up to ~300 pixels per strip is feasible.

## Troubleshooting

**No response from device:**
- Check USB connection
- Verify correct serial port
- Try lower baud rate

**Garbled colors:**
- Check `LED_COLOR_ORDER` matches your strips
- WS2812B is usually GRB, SK6812 may be RGBW

**Flickering:**
- Ensure adequate power supply
- Add capacitor across power rails
- Check ground connections

## License

MIT License - see project root for details.
