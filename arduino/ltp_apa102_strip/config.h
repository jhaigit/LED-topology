/**
 * LTP APA102 Strip Configuration
 *
 * Teensy 3.2 with single APA102 strip and input sensors.
 *
 * Hardware:
 *   - Teensy 3.2
 *   - 143 APA102 LEDs (data pin 3, clock pin 2)
 *   - 2 buttons (pins 4 and 5, active low)
 *   - Motion detector (pin 16, active high on detection)
 */

#ifndef LTP_APA102_CONFIG_H
#define LTP_APA102_CONFIG_H

// ============================================================================
// LED DRIVER SELECTION
// ============================================================================

// Uncomment ONE of the following to select LED driver:
#define USE_CUSTOM_APA102_DRIVER    // Use simple custom bit-banged driver
// #define USE_FASTLED              // Use FastLED library

// ============================================================================
// LED CONFIGURATION
// ============================================================================

// Number of LEDs in the strip
#define NUM_LEDS            143

// APA102 pins (hardware SPI-like)
#define LED_DATA_PIN        3
#define LED_CLOCK_PIN       2

// LED color order (APA102 is BGR)
#define LED_COLOR_ORDER     BGR

// LED type
#define LED_CHIPSET         APA102

// ============================================================================
// INPUT CONFIGURATION
// ============================================================================

// Button pins (active low - pressed = LOW)
#define BUTTON_1_PIN        4
#define BUTTON_2_PIN        5
#define BUTTON_ACTIVE_LOW   true

// Motion detector pin (active high - detection = HIGH)
#define MOTION_PIN          16
#define MOTION_ACTIVE_HIGH  true

// Debounce time for buttons (ms)
#define DEBOUNCE_TIME       20

// Number of inputs
#define NUM_INPUTS          3

// ============================================================================
// SERIAL CONFIGURATION
// ============================================================================

#define SERIAL_BAUD         115200

// ============================================================================
// DEVICE IDENTIFICATION
// ============================================================================

#define FIRMWARE_VERSION_MAJOR  1
#define FIRMWARE_VERSION_MINOR  0
#define DEVICE_NAME         "LTP-APA102"

// Maximum payload size (Teensy 3.2 has 64KB RAM)
#define MAX_PAYLOAD_SIZE    2048

// ============================================================================
// IDLE TIMEOUT
// ============================================================================

#define DEFAULT_IDLE_TIMEOUT 600  // 10 minutes default

// ============================================================================
// NUMBER OF CONTROLS
// ============================================================================

#define NUM_CONTROLS        7

#endif // LTP_APA102_CONFIG_H
