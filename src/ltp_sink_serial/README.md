# LTP Serial Protocol v2 - Python Host Implementation

Python library for communicating with microcontrollers running the LTP Serial Protocol v2.

## Installation

```bash
pip install pyserial
```

## Quick Start

```python
from ltp_sink_serial import LtpDevice

# Connect to device
device = LtpDevice('/dev/ttyUSB0')
device.connect()

# Show device info
print(f"Connected to {device.info.device_name}")
print(f"Pixels: {device.pixel_count}")

# Fill all pixels with red
device.fill(255, 0, 0)
device.show()

# Set brightness
device.set_brightness(128)

# Clear and close
device.clear()
device.show()
device.close()
```

## Context Manager

```python
with LtpDevice('/dev/ttyUSB0') as device:
    device.fill(0, 255, 0)
    device.show()
```

## API Reference

### LtpDevice

Main class for device communication.

#### Constructor

```python
LtpDevice(port: str, baudrate: int = 115200, timeout: float = 1.0)
```

#### Connection

```python
device.connect()           # Connect and get device info
device.close()             # Close connection
device.is_connected        # Check connection status
device.ping()              # Ping device, returns True/False
```

#### Pixel Commands

```python
device.fill(r, g, b)                    # Fill all pixels
device.fill_range(start, end, r, g, b)  # Fill range (exclusive end)
device.set_pixel(index, r, g, b)        # Set single pixel
device.set_pixels(data, start=0)        # Set from raw bytes (3 bytes/pixel)
device.clear()                          # Clear all pixels
device.show()                           # Display buffer on LEDs
```

#### Control Commands

```python
device.set_brightness(0-255)     # Set global brightness
device.set_gamma(1.0-3.0)        # Set gamma correction
device.set_auto_show(bool)       # Auto-display after PIXEL_FRAME
device.set_frame_ack(bool)       # Enable frame acknowledgments
device.set_control(id, value)    # Set generic control
device.get_control(id)           # Get control value
```

#### Query Commands

```python
device.info                      # DeviceInfo (after connect)
device.pixel_count               # Total pixels
device.get_status()              # DeviceStatus
device.get_stats()               # DeviceStats
device.get_pixels(start, count)  # Read pixel values
```

#### Input Events

```python
def on_input(input_id, input_type, timestamp, data):
    print(f"Input {input_id}: {data.hex()}")

device.set_input_callback(on_input)
```

### Data Classes

#### DeviceInfo

```python
info.protocol_version   # "2.0"
info.firmware_version   # "1.0"
info.strip_count        # Number of strips
info.total_pixels       # Total pixel count
info.control_count      # Number of controls
info.input_count        # Number of inputs
info.has_brightness     # Brightness control supported
info.has_gamma          # Gamma correction supported
info.is_usb_highspeed   # USB high-speed mode
info.strips             # List[StripInfo]
```

#### StripInfo

```python
strip.strip_id          # Strip ID (0-15)
strip.pixel_count       # Pixels in this strip
strip.led_type_name     # "WS2812", "LPD8806", etc.
strip.color_format_name # "RGB", "GRB", etc.
strip.data_pin          # Data pin number
strip.clock_pin         # Clock pin (0 if N/A)
```

#### DeviceStatus

```python
status.state_name       # "idle", "running", "error"
status.brightness       # Current brightness
status.temperature      # Temperature in °C (or None)
status.voltage          # Voltage in V (or None)
```

#### DeviceStats

```python
stats.frames_received   # Total frames received
stats.frames_displayed  # Total frames displayed
stats.bytes_received    # Total bytes received
stats.checksum_errors   # Checksum error count
stats.uptime_seconds    # Device uptime
```

## Command Line Interface

```bash
# Show device info
python -m ltp_sink_serial /dev/ttyUSB0 info

# Fill with color
python -m ltp_sink_serial /dev/ttyUSB0 fill 255 0 0

# Clear
python -m ltp_sink_serial /dev/ttyUSB0 clear

# Set brightness
python -m ltp_sink_serial /dev/ttyUSB0 brightness 128

# Rainbow pattern
python -m ltp_sink_serial /dev/ttyUSB0 rainbow

# Chase animation
python -m ltp_sink_serial /dev/ttyUSB0 chase -r 0 -g 0 -b 255

# Ping
python -m ltp_sink_serial /dev/ttyUSB0 ping

# Show status
python -m ltp_sink_serial /dev/ttyUSB0 status

# Show statistics
python -m ltp_sink_serial /dev/ttyUSB0 stats
```

## Low-Level Protocol Access

For advanced usage, you can use the protocol layer directly:

```python
from ltp_sink_serial import LtpProtocol, CMD_SHOW

protocol = LtpProtocol()

# Build a packet
packet = protocol.build_packet(CMD_SHOW, b'\x00\x00')

# Parse incoming data
packets = protocol.feed(received_bytes)
for pkt in packets:
    print(f"Received: {pkt.command_name}")
```

## Examples

### Animation Loop

```python
import time

with LtpDevice('/dev/ttyUSB0') as device:
    device.set_brightness(200)

    pos = 0
    while True:
        device.clear()
        device.fill_range(pos, pos + 10, 255, 0, 0)
        device.show()
        pos = (pos + 1) % device.pixel_count
        time.sleep(0.02)
```

### Gradient

```python
with LtpDevice('/dev/ttyUSB0') as device:
    pixels = bytearray(device.pixel_count * 3)

    for i in range(device.pixel_count):
        t = i / device.pixel_count
        pixels[i * 3] = int(255 * t)        # R
        pixels[i * 3 + 1] = int(255 * (1-t)) # G
        pixels[i * 3 + 2] = 0                # B

    device.set_pixels(bytes(pixels))
    device.show()
```

### Input Handling

```python
with LtpDevice('/dev/ttyUSB0') as device:
    def handle_input(input_id, input_type, timestamp, data):
        if input_type == 0x01:  # BUTTON
            state = "pressed" if data[0] else "released"
            print(f"Button {input_id} {state}")
        elif input_type == 0x02:  # ENCODER
            delta = struct.unpack('b', data)[0]
            print(f"Encoder {input_id} rotated {delta}")

    device.set_input_callback(handle_input)

    # Keep running to receive events
    while True:
        time.sleep(0.1)
```
