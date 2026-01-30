# LTP OctoWS2811 Protocol v2 - Teensy Build Makefile
#
# Specifically for Teensy 3.x with OctoWS2811 adapter.
#
# Uses arduino-cli for building and teensy_loader_cli for direct hex loading.
#
# Installation:
#   # arduino-cli:
#   curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | BINDIR=~/.local/bin sh
#
#   # teensy_loader_cli (for direct hex loading):
#   sudo apt install teensy-loader-cli
#   # Or build from: https://github.com/PaulStoffregen/teensy_loader_cli
#
# First-time setup:
#   make setup
#
# Build and upload:
#   make upload              # Build and upload via arduino-cli
#   make load                # Load pre-built hex via teensy_loader_cli
#   make load-hex HEX=custom.hex  # Load custom hex file

# Configuration
BOARD ?= teensy:avr:teensy31
PORT ?= /dev/ttyACM0
BAUD ?= 115200

# Teensy model for teensy_loader_cli (--mcu option)
# Options: TEENSY2, TEENSY2PP, TEENSYLC, TEENSY30, TEENSY31, TEENSY32,
#          TEENSY35, TEENSY36, TEENSY40, TEENSY41, TEENSYMM
TEENSY_MCU ?= TEENSY32

# Sketch info
SKETCH = ltp_octo_v2.ino
BUILD_DIR = build
# Default hex file: use build output, override with HEX_FILE=... for pre-built
HEX_FILE ?= $(BUILD_DIR)/$(SKETCH).hex

# Tools
CLI = arduino-cli
TEENSY_LOADER = teensy_loader_cli

# Version info (embedded at compile time)
GIT_COMMIT := $(shell git rev-parse --short=7 HEAD 2>/dev/null || echo "unknown")
BUILD_DATE := $(shell date +%Y%m%d)
BUILD_FLAGS = -DGIT_COMMIT=\"$(GIT_COMMIT)\" -DBUILD_DATE=\"$(BUILD_DATE)\"

# Shared libraries directory
LIBRARIES = ../libraries

# Teensy board manager URL
TEENSY_URL = https://www.pjrc.com/teensy/package_teensy_index.json

.PHONY: all build upload load load-hex clean setup monitor help

# Default target
all: build

# First-time setup - install Teensy core
setup:
	$(CLI) config init || true
	$(CLI) config add board_manager.additional_urls $(TEENSY_URL) || true
	$(CLI) core update-index
	$(CLI) core install teensy:avr
	$(CLI) lib install OctoWS2811
	@echo ""
	@echo "Setup complete. Teensy support and OctoWS2811 library installed."

# ============================================================================
# Build targets
# ============================================================================

# Compile for Teensy 3.2 (default)
build:
	$(CLI) compile --fqbn $(BOARD) --libraries $(LIBRARIES) --build-path $(BUILD_DIR) \
		--build-property "compiler.cpp.extra_flags=$(BUILD_FLAGS)" .
	@echo ""
	@echo "Build complete. Hex file: $(BUILD_DIR)/$(SKETCH).hex ($(GIT_COMMIT))"

build-teensy32:
	$(CLI) compile --fqbn teensy:avr:teensy31 --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/teensy32 \
		--build-property "compiler.cpp.extra_flags=$(BUILD_FLAGS)" .
	@cp $(BUILD_DIR)/teensy32/$(SKETCH).hex ltp_octo_v2_teensy32.hex
	@echo "Teensy 3.2 build complete: ltp_octo_v2_teensy32.hex ($(GIT_COMMIT))"

build-teensy35:
	$(CLI) compile --fqbn teensy:avr:teensy35 --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/teensy35 \
		--build-property "compiler.cpp.extra_flags=$(BUILD_FLAGS)" .
	@cp $(BUILD_DIR)/teensy35/$(SKETCH).hex ltp_octo_v2_teensy35.hex
	@echo "Teensy 3.5 build complete: ltp_octo_v2_teensy35.hex ($(GIT_COMMIT))"

