/**
 * LTP Stream (serial) - WS2812 output and clock for Teensy 3.x
 *
 * The 72 MHz Cortex-M4 calculates a base pattern's pixel in well under the
 * 5 us an older WS2812 tolerates between bits, so the frame is simply
 * bit-banged with the pattern calculated between pixels. Interrupts are off
 * for the frame; USB holds the host's data until they are back on.
 */

#ifndef LTP_STREAM_WS2812_TEENSY3_H
#define LTP_STREAM_WS2812_TEENSY3_H

#include <Arduino.h>

#if F_CPU < 48000000
#error "WS2812 timing in ws2812_teensy3.h needs a CPU clock of 48 MHz or more"
#endif

// ============================================================================
// CLOCK (CPU cycle counter)
// ============================================================================

// millis() loses time while a frame holds interrupts off; the cycle counter
// does not. It wraps after 59 s at 72 MHz and is read far more often.
#define CYCLES_PER_MS (F_CPU / 1000)
#define CYCLES_PER_US (F_CPU / 1000000)

static void clockInit() {
    ARM_DEMCR |= ARM_DEMCR_TRCENA;
    ARM_DWT_CTRL |= ARM_DWT_CTRL_CYCCNTENA;
}

static uint16_t platformElapsedMs() {
    static uint32_t last;
    static uint32_t pending;  // cycles not yet handed out as milliseconds
    uint32_t now = ARM_DWT_CYCCNT;
    pending += now - last;
    last = now;
    uint32_t ms = pending / CYCLES_PER_MS;
    pending -= ms * CYCLES_PER_MS;
    return ms > 0xFFFF ? 0xFFFF : (uint16_t)ms;
}

// ============================================================================
// WS2812 (bit-banged against the cycle counter)
// ============================================================================

// 800 kHz bits: 1.25 us period, 0.25 us high for a 0, 0.8 us high for a 1 -
// the timing Adafruit_NeoPixel uses on this processor.
#define WS_CYCLES_PERIOD (F_CPU / 800000)
#define WS_CYCLES_T0H    (F_CPU / 4000000)
#define WS_CYCLES_T1H    (F_CPU / 1250000)

static uint32_t wsFrameEndCycle;  // when the data line last went quiet

static void wsInit() {
    pinMode(WS2812_PIN_NUMBER, OUTPUT);
    digitalWriteFast(WS2812_PIN_NUMBER, LOW);
}

static bool wsShow(uint16_t count, uint16_t total, const uint8_t order[3]) {
    if (total == 0) return true;

    // Let the previous frame latch.
    while ((uint32_t)(ARM_DWT_CYCCNT - wsFrameEndCycle) < (uint32_t)LED_LATCH_US * CYCLES_PER_US) {}

    uint8_t rgb[3];
    noInterrupts();
    uint32_t bitStart = ARM_DWT_CYCCNT - WS_CYCLES_PERIOD;
    for (uint16_t i = 0; i < total; i++) {
        // Calculated while the last bit of the previous pixel is still low.
        if (i < count) {
            fxNext(rgb);
        } else {
            rgb[0] = rgb[1] = rgb[2] = 0;
        }
        for (uint8_t k = 0; k < 3; k++) {
            uint8_t value = rgb[order[k]];
            for (uint8_t mask = 0x80; mask; mask >>= 1) {
                while ((uint32_t)(ARM_DWT_CYCCNT - bitStart) < WS_CYCLES_PERIOD) {}
                bitStart = ARM_DWT_CYCCNT;
                digitalWriteFast(WS2812_PIN_NUMBER, HIGH);
                uint32_t high = (value & mask) ? WS_CYCLES_T1H : WS_CYCLES_T0H;
                while ((uint32_t)(ARM_DWT_CYCCNT - bitStart) < high) {}
                digitalWriteFast(WS2812_PIN_NUMBER, LOW);
            }
        }
    }
    wsFrameEndCycle = ARM_DWT_CYCCNT;
    interrupts();
    return true;
}

#endif // LTP_STREAM_WS2812_TEENSY3_H
