# LTP Serial Protocol v2 - Arduino Implementation

Arduino firmware for LED strip control using the LTP Serial Protocol v2.

## Features

- Binary bidirectional protocol with checksums
- Buffered display with explicit SHOW command (tear-free updates)
- Configurable controls (brightness, gamma, auto-show, etc.)
- Statistics reporting (frames, bytes, uptime)
- Pixel readback support

## Default Configuration

- **LED Chip:** LPD8806 (160 pixels)
- **Data Pin:** 11 (MOSI)
- **Clock Pin:** 13 (SCK)
- **Serial:** 115200 baud

## Requirements

### Install arduino-cli

```bash
# Linux
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
sudo mv bin/arduino-cli /usr/local/bin/

# macOS
brew install arduino-cli

# Or download from: https://arduino.github.io/arduino-cli/installation/
```

### First-time Setup

```bash
make setup
```

This installs the Arduino AVR core.

## Building and Uploading

```bash
# Build only
make build

# Build and upload to Arduino Uno on /dev/ttyUSB0
make upload PORT=/dev/ttyUSB0

# Upload to different board
make upload PORT=/dev/ttyACM0 BOARD=arduino:avr:nano

# Show memory usage
make size
```

## Changing LED Chip

To use a different LED chip (WS2812, APA102, etc.):

1. Create a new driver class inheriting from `LedDriver` (see `led_driver_lpd8806.h` as example)
2. Include your driver header in `ltp_serial_v2.ino`
3. Change the driver instantiation:

```cpp
// Before (LPD8806):
LedDriverLPD8806 leds(NUM_PIXELS, DATA_PIN, CLOCK_PIN, USE_HARDWARE_SPI);

// After (your driver):
LedDriverWS2812 leds(NUM_PIXELS, DATA_PIN);
```

### Driver Interface

Your driver must implement these methods:

```cpp
class LedDriverYourChip : public LedDriver {
public:
    void begin() override;           // Initialize hardware
    void show() override;            // Push buffer to LEDs
    uint8_t* getPixelBuffer() override;  // Get raw buffer
    void setPixel(uint16_t index, uint8_t r, uint8_t g, uint8_t b) override;
    uint8_t getLedType() const override; // Return LED_TYPE_* constant
};
```

## Pin Configuration by Board

| Board | Data (MOSI) | Clock (SCK) |
|-------|-------------|-------------|
| Uno/Nano | 11 | 13 |
| Mega | 51 | 52 |
| Leonardo | ICSP-4 | ICSP-3 |

## Protocol Commands Supported

| Command | Description |
|---------|-------------|
| NOP | Keepalive/ping |
| RESET | Restart MCU |
| HELLO | Announce capabilities |
| SHOW | Display buffered pixels |
| GET_INFO | Query device info |
| GET_PIXELS | Read pixel values |
| GET_CONTROL | Read control value |
| PIXEL_SET_ALL | Fill all pixels |
| PIXEL_SET_RANGE | Fill pixel range |
| PIXEL_FRAME | Full frame data |
| SET_CONTROL | Set control value |

## Controls

| ID | Name | Type | Range |
|----|------|------|-------|
| 0 | Brightness | UINT8 | 0-255 |
| 1 | Gamma | UINT8 | 10-30 (×10) |
| 2 | Idle Timeout | UINT16 | seconds |
| 3 | Auto Show | BOOL | 0/1 |
| 4 | Frame Ack | BOOL | 0/1 |
| 5 | Status Interval | UINT16 | seconds |

## Memory Usage (Arduino Uno)

```
Sketch:  ~8 KB (25% of 32 KB)
RAM:     ~700 bytes + 480 bytes (160×3) pixel buffer
         = ~1180 bytes (58% of 2 KB)
```

Maximum pixels on Uno: ~250 (with minimal headroom)

## Testing

Use a serial terminal or the Python host implementation:

```bash
# Open serial monitor
make monitor

# Or use Python
python -c "
import serial
s = serial.Serial('/dev/ttyUSB0', 115200)
# Send NOP packet
s.write(bytes([0xAA, 0x00, 0x00, 0x00, 0x00, 0x00]))
print(s.read(10))
"
```

## Files

```
ltp_serial_v2/
├── ltp_serial_v2.ino      # Main sketch
├── protocol.h             # Protocol constants and parser
├── protocol.cpp           # Protocol implementation
├── led_driver.h           # LED driver base class
├── led_driver_lpd8806.h   # LPD8806 driver
├── Makefile               # Build system
└── README.md              # This file
```

## License

See repository root for license information.
