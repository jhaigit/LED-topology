/**
 * LTP OctoWS2811 Configuration
 *
 * Teensy 3.2 with OctoWS2811 adapter for 8 parallel LED strip outputs.
 *
 * CONFIGURATION MODES:
 *
 * 1. STRIPS_MODE (default)
 *    - 8 independent strips, each addressable separately
 *    - Strip IDs: 0-7
 *    - Total pixels reported: PIXELS_PER_STRIP * 8
 *
 * 2. MATRIX_8 mode (MATRIX_MODE=1, MATRIX_FOLD=1)
 *    - 8 strips presented as one matrix
 *    - Dimensions: PIXELS_PER_STRIP x 8 (width x height)
 *    - All 8 strips treated as rows of a single display
 *    - Linear addressing: pixel N maps to strip (N/width), position (N%width)
 *
 * 3. MATRIX_16 mode (MATRIX_MODE=1, MATRIX_FOLD=2)
 *    - 8 physical strips presented as 16-row matrix
 *    - Each physical strip becomes 2 logical rows
 *    - Serpentine folding: odd rows are reversed
 *    - Dimensions: (PIXELS_PER_STRIP/2) x 16
 *    - Example: 120 pixels/strip -> 60 x 16 matrix
 */

#ifndef LTP_OCTO_CONFIG_H
#define LTP_OCTO_CONFIG_H

// ============================================================================
// HARDWARE CONFIGURATION
// ============================================================================

// Pixels per physical strip (OctoWS2811 has 8 outputs)
#define PIXELS_PER_STRIP    120

// LED color order (WS2812B is typically GRB)
#define LED_COLOR_ORDER     WS2811_GRB

// Serial configuration
#define SERIAL_BAUD         115200

// Device identification
#define FIRMWARE_VERSION_MAJOR  1
#define FIRMWARE_VERSION_MINOR  0
#define DEVICE_NAME         "LTP-Octo8"

// Maximum payload size (Teensy 3.2 has 64KB RAM)
#define MAX_PAYLOAD_SIZE    4096

// ============================================================================
// MODE CONFIGURATION - Uncomment ONE mode
// ============================================================================

// Mode 1: 8 Independent Strips
// Each strip addressable separately with strip ID 0-7
// #define MODE_STRIPS         1

// Mode 2: Single Matrix (8 rows)
// All strips combined into one matrix, width=PIXELS_PER_STRIP, height=8
// #define MODE_MATRIX_8       1

// Mode 3: Folded Matrix (16 rows)
// Each strip folded in half with serpentine addressing
// Width = PIXELS_PER_STRIP/2, Height = 16
#define MODE_MATRIX_16      1

// ============================================================================
// DERIVED CONFIGURATION - Do not modify
// ============================================================================

#define NUM_STRIPS          8
#define TOTAL_PIXELS        (PIXELS_PER_STRIP * NUM_STRIPS)

#if defined(MODE_MATRIX_16)
    #define MATRIX_MODE         1
    #define MATRIX_FOLD         2
    #define MATRIX_WIDTH        (PIXELS_PER_STRIP / 2)
    #define MATRIX_HEIGHT       16
    #define REPORT_STRIPS       1
    #define REPORT_PIXELS       (MATRIX_WIDTH * MATRIX_HEIGHT)
    #undef  DEVICE_NAME
    #define DEVICE_NAME         "LTP-Octo16"
#elif defined(MODE_MATRIX_8)
    #define MATRIX_MODE         1
    #define MATRIX_FOLD         1
    #define MATRIX_WIDTH        PIXELS_PER_STRIP
    #define MATRIX_HEIGHT       8
    #define REPORT_STRIPS       1
    #define REPORT_PIXELS       (MATRIX_WIDTH * MATRIX_HEIGHT)
    #undef  DEVICE_NAME
    #define DEVICE_NAME         "LTP-Octo8M"
#else
    // Default: Independent strips mode
    #define MATRIX_MODE         0
    #define MATRIX_FOLD         0
    #define REPORT_STRIPS       8
    #define REPORT_PIXELS       PIXELS_PER_STRIP
#endif

// ============================================================================
// PIN CONFIGURATION (Fixed for OctoWS2811)
// ============================================================================
// OctoWS2811 uses specific pins on Teensy 3.x:
// Pin 2:  Strip 1
// Pin 14: Strip 2
// Pin 7:  Strip 3
// Pin 8:  Strip 4
// Pin 6:  Strip 5
// Pin 20: Strip 6
// Pin 21: Strip 7
// Pin 5:  Strip 8
// These are directly connected to the OctoWS2811 adapter board

#endif // LTP_OCTO_CONFIG_H
