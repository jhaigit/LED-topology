# LTP ESP32 Ring Controller - Build Makefile
#
# Uses arduino-cli for command-line builds without IDE.
#
# Installation:
#   curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | BINDIR=~/.local/bin sh
#   # Or: brew install arduino-cli (macOS)
#   # Or: sudo apt install arduino-cli (Debian/Ubuntu)
#
# First-time setup:
#   make setup          # Install ESP32 core and libraries
#
# Build and upload:
#   make upload PORT=/dev/ttyUSB0

# Configuration - modify for your setup
BOARD ?= esp32:esp32:esp32
PORT ?= /dev/ttyUSB0
BAUD ?= 115200

# Build flags for ESP32
UPLOAD_SPEED ?= 921600
PARTITION_SCHEME ?= default

# Sketch info
SKETCH = ltp_esp32_ring.ino
BUILD_DIR = build

# arduino-cli executable
CLI = arduino-cli

# Shared libraries directory (local)
LIBRARIES = ../libraries

# ESP32 board URL
ESP32_URL = https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json

# FastLED library
FASTLED_VERSION = 3.10.3

# ArduinoJson library (for sink protocol)
ARDUINOJSON_VERSION = 7.4.2

.PHONY: all build upload clean setup monitor cores list-boards help \
	build-verbose upload-only dist size install-libs

# Default target
all: build

# First-time setup - install ESP32 core and libraries
setup:
	$(CLI) config init || true
	$(CLI) config add board_manager.additional_urls $(ESP32_URL) || true
	$(CLI) core update-index
	$(CLI) core install esp32:esp32
	@echo ""
	@echo "Installing required libraries..."
	$(CLI) lib install "FastLED@$(FASTLED_VERSION)"
	$(CLI) lib install "ArduinoJson@$(ARDUINOJSON_VERSION)"
	@echo ""
	@echo "Setup complete. You can now build with 'make build'"

# Install/update libraries only
install-libs:
	$(CLI) lib install "FastLED@$(FASTLED_VERSION)"
	$(CLI) lib install "ArduinoJson@$(ARDUINOJSON_VERSION)"
	$(CLI) lib update-index
	@echo "Libraries installed/updated."

# Compile the sketch
build:
	$(CLI) compile \
		--fqbn $(BOARD) \
		--libraries $(LIBRARIES) \
		--build-path $(BUILD_DIR) \
		--build-property "build.partitions=$(PARTITION_SCHEME)" \
		.
	@echo ""
	@echo "Build complete."
	@echo "Binary size:"
	@ls -lh $(BUILD_DIR)/$(SKETCH).bin 2>/dev/null || true

# Upload to board
upload: build
	$(CLI) upload \
		--fqbn $(BOARD) \
		--port $(PORT) \
		--input-dir $(BUILD_DIR)
	@echo ""
	@echo "Upload complete."

# Upload without rebuilding
upload-only:
	$(CLI) upload \
		--fqbn $(BOARD) \
		--port $(PORT) \
		--input-dir $(BUILD_DIR)

# Clean build artifacts
clean:
	rm -rf $(BUILD_DIR)
	@echo "Build directory cleaned."

# Open serial monitor
monitor:
	$(CLI) monitor --port $(PORT) --config baudrate=$(BAUD)

# List available cores
cores:
	$(CLI) core list

# List connected boards
list-boards:
	$(CLI) board list

# Show available ESP32 board variants
boards:
	$(CLI) board listall | grep -i esp32 | head -20

# Compile with verbose output
build-verbose:
	$(CLI) compile \
		--fqbn $(BOARD) \
		--libraries $(LIBRARIES) \
		--build-path $(BUILD_DIR) \
		--verbose \
		.

# Show memory usage
size: build
	@echo "Memory usage:"
	@$(CLI) compile \
		--fqbn $(BOARD) \
		--libraries $(LIBRARIES) \
		--build-path $(BUILD_DIR) \
		. 2>&1 | grep -E "(Sketch|Global|Memory|Flash|RAM)" || true

# ============================================================================
# ESP32 variant targets
# ============================================================================

# ESP32 DevKit (default)
build-esp32:
	$(CLI) compile \
		--fqbn esp32:esp32:esp32 \
		--libraries $(LIBRARIES) \
		--build-path $(BUILD_DIR)/esp32 \
		.
	@echo "ESP32 DevKit build complete"

