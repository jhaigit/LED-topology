/**
 * LTP Stream (serial) - Teensy 3.x hardware layer
 *
 * The 72 MHz Cortex-M4 calculates a pixel in well under the 5 us an older
 * WS2812 tolerates between bits, so the WS2812 frame is simply bit-banged
 * with the pattern calculated between pixels. Interrupts are off for the
 * frame; USB holds the host's data until they are back on.
 *
 * APA102 and LPD8806 go out over hardware SPI, a pixel at a time.
 */

#ifndef LTP_STREAM_PLATFORM_TEENSY_H
#define LTP_STREAM_PLATFORM_TEENSY_H

#include <Arduino.h>
#include <SPI.h>

#if F_CPU < 48000000
#error "WS2812 timing in platform_teensy.h needs a CPU clock of 48 MHz or more"
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

// ============================================================================
// APA102 / SK9822 and LPD8806 (hardware SPI)
// ============================================================================

#define APA102_PIXEL_HEADER  0xFF  // 111 + 5-bit brightness, full
#define APA102_BLACK_HEADER  0xE0  // brightness zero
#define LPD8806_DATA_FLAG    0x80  // high bit marks a colour byte; the low 7 bits are the level
#define LPD8806_LEDS_PER_LATCH_BYTE 32

static void spiInit() {
    SPI.begin();
}

static bool apaShow(uint16_t count, uint16_t total, const uint8_t order[3]) {
    SPI.beginTransaction(SPISettings(APA102_SPI_HZ, MSBFIRST, SPI_MODE0));
    for (uint8_t i = 0; i < 4; i++) SPI.transfer(0x00);  // start frame

    uint8_t rgb[3];
    for (uint16_t i = 0; i < total; i++) {
        if (i < count) {
            fxNext(rgb);
            SPI.transfer(APA102_PIXEL_HEADER);
            SPI.transfer(rgb[order[0]]);
            SPI.transfer(rgb[order[1]]);
            SPI.transfer(rgb[order[2]]);
        } else {
            SPI.transfer(APA102_BLACK_HEADER);
            SPI.transfer(0);
            SPI.transfer(0);
            SPI.transfer(0);
        }
    }

    // End frame: half a clock per LED pushes the last pixels through the
    // chain, and 32 more zero bits make an SK9822 show the frame. Zeros, not
    // ones, so LEDs beyond `total` are not lit white.
    uint16_t endBytes = total / 16 + 1 + 4;
    for (uint16_t i = 0; i < endBytes; i++) SPI.transfer(0x00);
    SPI.endTransaction();
    return true;
}

static void lpdLatch(uint16_t total) {
    uint16_t latchBytes = (total + LPD8806_LEDS_PER_LATCH_BYTE - 1) / LPD8806_LEDS_PER_LATCH_BYTE;
    for (uint16_t i = 0; i < latchBytes; i++) SPI.transfer(0x00);
}

static bool lpdShow(uint16_t count, uint16_t total, const uint8_t order[3]) {
    SPI.beginTransaction(SPISettings(LPD8806_SPI_HZ, MSBFIRST, SPI_MODE0));
    lpdLatch(total);  // returns every LED to "expecting its first byte"

    uint8_t rgb[3];
    for (uint16_t i = 0; i < total; i++) {
        if (i < count) {
            fxNext(rgb);
        } else {
            rgb[0] = rgb[1] = rgb[2] = 0;
        }
        for (uint8_t k = 0; k < 3; k++) {
            SPI.transfer(LPD8806_DATA_FLAG | (rgb[order[k]] >> 1));
        }
    }
    lpdLatch(total);
    SPI.endTransaction();
    return true;
}

// ============================================================================
// PLATFORM INTERFACE
// ============================================================================

#define HOST Serial

static void platformInit() {
    clockInit();
    wsInit();
    spiInit();
    Serial.begin(SERIAL_BAUD);  // USB: the baud rate is ignored
}

// Send one frame from the pattern engine: `count` pixels, then black up to
// `total`. False if the frame did not go out whole.
static bool platformShow(uint8_t ledType, uint16_t count, uint16_t total, const uint8_t order[3]) {
    switch (ledType) {
        case FXLED_APA102:  return apaShow(count, total, order);
        case FXLED_LPD8806: return lpdShow(count, total, order);
        default:            return wsShow(count, total, order);
    }
}

static void platformPatternChanged() {}

// USB serial is flow-controlled; host bytes are never dropped.
static bool platformHostOverflow() { return false; }
static void platformHostDiscard() {}

static void platformReboot() {
    Serial.flush();
    delay(50);  // let USB deliver the acknowledgement
    SCB_AIRCR = 0x05FA0004;  // system reset request
    for (;;) {}
}

static uint8_t platformLedTypeCode(uint8_t ledType) {
    switch (ledType) {
        case FXLED_APA102:  return LED_TYPE_APA102;
        case FXLED_LPD8806: return LED_TYPE_LPD8806;
        default:            return LED_TYPE_WS2812;
    }
}

static uint8_t platformDataPin(uint8_t ledType) {
    return ledType == FXLED_WS2812 ? WS2812_PIN_NUMBER : APA102_DATA_PIN_NUMBER;
}

static uint8_t platformClockPin(uint8_t ledType) {
    return ledType == FXLED_WS2812 ? 0 : APA102_CLOCK_PIN_NUMBER;
}

#endif // LTP_STREAM_PLATFORM_TEENSY_H
