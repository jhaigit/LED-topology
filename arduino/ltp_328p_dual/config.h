/**
 * LTP 328P Dual Strip Configuration
 *
 * ATmega328P (Arduino Uno/Nano) with two LED strips and two buttons.
 * Only one strip is active at a time, sharing a single pixel buffer.
 *
 * Hardware:
 *   - ATmega328P (Arduino Uno or Nano)
 *   - WS2812 strip on pin D8
 *   - APA102 strip on pins D3 (data) and D2 (clock)
 *   - 2 buttons on pins A4 and A5 (active low with internal pullup)
 */

#ifndef LTP_328P_DUAL_CONFIG_H
#define LTP_328P_DUAL_CONFIG_H

// ============================================================================
// LED CONFIGURATION
// ============================================================================

// Maximum pixels that can fit in RAM.
// ATmega328P has 2KB RAM. Budget:
//   ~256 bytes protocol buffer (MAX_PAYLOAD_SIZE)
//   ~160 bytes fire effect heat map
//   ~300 bytes control defs, config, inputs, stats, stack
//   Remaining ~1300 bytes for pixel buffer (3 bytes/pixel)
// Use 3 bytes/pixel for the shared buffer. Both WS2812 and APA102
// are driven from this single RGB buffer with conversion at show time.
// With 80 pixels: 240 bytes for pixel buffer, leaving adequate stack.
#define MAX_PIXELS          80

// Default pixel count (can be changed via control)
#define DEFAULT_NUM_PIXELS  60

// Strip selection
#define STRIP_ID_WS2812     0
#define STRIP_ID_APA102     1
#define DEFAULT_ACTIVE_STRIP STRIP_ID_WS2812

// WS2812 pin
#define WS2812_DATA_PIN     8

// APA102 pins
#define APA102_DATA_PIN     3
#define APA102_CLOCK_PIN    2

// ============================================================================
// INPUT CONFIGURATION
// ============================================================================

// Button pins (active low with internal pullup)
#define BUTTON_1_PIN        A4
#define BUTTON_2_PIN        A5
#define BUTTON_ACTIVE_LOW   true

// Debounce time for buttons (ms)
#define DEBOUNCE_TIME       20

// Number of inputs
#define NUM_INPUTS          2

// ============================================================================
// SERIAL CONFIGURATION
// ============================================================================

#define SERIAL_BAUD         115200

// ============================================================================
// DEVICE IDENTIFICATION
// ============================================================================

#define FIRMWARE_VERSION_MAJOR  1
#define FIRMWARE_VERSION_MINOR  0
#define DEVICE_NAME         "LTP-328P-Dual"   // factory default; runtime name is EEPROM-backed
#define FIRMWARE_NAME       "ltp-328p-dual"
#define DEVICE_NAME_MAXLEN  15                 // wire cap: 15 chars + NUL (INFO_ALL/SET_NAME)

// Maximum payload size (ATmega328P has 2KB RAM - keep small)
#define MAX_PAYLOAD_SIZE    256

// ============================================================================
// IDLE TIMEOUT
// ============================================================================

#define DEFAULT_IDLE_TIMEOUT 600  // 10 minutes default

// ============================================================================
// NUMBER OF CONTROLS
// ============================================================================

#define NUM_CONTROLS        14

#endif // LTP_328P_DUAL_CONFIG_H
