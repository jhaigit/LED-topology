/**
 * LTP 328P PWM Configuration
 *
 * ATmega328P (Arduino Nano) with up to 6 PWM channels driving D4184
 * MOSFET solid-state relays for monochrome LED dimming.
 *
 * Hardware:
 *   - ATmega328P (Arduino Nano, old bootloader)
 *   - 6 PWM outputs driving D4184 MOSFETs:
 *       Channel 0: D3  (Timer2B, 490 Hz)
 *       Channel 1: D5  (Timer0B, 976 Hz)
 *       Channel 2: D6  (Timer0A, 976 Hz)
 *       Channel 3: D9  (Timer1A, 490 Hz)
 *       Channel 4: D10 (Timer1B, 490 Hz)
 *       Channel 5: D11 (Timer2A, 490 Hz)
 */

#ifndef LTP_328P_PWM_CONFIG_H
#define LTP_328P_PWM_CONFIG_H

// ============================================================================
// PWM CHANNEL CONFIGURATION
// ============================================================================

// Maximum number of PWM channels (all 6 hardware PWM pins on ATmega328P)
#define MAX_CHANNELS        6

// Default active channel count
#define DEFAULT_NUM_CHANNELS 6

// PWM output pins (must all support analogWrite)
static const uint8_t pwmPins[MAX_CHANNELS] = { 3, 5, 6, 9, 10, 11 };

// ============================================================================
// SERIAL CONFIGURATION
// ============================================================================

#define SERIAL_BAUD         115200

// ============================================================================
// DEVICE IDENTIFICATION
// ============================================================================

#define FIRMWARE_VERSION_MAJOR  1
#define FIRMWARE_VERSION_MINOR  0
#define DEVICE_NAME         "LTP-328P-PWM"        // factory default instance name
#define FIRMWARE_NAME       "ltp-328p-pwm"
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

#define NUM_CONTROLS        12

#endif // LTP_328P_PWM_CONFIG_H
