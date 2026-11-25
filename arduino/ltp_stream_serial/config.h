/**
 * LTP Stream (serial) - board configuration
 *
 * Pattern-only LED controller: it speaks LTP serial protocol v2 but takes no
 * pixel data. Every pixel is calculated while the frame is being sent, so no
 * RAM is spent per LED and the pixel count is limited by frame rate alone.
 *
 * Boards:
 *   - ATmega328P (Arduino Uno / Nano): WS2812 on D8, APA102 on D3 (data) and
 *     D2 (clock) - the wiring of ltp_328p_dual. Host link: UART, 115200 baud.
 *   - Teensy 3.2: WS2812 on pin 2, APA102 or LPD8806 on hardware SPI
 *     (11 = data, 13 = clock). Host link: USB serial.
 *   - Teensy 4.0: WS2812 on pin 14 (a UART transmit pin), APA102 or LPD8806
 *     on hardware SPI (11 = data, 13 = clock). Host link: USB serial. Fast
 *     enough for the extended patterns of LtpStreamFx.
 */

#ifndef LTP_STREAM_SERIAL_CONFIG_H
#define LTP_STREAM_SERIAL_CONFIG_H

// ============================================================================
// COMMON
// ============================================================================

#define SERIAL_BAUD             115200

#define FIRMWARE_VERSION_MAJOR  1
#define FIRMWARE_VERSION_MINOR  0
#define FIRMWARE_NAME           "ltp-stream"

// No command this firmware acts on carries more than a device name (15
// bytes). Longer packets - pixel frames - are read and discarded unstored.
#define MAX_PAYLOAD_SIZE        32

// Where the settings live in EEPROM. Not address 0, so the settings of
// ltp_328p_dual or ltp_serial_v2 survive on a board that is reflashed
// between the two firmwares.
#define EEPROM_CONFIG_ADDR      128

// Quiet time on the data line that makes the LEDs show a frame. Newer WS2812B
// need 280 us; older ones and SK6812 need less.
#define LED_LATCH_US            300

// A frame that never changes (Off, Solid) is sent again at this interval, so
// a strip that lost power for a moment recovers.
#define STATIC_REFRESH_MS       1000

// The largest pixel read-back one response can carry: the host accepts
// payloads up to 4096 bytes, 5 of them header.
#define MAX_READBACK_PIXELS     1363

// ============================================================================
// ATMEGA328P (Arduino Uno, Nano)
// ============================================================================

#if defined(__AVR_ATmega328P__)

#define BOARD_AVR               1
#define DEVICE_NAME             "LTP-Stream-328P"

// No frame buffer, so this is a frame-rate limit, not a RAM limit: a WS2812
// frame takes 102 us per pixel, 1000 pixels refresh about 10 times a second.
#define MAX_PIXELS              1000

// WS2812 data on D8 = PB0. The bit clock interrupt drives this pin with
// single sbi/cbi instructions, which need the port and bit at compile time.
#define WS2812_PORT             PORTB
#define WS2812_DDR              DDRB
#define WS2812_BIT              0
#define WS2812_PIN_NUMBER       8

// WS2812 bits are sent by a timer interrupt, one bit per period, and the
// pattern is calculated in the time between. The period, in CPU cycles at
// 16 MHz, sets the longest low time on the data line (period - 5 cycles, plus
// a few of jitter) and the calculation time per pixel.
//   68 cycles: 4.0 us low, 102 us per pixel.
// WS2812 made before about 2017 latch after 6-9 us of low, so stay under
// 80 cycles. Measured on a simulated 328P (make sim-test), the heaviest
// patterns (Fire, Ripple on a matrix) keep up down to 60 cycles and the
// lightest down to 52; 68 leaves room for the few cycles by which the real
// chip enters an interrupt later than the simulator. If a pattern still
// cannot keep up, the frame is cut short and the period grows by
// WS2812_BIT_PERIOD_STEP, up to WS2812_BIT_PERIOD_MAX, until the pattern is
// changed.
#define WS2812_BIT_PERIOD       68
#define WS2812_BIT_PERIOD_MAX   76
#define WS2812_BIT_PERIOD_STEP  4

// APA102 on D3 = PD3 (data) and D2 = PD2 (clock), bit-banged with direct
// port writes: about 27 us per pixel, 15 times faster than digitalWrite.
#define APA102_PORT             PORTD
#define APA102_DDR              DDRD
#define APA102_DATA_BIT         3
#define APA102_CLOCK_BIT        2
#define APA102_DATA_PIN_NUMBER  3
#define APA102_CLOCK_PIN_NUMBER 2

#define HAS_LPD8806             0
#define HAS_LOAD_CONTROL        0

// Heartbeat on the built-in LED (D13), free because nothing here uses SPI.
#define HEARTBEAT_PIN           13
#define HEARTBEAT_INTERVAL_MS   500

// ============================================================================
// TEENSY 3.x (Teensy 3.2)
// ============================================================================

#elif defined(KINETISK)

#define BOARD_TEENSY            1
#define DEVICE_NAME             "LTP-Stream-T32"

// Frame-rate limit only: a WS2812 pixel is 30 us on the wire, so 4000 pixels
// take a little over 120 ms.
#define MAX_PIXELS              4000

// WS2812 data: pin 2, strip 1 of an OctoWS2811 adaptor board.
#define WS2812_PIN_NUMBER       2

// Clocked LEDs on hardware SPI: 11 = data, 13 = clock.
#define APA102_DATA_PIN_NUMBER  11
#define APA102_CLOCK_PIN_NUMBER 13
// Clock rates ltp_serial_v2 runs these strips at; long or unshielded wiring
// sets the limit before the LED chips do.
#define APA102_SPI_HZ           4000000
#define LPD8806_SPI_HZ          2000000

#define HAS_LPD8806             1
#define HAS_LOAD_CONTROL        0

// ============================================================================
// TEENSY 4.x (Teensy 4.0)
// ============================================================================

#elif defined(__IMXRT1062__)

#define BOARD_TEENSY            1
#define DEVICE_NAME             "LTP-Stream-T40"

// Frame-rate limit only: a WS2812 pixel is 30 us on the wire, so 4000 pixels
// take 120 ms.
#define MAX_PIXELS              4000

// WS2812 data comes from a UART, so it has to be a UART transmit pin: 1, 8,
// 14, 17 or 20 on the Teensy 4.0 (ws2812_teensy4.h has the table). Pin 14 is
// strip 2 of an OctoWS2811 adaptor board, whose buffer also lifts the 3.3 V
// signal to the 5 V a WS2812 expects; pins 8 and 20 are its strips 4 and 6.
// The Teensy 3.2 build uses pin 2, which is no UART pin on this board.
#define WS2812_PIN_NUMBER       14

// Clocked LEDs on hardware SPI: 11 = data, 13 = clock, as on the Teensy 3.2.
#define APA102_DATA_PIN_NUMBER  11
#define APA102_CLOCK_PIN_NUMBER 13
#define APA102_SPI_HZ           4000000
#define LPD8806_SPI_HZ          2000000

#define HAS_LPD8806             1
// The `load` control: how much of each pixel's time on the wire the pattern
// took to calculate. See ws2812_teensy4.h.
#define HAS_LOAD_CONTROL        1

#else
#error "ltp_stream_serial supports ATmega328P (Uno, Nano), Teensy 3.x and Teensy 4.x"
#endif

#endif // LTP_STREAM_SERIAL_CONFIG_H
