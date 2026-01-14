# LTP Serial Sink Setup Guide

This guide covers setting up and running `ltp-serial-sink` on a Raspberry Pi or any fresh Unix/Linux system.

## Prerequisites

- Raspberry Pi (any model) or Linux system
- Python 3.10 or newer
- Serial/USB-serial device (Arduino, ESP32, or similar LED controller)
- Network connection (for mDNS discovery)

## 1. System Setup

### Update System Packages

```bash
sudo apt update && sudo apt upgrade -y
```

### Install Python and Dependencies

```bash
# Install Python and pip
sudo apt install -y python3 python3-pip python3-venv git

# Verify Python version (must be 3.10+)
python3 --version
```

If your system has Python < 3.10, you may need to install a newer version:

```bash
# On Debian/Ubuntu systems with older Python
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv
```

## 2. Serial Port Permissions

By default, serial ports require root access. Add your user to the `dialout` group:

```bash
sudo usermod -a -G dialout $USER
```

**Important:** Log out and back in (or reboot) for group changes to take effect.

Verify access:

```bash
# After logging back in
groups  # Should include 'dialout'
ls -la /dev/ttyUSB0  # Check port exists and permissions
```

## 3. Install LTP

### Option A: Install from GitHub (Recommended)

```bash
# Clone the repository
git clone https://github.com/jhaigit/LED-topology.git
cd LED-topology

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install with serial support
pip install -e ".[serial]"
```

### Option B: Install Without Cloning

```bash
# Create project directory
mkdir -p ~/ltp && cd ~/ltp

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install directly from GitHub
pip install "git+https://github.com/jhaigit/LED-topology.git#egg=ltp[serial]"
```

## 4. Verify Installation

```bash
# Activate virtual environment if not already active
source .venv/bin/activate

# Check command is available
ltp-serial-sink --help

# List available serial ports
ltp-serial-sink --list-ports
```

## 5. Connect Your Hardware

1. Connect your LED controller (Arduino, ESP32, etc.) via USB
2. Identify the serial port:

```bash
# List ports - look for your device
ltp-serial-sink --list-ports

# Common port names:
#   /dev/ttyUSB0  - USB-serial adapters (FTDI, CH340, etc.)
#   /dev/ttyACM0  - Arduino Uno, Mega, etc.
#   /dev/serial0  - Raspberry Pi GPIO serial
```

3. Test the connection:

```bash
ltp-serial-sink --port /dev/ttyUSB0 --test
```

This sends a red/green/blue test pattern to verify communication.

## 6. Run the Serial Sink

### Basic Usage

```bash
# Run with default settings (160 pixels, 38400 baud)
ltp-serial-sink --port /dev/ttyUSB0

# Specify pixel count
ltp-serial-sink --port /dev/ttyUSB0 --pixels 60

# Custom name and baud rate
ltp-serial-sink --port /dev/ttyUSB0 --pixels 300 --baud 115200 --name "Living Room LEDs"
```

### Using a Configuration File

Create `serial-sink.yaml`:

```yaml
device:
  name: "Workshop LED Strip"
  description: "160-pixel WS2812B strip"

display:
  pixels: 160
  dimensions: [160]
  max_refresh_hz: 30

serial:
  port: "/dev/ttyUSB0"
  baud: 38400

optimization:
  change_detection: true
  run_length: true
```

Run with config:

```bash
ltp-serial-sink --config serial-sink.yaml
```

## 7. Run as a Service (Auto-start on Boot)

Create a systemd service file:

```bash
sudo nano /etc/systemd/system/ltp-serial-sink.service
```

Add the following content (adjust paths as needed):

```ini
[Unit]
Description=LTP Serial Sink
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/LED-topology
ExecStart=/home/pi/LED-topology/.venv/bin/ltp-serial-sink --port /dev/ttyUSB0 --pixels 160 --name "LED Strip"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable auto-start on boot
sudo systemctl enable ltp-serial-sink

# Start the service now
sudo systemctl start ltp-serial-sink

# Check status
sudo systemctl status ltp-serial-sink

# View logs
journalctl -u ltp-serial-sink -f
```