build-teensy36:
	$(CLI) compile --fqbn teensy:avr:teensy36 --libraries $(LIBRARIES) --build-path $(BUILD_DIR)/teensy36 \
		--build-property "compiler.cpp.extra_flags=$(BUILD_FLAGS)" .
	@cp $(BUILD_DIR)/teensy36/$(SKETCH).hex ltp_octo_v2_teensy36.hex
	@echo "Teensy 3.6 build complete: ltp_octo_v2_teensy36.hex ($(GIT_COMMIT))"

# Build all supported Teensy variants
build-all: build-teensy32 build-teensy35 build-teensy36
	@echo ""
	@echo "All builds complete."

# ============================================================================
# Upload targets (via arduino-cli)
# ============================================================================

# Build and upload via arduino-cli
upload: build
	$(CLI) upload --fqbn $(BOARD) --port $(PORT) --input-dir $(BUILD_DIR)

# Upload without rebuilding
upload-only:
	$(CLI) upload --fqbn $(BOARD) --port $(PORT) --input-dir $(BUILD_DIR)

# ============================================================================
# Load targets (via teensy_loader_cli - direct hex loading)
# ============================================================================

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

# ============================================================================
# Utility targets
# ============================================================================

# Clean build artifacts
clean:
	rm -rf $(BUILD_DIR)

# Open serial monitor
monitor:
	$(CLI) monitor --port $(PORT) --config baudrate=$(BAUD)

# List connected boards
list-boards:
	$(CLI) board list

# Show memory usage
size: build
	@echo "Memory usage:"
	@$(CLI) compile --fqbn $(BOARD) --libraries $(LIBRARIES) --build-path $(BUILD_DIR) . 2>&1 | grep -E "(Sketch|Global|Memory)"

# Compile with verbose output
build-verbose:
	$(CLI) compile --fqbn $(BOARD) --libraries $(LIBRARIES) --build-path $(BUILD_DIR) --verbose \
		--build-property "compiler.cpp.extra_flags=$(BUILD_FLAGS)" .

# ============================================================================
# Help
# ============================================================================

help:
	@echo "LTP OctoWS2811 v2 - Teensy Build System"
	@echo ""
	@echo "First-time setup:"
	@echo "  make setup          - Install Teensy core and OctoWS2811 library"
	@echo ""
	@echo "Build targets:"
	@echo "  make build          - Compile for Teensy 3.2"
	@echo "  make build-teensy32 - Build for Teensy 3.2 (creates .hex)"
	@echo "  make build-teensy35 - Build for Teensy 3.5"
	@echo "  make build-teensy36 - Build for Teensy 3.6"
	@echo "  make build-all      - Build all Teensy variants"
	@echo ""
	@echo "Upload via arduino-cli:"
	@echo "  make upload         - Build and upload"
	@echo "  make upload-only    - Upload without rebuilding"
	@echo ""
	@echo "Load via teensy_loader_cli (direct hex loading):"
	@echo "  make load           - Load pre-built $(HEX_FILE)"
	@echo "  make load-hex HEX=file.hex - Load custom hex file"
	@echo "  make load-reboot    - Load and auto-reboot"
	@echo "  make load-verbose   - Load with verbose output"
	@echo ""
	@echo "Utilities:"
	@echo "  make monitor        - Open serial monitor"
	@echo "  make list-boards    - Show connected boards"
	@echo "  make size           - Show memory usage"
	@echo "  make clean          - Remove build artifacts"
	@echo ""
	@echo "Configuration variables:"
	@echo "  BOARD=$(BOARD)"
	@echo "  PORT=$(PORT)"
	@echo "  TEENSY_MCU=$(TEENSY_MCU)"
	@echo "  HEX_FILE=$(HEX_FILE)"
	@echo ""
	@echo "Examples:"
	@echo "  make load                          # Load pre-built hex"
	@echo "  make upload PORT=/dev/ttyACM0      # Build and upload"
	@echo "  make load TEENSY_MCU=TEENSY35      # Load to Teensy 3.5"
