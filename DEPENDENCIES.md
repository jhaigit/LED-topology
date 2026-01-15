# Dependencies

This document lists all dependencies required to build and run components of the LED Topology Protocol project on Linux.

## Table of Contents

- [Python Host Implementation](#python-host-implementation)
- [Arduino/Teensy Firmware](#arduinoteensy-firmware)
- [LTP Controller](#ltp-controller)
- [Documentation Tools](#documentation-tools)

---

## Python Host Implementation

**Location:** `src/ltp_serial_cli/`

### Required Packages

```bash
# PySerial - Serial port communication
pip install pyserial
```

### Optional (for development)

```bash
# Type checking
pip install mypy

# Testing
pip install pytest

# Linting
pip install ruff
```

### System Requirements

- Python 3.10 or later
- Access to serial ports (user may need to be in `dialout` group)

```bash
# Add user to dialout group for serial port access
sudo usermod -a -G dialout $USER
# Log out and back in for changes to take effect
```

---

## Arduino/Teensy Firmware

**Location:** `arduino/ltp_serial_v2/`

### arduino-cli Installation

```bash
# Option 1: Install to ~/.local/bin (recommended, no sudo required)
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | BINDIR=~/.local/bin sh

# Option 2: Install system-wide
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
sudo mv bin/arduino-cli /usr/local/bin/

# Option 3: Package manager (may be older version)
# Debian/Ubuntu
sudo apt install arduino-cli

# Arch Linux
sudo pacman -S arduino-cli

# macOS
brew install arduino-cli
```

### Add to PATH (if using Option 1)

```bash
# Add to ~/.bashrc or ~/.zshrc
export PATH="$HOME/.local/bin:$PATH"
```

### Arduino Core Setup

```bash
cd arduino/ltp_serial_v2

# Initialize and install Arduino AVR core
make setup

# Or manually:
arduino-cli config init
arduino-cli core update-index
arduino-cli core install arduino:avr
```

### Teensy Core Setup

```bash
cd arduino/ltp_serial_v2

# Install Teensy support
make setup-teensy

# Or manually:
arduino-cli config add board_manager.additional_urls https://www.pjrc.com/teensy/package_teensy_index.json
arduino-cli core update-index
arduino-cli core install teensy:avr
```

### Supported Boards

| Board | FQBN | Make Target |
|-------|------|-------------|
| Arduino Uno | `arduino:avr:uno` | `make build-uno` |
| Arduino Nano | `arduino:avr:nano` | `make build-nano` |
| Arduino Mega | `arduino:avr:mega` | `make build-mega` |
| Teensy 3.2 | `teensy:avr:teensy31` | `make build-teensy32` |
| Teensy 3.5 | `teensy:avr:teensy35` | `make build-teensy35` |
| Teensy 3.6 | `teensy:avr:teensy36` | `make build-teensy36` |
| Teensy 4.0 | `teensy:avr:teensy40` | `make build-teensy40` |
| Teensy 4.1 | `teensy:avr:teensy41` | `make build-teensy41` |

### Teensy Upload Requirements

Teensy boards require the Teensy Loader for uploading. This is included with the Teensy core but may require udev rules:

```bash
# Create udev rules for Teensy
sudo tee /etc/udev/rules.d/49-teensy.rules << 'EOF'
# Teensy 3.x/4.x
ATTRS{idVendor)}=="16c0", ATTRS{idProduct}=="04*", ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1"
ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="04*", MODE="0666"
KERNEL=="ttyACM*", ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="04*", MODE="0666"
EOF

# Reload udev rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

---

## LTP Controller

**Location:** `src/ltp_controller/`

### Required Packages

```bash
# Core dependencies
pip install flask pyyaml numpy

# Full installation with all features
pip install flask pyyaml numpy psutil aiohttp
```

### System Dependencies

```bash
# For system metrics scalar source
pip install psutil

# For async HTTP (if using network features)
pip install aiohttp
```

### Running the Controller

```bash
# From repository root
cd src
python -m ltp_controller

# Or with config file
python -m ltp_controller --config config.yaml
```

---

## Documentation Tools

### PDF Generation

Used to convert the protocol specification to PDF.

```bash
# Create virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install markdown weasyprint

# Convert markdown to PDF
python << 'EOF'
import markdown
from weasyprint import HTML

with open('spec/serial-protocol-v2.md') as f:
    md = f.read()

html = markdown.markdown(md, extensions=['tables', 'fenced_code'])
HTML(string=f"<html><body>{html}</body></html>").write_pdf('spec/serial-protocol-v2.pdf')
EOF

# Deactivate virtual environment
deactivate
```

#### WeasyPrint System Dependencies

WeasyPrint requires some system libraries:

```bash
# Debian/Ubuntu
sudo apt install libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev shared-mime-info

# Fedora
sudo dnf install pango gdk-pixbuf2 libffi-devel

# Arch Linux
sudo pacman -S pango gdk-pixbuf2 libffi
```

---

## Quick Setup Script

Create a script to install all dependencies:

```bash
#!/bin/bash
# setup-dependencies.sh

set -e

echo "=== LED Topology Protocol - Dependency Setup ==="

# Python packages
echo ""
echo "Installing Python packages..."
pip install --user pyserial pyyaml flask numpy psutil

# arduino-cli
echo ""
echo "Installing arduino-cli..."
if ! command -v arduino-cli &> /dev/null; then
    curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | BINDIR=~/.local/bin sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Arduino cores
echo ""
echo "Setting up Arduino cores..."
arduino-cli config init 2>/dev/null || true
arduino-cli core update-index
arduino-cli core install arduino:avr

# Teensy cores
echo ""
echo "Setting up Teensy cores..."
arduino-cli config add board_manager.additional_urls https://www.pjrc.com/teensy/package_teensy_index.json 2>/dev/null || true
arduino-cli core update-index
arduino-cli core install teensy:avr

# Serial port access
echo ""
echo "Adding user to dialout group..."
sudo usermod -a -G dialout $USER 2>/dev/null || true

echo ""
echo "=== Setup complete ==="
echo ""
echo "NOTE: Log out and back in for serial port access to take effect."
echo ""
echo "To verify installation:"
echo "  arduino-cli core list"
echo "  python -c 'import serial; print(serial.VERSION)'"
```

Save as `setup-dependencies.sh` and run:

```bash
chmod +x setup-dependencies.sh
./setup-dependencies.sh
```

---

## Version Requirements

| Component | Minimum Version | Recommended |
|-----------|-----------------|-------------|
| Python | 3.10 | 3.11+ |
| arduino-cli | 0.35.0 | Latest |
| Arduino AVR Core | 1.8.0 | Latest |
| Teensy Core | 1.58.0 | Latest |
| PySerial | 3.5 | Latest |
| Flask | 2.0 | Latest |
| NumPy | 1.20 | Latest |

---

## Troubleshooting

### Serial Port Permission Denied

```bash
# Check group membership
groups $USER

# Add to dialout group
sudo usermod -a -G dialout $USER

# Or use udev rule for specific device
echo 'SUBSYSTEM=="tty", ATTRS{idVendor}=="2341", MODE="0666"' | sudo tee /etc/udev/rules.d/99-arduino.rules
sudo udevadm control --reload-rules
```

### arduino-cli Not Found

```bash
# Check if installed
which arduino-cli

# Add to PATH
export PATH="$HOME/.local/bin:$PATH"

# Make permanent (add to ~/.bashrc)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### Teensy Upload Fails

```bash
# Check for Teensy Loader
ls ~/.arduino15/packages/teensy/tools/teensy-tools/*/

# Ensure udev rules are installed (see Teensy section above)

# Try running Teensy Loader manually
~/.arduino15/packages/teensy/tools/teensy-tools/*/teensy_post_compile
```

### Python Import Errors

```bash
# Ensure packages are installed for correct Python version
python3 -m pip install --user pyserial

# Or use virtual environment
python3 -m venv venv
source venv/bin/activate
pip install pyserial flask numpy pyyaml
```