# ESP32-S2
build-esp32s2:
	$(CLI) compile \
		--fqbn esp32:esp32:esp32s2 \
		--libraries $(LIBRARIES) \
		--build-path $(BUILD_DIR)/esp32s2 \
		.
	@echo "ESP32-S2 build complete"

# ESP32-S3
build-esp32s3:
	$(CLI) compile \
		--fqbn esp32:esp32:esp32s3 \
		--libraries $(LIBRARIES) \
		--build-path $(BUILD_DIR)/esp32s3 \
		.
	@echo "ESP32-S3 build complete"

# ESP32-C3
build-esp32c3:
	$(CLI) compile \
		--fqbn esp32:esp32:esp32c3 \
		--libraries $(LIBRARIES) \
		--build-path $(BUILD_DIR)/esp32c3 \
		.
	@echo "ESP32-C3 build complete"

# Build all ESP32 variants
build-all: build-esp32 build-esp32s3
	@echo ""
	@echo "All builds complete."

# ============================================================================
# Distribution targets
# ============================================================================

# Create distributable binary
dist:
	$(CLI) compile \
		--fqbn $(BOARD) \
		--libraries $(LIBRARIES) \
		--build-path $(BUILD_DIR) \
		.
	@cp $(BUILD_DIR)/$(SKETCH).bin ltp_esp32_ring.bin
	@echo "Distributable binary: ltp_esp32_ring.bin"
	@ls -lh ltp_esp32_ring.bin

# ============================================================================
# OTA update target (for future use)
# ============================================================================

# Upload via OTA (requires device IP)
OTA_HOST ?= ltp-ring.local
OTA_PORT ?= 3232
OTA_PASS ?=

upload-ota: build
	@if [ -z "$(OTA_HOST)" ]; then \
		echo "Error: OTA_HOST not set. Use: make upload-ota OTA_HOST=192.168.1.x"; \
		exit 1; \
	fi
	@echo "OTA upload to $(OTA_HOST)..."
	python3 ~/.arduino15/packages/esp32/hardware/esp32/*/tools/espota.py \
		-i $(OTA_HOST) \
		-p $(OTA_PORT) \
		-f $(BUILD_DIR)/$(SKETCH).bin \
		$(if $(OTA_PASS),-a $(OTA_PASS))

# ============================================================================
# Erase and flash targets
# ============================================================================

# Erase flash completely
erase:
	python3 -m esptool --port $(PORT) erase_flash
	@echo "Flash erased."

# Flash with bootloader (full flash)
flash-full: build
	python3 -m esptool --port $(PORT) \
		--baud $(UPLOAD_SPEED) \
		write_flash \
		0x0 $(BUILD_DIR)/$(SKETCH).ino.bootloader.bin \
		0x8000 $(BUILD_DIR)/$(SKETCH).ino.partitions.bin \
		0x10000 $(BUILD_DIR)/$(SKETCH).bin

# ============================================================================
# Help
# ============================================================================

help:
	@echo "LTP ESP32 Ring Controller - Build System"
	@echo ""
	@echo "First-time setup:"
	@echo "  make setup          - Install ESP32 core and FastLED library"
	@echo "  make install-libs   - Install/update libraries only"
	@echo ""
	@echo "Build targets:"
	@echo "  make build          - Compile for current BOARD"
	@echo "  make upload         - Compile and upload"
	@echo "  make upload-only    - Upload without rebuilding"
	@echo "  make clean          - Remove build artifacts"
	@echo ""
	@echo "ESP32 variants:"
	@echo "  make build-esp32    - Build for ESP32 DevKit"
	@echo "  make build-esp32s2  - Build for ESP32-S2"
	@echo "  make build-esp32s3  - Build for ESP32-S3"
	@echo "  make build-esp32c3  - Build for ESP32-C3"
	@echo "  make build-all      - Build all ESP32 variants"
	@echo ""
	@echo "Distribution:"
	@echo "  make dist           - Create distributable binary"
	@echo ""
	@echo "Flash tools:"
	@echo "  make erase          - Erase flash completely"
	@echo "  make flash-full     - Flash bootloader + app"
	@echo ""
	@echo "OTA update:"
	@echo "  make upload-ota OTA_HOST=192.168.1.x"
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
	@echo "  UPLOAD_SPEED=$(UPLOAD_SPEED)"
	@echo ""
	@echo "Examples:"
	@echo "  make upload PORT=/dev/ttyUSB0"
	@echo "  make build-esp32s3"
	@echo "  make upload-ota OTA_HOST=ltp-ring.local"
