# LTP Serial Protocol v2 - Arduino Build Makefile
#
# Uses arduino-cli for command-line builds without IDE.
#
# Installation:
#   curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
#   # Or: brew install arduino-cli (macOS)
#   # Or: sudo apt install arduino-cli (Debian/Ubuntu)
#
# First-time setup:
#   make setup
#
# Build and upload:
#   make upload PORT=/dev/ttyUSB0

# Configuration - modify for your setup
BOARD ?= arduino:avr:uno
PORT ?= /dev/ttyUSB0
BAUD ?= 115200

# Sketch info
SKETCH = ltp_serial_v2.ino
BUILD_DIR = build

# arduino-cli executable
CLI = arduino-cli

.PHONY: all build upload clean setup monitor cores list-boards help

# Default target
all: build

# First-time setup - install Arduino AVR core
setup:
	$(CLI) config init || true
	$(CLI) core update-index
	$(CLI) core install arduino:avr
	@echo ""
	@echo "Setup complete. Available boards:"
	@$(CLI) board listall | grep -E "^(arduino:avr:uno|arduino:avr:nano|arduino:avr:mega)"

# Compile the sketch
build:
	$(CLI) compile --fqbn $(BOARD) --build-path $(BUILD_DIR) .
	@echo ""
	@echo "Build complete. Binary size:"
	@ls -lh $(BUILD_DIR)/*.hex 2>/dev/null || ls -lh $(BUILD_DIR)/*.bin 2>/dev/null

# Upload to board
upload: build
	$(CLI) upload --fqbn $(BOARD) --port $(PORT) --input-dir $(BUILD_DIR)

# Upload without rebuilding
upload-only:
	$(CLI) upload --fqbn $(BOARD) --port $(PORT) --input-dir $(BUILD_DIR)

# Clean build artifacts
clean:
	rm -rf $(BUILD_DIR)

# Open serial monitor
monitor:
	$(CLI) monitor --port $(PORT) --config baudrate=$(BAUD)

# List available cores
cores:
	$(CLI) core list

# List connected boards
list-boards:
	$(CLI) board list

# Show available board FQBNs
boards:
	$(CLI) board listall | head -50

# Compile for different boards
build-uno:
	$(CLI) compile --fqbn arduino:avr:uno --build-path $(BUILD_DIR) .

build-nano:
	$(CLI) compile --fqbn arduino:avr:nano --build-path $(BUILD_DIR) .

build-mega:
	$(CLI) compile --fqbn arduino:avr:mega --build-path $(BUILD_DIR) .

# Compile with verbose output
build-verbose:
	$(CLI) compile --fqbn $(BOARD) --build-path $(BUILD_DIR) --verbose .

# Show memory usage
size: build
	@echo "Memory usage:"
	@$(CLI) compile --fqbn $(BOARD) --build-path $(BUILD_DIR) . 2>&1 | grep -E "(Sketch|Global)"

help:
	@echo "LTP Serial Protocol v2 - Arduino Build System"
	@echo ""
	@echo "First-time setup:"
	@echo "  make setup          - Install Arduino AVR core"
	@echo ""
	@echo "Build targets:"
	@echo "  make build          - Compile the sketch"
	@echo "  make upload         - Compile and upload (PORT=/dev/ttyUSB0)"
	@echo "  make upload-only    - Upload without recompiling"
	@echo "  make clean          - Remove build artifacts"
	@echo ""
	@echo "Board variants:"
	@echo "  make build-uno      - Build for Arduino Uno"
	@echo "  make build-nano     - Build for Arduino Nano"
	@echo "  make build-mega     - Build for Arduino Mega"
	@echo ""
	@echo "Utilities:"
	@echo "  make monitor        - Open serial monitor"
	@echo "  make list-boards    - Show connected boards"
	@echo "  make size           - Show memory usage"
	@echo ""
	@echo "Configuration variables:"
	@echo "  BOARD=$(BOARD)"
	@echo "  PORT=$(PORT)"
	@echo "  BAUD=$(BAUD)"
	@echo ""
	@echo "Example:"
	@echo "  make upload PORT=/dev/ttyACM0 BOARD=arduino:avr:nano"
