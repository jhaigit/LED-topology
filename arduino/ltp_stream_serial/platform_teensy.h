/**
 * LTP Stream (serial) - Teensy hardware layer
 *
 * Shared by the Teensy 3.x and 4.x builds: APA102 and LPD8806 over hardware
 * SPI, a pixel at a time, and the interface the sketch calls. The WS2812
 * output and the clock differ too much between the two processors to share:
 *   ws2812_teensy3.h  bit-banged, the pattern calculated between pixels
 *   ws2812_teensy4.h  sent by a UART while the pattern is calculated
 */

#ifndef LTP_STREAM_PLATFORM_TEENSY_H
#define LTP_STREAM_PLATFORM_TEENSY_H

#include <Arduino.h>
#include <SPI.h>

#if defined(__IMXRT1062__)
#include "ws2812_teensy4.h"
#else
#include "ws2812_teensy3.h"
#endif

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
#if HAS_LOAD_CONTROL
    fxRuntime.load = 0;  // only a WS2812 frame has a deadline to measure against
#endif
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
