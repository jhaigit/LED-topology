# LTP Serial Protocol v2 - Arduino Build Makefile
#
# Uses arduino-cli for command-line builds without IDE.
#
# Installation:
#   curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | BINDIR=~/.local/bin sh
#   # Or: brew install arduino-cli (macOS)
#   # Or: sudo apt install arduino-cli (Debian/Ubuntu)
#
# First-time setup:
#   make setup          # Arduino AVR only
#   make setup-teensy   # Add Teensy support
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

# Teensy board URL
TEENSY_URL = https://www.pjrc.com/teensy/package_teensy_index.json

.PHONY: all build upload clean setup setup-teensy monitor cores list-boards help

# Default target
all: build

# First-time setup - install Arduino AVR core
setup:
	$(CLI) config init || true
	$(CLI) core update-index
	$(CLI) core install arduino:avr
	@echo ""
	@echo "Setup complete. Run 'make setup-teensy' for Teensy support."

# Setup Teensy support
setup-teensy:
	$(CLI) config add board_manager.additional_urls $(TEENSY_URL) || true
	$(CLI) core update-index
	$(CLI) core install teensy:avr
	@echo ""
	@echo "Teensy support installed."

# Compile the sketch
build:
	$(CLI) compile --fqbn $(BOARD) --build-path $(BUILD_DIR) .
	@echo ""
	@echo "Build complete."

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

# ============================================================================
# Arduino AVR targets
# ============================================================================

build-uno:
	$(CLI) compile --fqbn arduino:avr:uno --build-path $(BUILD_DIR)/uno .
	@echo "Arduino Uno build complete"

build-nano:
	$(CLI) compile --fqbn arduino:avr:nano --build-path $(BUILD_DIR)/nano .
	@echo "Arduino Nano build complete"

build-mega:
	$(CLI) compile --fqbn arduino:avr:mega --build-path $(BUILD_DIR)/mega .
	@echo "Arduino Mega build complete"

# ============================================================================
# Teensy targets
# ============================================================================

build-teensy32:
	$(CLI) compile --fqbn teensy:avr:teensy31 --build-path $(BUILD_DIR)/teensy32 .
	@echo "Teensy 3.2 build complete"

build-teensy35:
	$(CLI) compile --fqbn teensy:avr:teensy35 --build-path $(BUILD_DIR)/teensy35 .
	@echo "Teensy 3.5 build complete"

build-teensy36:
	$(CLI) compile --fqbn teensy:avr:teensy36 --build-path $(BUILD_DIR)/teensy36 .
	@echo "Teensy 3.6 build complete"

build-teensy40:
	$(CLI) compile --fqbn teensy:avr:teensy40 --build-path $(BUILD_DIR)/teensy40 .
	@echo "Teensy 4.0 build complete"

build-teensy41:
	$(CLI) compile --fqbn teensy:avr:teensy41 --build-path $(BUILD_DIR)/teensy41 .
	@echo "Teensy 4.1 build complete"

# Build all Teensy variants
build-all-teensy: build-teensy32 build-teensy40 build-teensy41

# ============================================================================
# Build all targets
# ============================================================================

build-all: build-uno build-nano build-mega build-all-teensy
	@echo ""
	@echo "All builds complete."

# Compile with verbose output
build-verbose:
	$(CLI) compile --fqbn $(BOARD) --build-path $(BUILD_DIR) --verbose .

# Show memory usage
size: build
	@echo "Memory usage:"
	@$(CLI) compile --fqbn $(BOARD) --build-path $(BUILD_DIR) . 2>&1 | grep -E "(Sketch|Global|Memory)"

help:
	@echo "LTP Serial Protocol v2 - Arduino Build System"
	@echo ""
	@echo "First-time setup:"
	@echo "  make setup          - Install Arduino AVR core"
	@echo "  make setup-teensy   - Install Teensy support"
	@echo ""
	@echo "Build targets:"
	@echo "  make build          - Compile for current BOARD"
	@echo "  make upload         - Compile and upload"
	@echo "  make clean          - Remove build artifacts"
	@echo ""
	@echo "Arduino variants:"
	@echo "  make build-uno      - Build for Arduino Uno"
	@echo "  make build-nano     - Build for Arduino Nano"
	@echo "  make build-mega     - Build for Arduino Mega"
	@echo ""
	@echo "Teensy variants:"
	@echo "  make build-teensy32 - Build for Teensy 3.2"
	@echo "  make build-teensy35 - Build for Teensy 3.5"
	@echo "  make build-teensy36 - Build for Teensy 3.6"
	@echo "  make build-teensy40 - Build for Teensy 4.0"
	@echo "  make build-teensy41 - Build for Teensy 4.1"
	@echo ""
	@echo "Build all:"
	@echo "  make build-all-teensy - Build all Teensy variants"
	@echo "  make build-all        - Build all targets"
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
	@echo "Examples:"
	@echo "  make upload PORT=/dev/ttyACM0 BOARD=teensy:avr:teensy40"
	@echo "  make build-teensy40"
