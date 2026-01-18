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

# Shared libraries directory
LIBRARIES = ../libraries

# Teensy board URL
TEENSY_URL = https://www.pjrc.com/teensy/package_teensy_index.json

.PHONY: all build upload clean setup setup-teensy monitor cores list-boards help \
	load load-hex load-reboot load-verbose dist-teensy32 dist-teensy40 dist-teensy41 dist-all

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
	$(CLI) compile --fqbn $(BOARD) --libraries $(LIBRARIES) --build-path $(BUILD_DIR) .
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
	$(CLI) compile --fqbn arduino:avr:uno --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/uno .
	@echo "Arduino Uno build complete"

build-nano:
	$(CLI) compile --fqbn arduino:avr:nano --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/nano .
	@echo "Arduino Nano build complete"

build-mega:
	$(CLI) compile --fqbn arduino:avr:mega --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/mega .
	@echo "Arduino Mega build complete"

# ============================================================================
# Teensy targets
# ============================================================================

build-teensy32:
	$(CLI) compile --fqbn teensy:avr:teensy31 --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/teensy32 .
	@echo "Teensy 3.2 build complete"

build-teensy35:
	$(CLI) compile --fqbn teensy:avr:teensy35 --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/teensy35 .
	@echo "Teensy 3.5 build complete"

build-teensy36:
	$(CLI) compile --fqbn teensy:avr:teensy36 --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/teensy36 .
	@echo "Teensy 3.6 build complete"

build-teensy40:
	$(CLI) compile --fqbn teensy:avr:teensy40 --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/teensy40 .
	@echo "Teensy 4.0 build complete"

build-teensy41:
	$(CLI) compile --fqbn teensy:avr:teensy41 --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/teensy41 .
	@echo "Teensy 4.1 build complete"

# Build all Teensy variants
build-all-teensy: build-teensy32 build-teensy40 build-teensy41

# ============================================================================
# Load targets (via teensy_loader_cli - direct hex loading for Teensy)
# ============================================================================

# Teensy loader CLI (install via: sudo apt install teensy-loader-cli)
TEENSY_LOADER = teensy_loader_cli

# Teensy model for teensy_loader_cli (--mcu option)
# Options: TEENSY2, TEENSY2PP, TEENSYLC, TEENSY30, TEENSY31, TEENSY32,
#          TEENSY35, TEENSY36, TEENSY40, TEENSY41, TEENSYMM
TEENSY_MCU ?= TEENSY32

# Pre-built hex file (for distribution)
HEX_FILE ?= ltp_serial_v2_teensy32.hex

# Load pre-built hex file using teensy_loader_cli
# Press the button on Teensy to enter programming mode
load:
	@echo "Loading $(HEX_FILE) to Teensy..."
	@echo "Press the button on Teensy to enter programming mode."
	$(TEENSY_LOADER) --mcu=$(TEENSY_MCU) -w $(HEX_FILE)

# Load custom hex file
load-hex:
	@if [ -z "$(HEX)" ]; then \
		echo "Usage: make load-hex HEX=filename.hex"; \
		exit 1; \
	fi
	@echo "Loading $(HEX) to Teensy..."
	@echo "Press the button on Teensy to enter programming mode."
	$(TEENSY_LOADER) --mcu=$(TEENSY_MCU) -w $(HEX)

# Load and reboot (no wait)
load-reboot:
	@echo "Loading $(HEX_FILE) and rebooting..."
	$(TEENSY_LOADER) --mcu=$(TEENSY_MCU) -w -r $(HEX_FILE)

# Load with verbose output
load-verbose:
	@echo "Loading $(HEX_FILE) to Teensy (verbose)..."
	$(TEENSY_LOADER) --mcu=$(TEENSY_MCU) -w -v $(HEX_FILE)

# Build and create distributable hex files
dist-teensy32:
	$(CLI) compile --fqbn teensy:avr:teensy31 --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/teensy32 .
	@cp $(BUILD_DIR)/teensy32/$(SKETCH).hex ltp_serial_v2_teensy32.hex
	@echo "Teensy 3.2 hex: ltp_serial_v2_teensy32.hex"

dist-teensy40:
	$(CLI) compile --fqbn teensy:avr:teensy40 --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/teensy40 .
	@cp $(BUILD_DIR)/teensy40/$(SKETCH).hex ltp_serial_v2_teensy40.hex
	@echo "Teensy 4.0 hex: ltp_serial_v2_teensy40.hex"

dist-teensy41:
	$(CLI) compile --fqbn teensy:avr:teensy41 --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/teensy41 .
	@cp $(BUILD_DIR)/teensy41/$(SKETCH).hex ltp_serial_v2_teensy41.hex
	@echo "Teensy 4.1 hex: ltp_serial_v2_teensy41.hex"

# Build all distributable hex files
dist-all: dist-teensy32 dist-teensy40 dist-teensy41
	@echo ""
	@echo "All distributable hex files created."

# ============================================================================
# Build all targets
# ============================================================================

build-all: build-uno build-nano build-mega build-all-teensy
	@echo ""
	@echo "All builds complete."

# Compile with verbose output
build-verbose:
	$(CLI) compile --fqbn $(BOARD) --libraries $(LIBRARIES) --build-path $(BUILD_DIR) --verbose .

# Show memory usage
size: build
	@echo "Memory usage:"
	@$(CLI) compile --fqbn $(BOARD) --libraries $(LIBRARIES) --build-path $(BUILD_DIR) . 2>&1 | grep -E "(Sketch|Global|Memory)"

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
	@echo "Load via teensy_loader_cli (direct hex loading):"
	@echo "  make load           - Load pre-built $(HEX_FILE)"
	@echo "  make load-hex HEX=file.hex - Load custom hex file"
	@echo "  make load-reboot    - Load and auto-reboot"
	@echo "  make load-verbose   - Load with verbose output"
	@echo ""
	@echo "Distribution (create hex files):"
	@echo "  make dist-teensy32  - Build Teensy 3.2 hex"
	@echo "  make dist-teensy40  - Build Teensy 4.0 hex"
	@echo "  make dist-teensy41  - Build Teensy 4.1 hex"
	@echo "  make dist-all       - Build all distributable hex files"
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
	@echo "  TEENSY_MCU=$(TEENSY_MCU)"
	@echo "  HEX_FILE=$(HEX_FILE)"
	@echo ""
	@echo "Examples:"
	@echo "  make upload PORT=/dev/ttyACM0 BOARD=teensy:avr:teensy40"
	@echo "  make build-teensy40"
	@echo "  make load TEENSY_MCU=TEENSY40 HEX_FILE=ltp_serial_v2_teensy40.hex"