## 8. Network Discovery

The serial sink advertises itself via mDNS (Bonjour/Avahi). Ensure Avahi is installed:

```bash
sudo apt install -y avahi-daemon
sudo systemctl enable avahi-daemon
sudo systemctl start avahi-daemon
```

The sink will appear as `_ltp-sink._tcp` on the local network and can be discovered by the LTP controller.

## 9. Troubleshooting

### Port Permission Denied

```
Error: [Errno 13] Permission denied: '/dev/ttyUSB0'
```

**Solution:** Add user to dialout group and re-login:
```bash
sudo usermod -a -G dialout $USER
# Then log out and back in
```

### Port Not Found

```
Error: [Errno 2] No such file or directory: '/dev/ttyUSB0'
```

**Solution:**
1. Check device is connected: `lsusb`
2. Check available ports: `ls /dev/tty*`
3. Try different port names (ttyACM0, ttyUSB1, etc.)

### No Data Received by LEDs

1. Verify baud rate matches your Arduino/ESP32 code
2. Check serial protocol format matches (0x vs # prefix)
3. Test with `--test` flag to send test pattern
4. Check Arduino Serial Monitor for received commands

### mDNS Discovery Not Working

**On the sink machine** (Raspberry Pi running ltp-serial-sink):

```bash
# Ensure avahi daemon is running
sudo systemctl status avahi-daemon

# If not running, start it
sudo systemctl start avahi-daemon
sudo systemctl enable avahi-daemon
```

**On any machine** (to verify the sink is discoverable):

```bash
# Install avahi-utils if avahi-browse is missing
sudo apt install -y avahi-utils

# Browse for all mDNS services
avahi-browse -a

# Browse specifically for LTP sinks
avahi-browse -r _ltp-sink._tcp
```

If the sink doesn't appear, check:
1. Both machines are on the same network/subnet
2. No firewall blocking mDNS (UDP port 5353)
3. The sink is running: `sudo systemctl status ltp-serial-sink`

### Python Version Too Old

```bash
# Check version
python3 --version

# Install newer Python if needed (Ubuntu/Debian)
sudo apt install python3.11 python3.11-venv
python3.11 -m venv .venv
```

## 10. Arduino Receiver Setup

Upload this sketch to your Arduino to receive LED commands:

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
    int commaPos = cmd.indexOf(',');
    int equalsPos = cmd.indexOf('=');
    if (equalsPos < 0) return;

    int start, end;
    if (commaPos >= 0 && commaPos < equalsPos) {
        start = cmd.substring(0, commaPos).toInt();
        end = cmd.substring(commaPos + 1, equalsPos).toInt();
    } else {
        start = cmd.substring(0, equalsPos).toInt();
        end = start;
    }

    String colorStr = cmd.substring(equalsPos + 1);
    uint32_t color = 0;
    if (colorStr.startsWith("0x") || colorStr.startsWith("0X")) {
        color = strtoul(colorStr.substring(2).c_str(), NULL, 16);
    } else if (colorStr.startsWith("#")) {
        color = strtoul(colorStr.substring(1).c_str(), NULL, 16);
    }

    uint8_t r = (color >> 16) & 0xFF;
    uint8_t g = (color >> 8) & 0xFF;
    uint8_t b = color & 0xFF;

    start = constrain(start, 0, NUM_LEDS - 1);
    end = constrain(end, 0, NUM_LEDS - 1);

    for (int i = start; i <= end; i++) {
        leds[i] = CRGB(r, g, b);
    }
    FastLED.show();
}
```

## Quick Reference

| Command | Description |
|---------|-------------|
| `ltp-serial-sink --help` | Show all options |
| `ltp-serial-sink --list-ports` | List available serial ports |
| `ltp-serial-sink --port /dev/ttyUSB0 --test` | Test connection |
| `ltp-serial-sink --port /dev/ttyUSB0 --pixels 60` | Run with 60 pixels |
| `ltp-serial-sink --config config.yaml` | Run with config file |
| `sudo systemctl status ltp-serial-sink` | Check service status |
| `journalctl -u ltp-serial-sink -f` | View service logs |
