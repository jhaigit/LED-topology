/**
 * LTP Stream (ESP32) - LED output without a frame buffer
 *
 * WS2812: the RMT peripheral sends the frame, and an encoder callback makes
 * the waveform for one pixel at a time as RMT asks for more - the pattern is
 * calculated inside that callback. Nothing the size of the strip is ever
 * held: not the pixels, and not the RMT symbols either (those would be
 * 96 bytes per pixel).
 *
 * APA102 / SK9822: SPI, sent in chunks of a few pixels from a small buffer
 * on the stack.
 */

#ifndef LTP_STREAM_LED_OUTPUT_H
#define LTP_STREAM_LED_OUTPUT_H

#include <Arduino.h>
#include <SPI.h>
#include <driver/rmt_tx.h>
#include <driver/rmt_encoder.h>
#include <soc/soc_caps.h>
#include <ltp_fx.h>
#include "config.h"

// RMT tick: 100 ns. WS2812 bit timing as in Espressif's led_strip example:
// 0 = 0.3 us high + 0.9 us low, 1 = 0.9 us high + 0.3 us low.
#define RMT_RESOLUTION_HZ     10000000
#define WS_TICKS_SHORT        3
#define WS_TICKS_LONG         9
#define WS_BITS_PER_PIXEL     24
#define RMT_TICKS_PER_US      (RMT_RESOLUTION_HZ / 1000000)

// RMT memory for the channel, in symbols (one WS2812 bit each). The interrupt
// that refills it must run before it empties; two blocks give WiFi's own
// interrupts twice the time to get out of the way.
#define RMT_MEM_SYMBOLS       (SOC_RMT_MEM_WORDS_PER_CHANNEL * 2)

// Longest wait for a frame to leave before giving up on it.
#define FRAME_TIMEOUT_MS      1000

// APA102 pixels sent per SPI call.
#define APA102_CHUNK_PIXELS   16
#define APA102_PIXEL_HEADER   0xFF  // 111 + 5-bit brightness, full
#define APA102_BLACK_HEADER   0xE0  // brightness zero

class LedOutput {
public:
    bool begin() {
        rmt_tx_channel_config_t channelConfig = {};
        channelConfig.gpio_num = (gpio_num_t)WS2812_PIN;
        channelConfig.clk_src = RMT_CLK_SRC_DEFAULT;
        channelConfig.resolution_hz = RMT_RESOLUTION_HZ;
        channelConfig.mem_block_symbols = RMT_MEM_SYMBOLS;
        channelConfig.trans_queue_depth = 1;
        if (rmt_new_tx_channel(&channelConfig, &channel) != ESP_OK) return false;

        rmt_simple_encoder_config_t encoderConfig = {};
        encoderConfig.callback = encodePixel;
        encoderConfig.arg = this;
        encoderConfig.min_chunk_size = WS_BITS_PER_PIXEL;
        if (rmt_new_simple_encoder(&encoderConfig, &encoder) != ESP_OK) return false;
        if (rmt_enable(channel) != ESP_OK) return false;

        SPI.begin(APA102_CLOCK_PIN, -1, APA102_DATA_PIN, -1);
        ready = true;
        return true;
    }

    // Send one frame from the pattern engine: `count` pixels, then black up
    // to `total`. `order[k]` says which of r, g, b is the k-th byte on the
    // wire. False if the frame did not go out whole.
    bool show(uint8_t ledType, uint16_t count, uint16_t total, const uint8_t order[3]) {
        if (!ready || total == 0) return ready;
        return ledType == FXLED_APA102 ? showApa102(count, total, order)
                                       : showWs2812(count, total, order);
    }

private:
    rmt_channel_handle_t channel = nullptr;
    rmt_encoder_handle_t encoder = nullptr;
    bool ready = false;

    // The frame being encoded. Written by show() before the transmission
    // starts and by the encoder callback until it ends, never by both at once.
    volatile uint16_t nextPixel = 0;
    uint16_t patternPixels = 0;
    uint16_t totalPixels = 0;
    uint8_t wireOrder[3] = { 1, 0, 2 };

    // Called by the RMT driver, in interrupt context, each time it has room
    // for more symbols. Encodes exactly one pixel per call.
    static size_t encodePixel(const void* data, size_t dataSize, size_t symbolsWritten,
                              size_t symbolsFree, rmt_symbol_word_t* symbols, bool* done,
                              void* arg) {
        (void)data; (void)dataSize; (void)symbolsWritten;
        LedOutput* self = (LedOutput*)arg;
        if (symbolsFree < WS_BITS_PER_PIXEL) return 0;  // ask again with more room

        if (self->nextPixel >= self->totalPixels) {
            // All pixels are out: hold the line low so the LEDs latch.
            rmt_symbol_word_t latch;
            latch.level0 = 0;
            latch.duration0 = LED_LATCH_US * RMT_TICKS_PER_US / 2;
            latch.level1 = 0;
            latch.duration1 = LED_LATCH_US * RMT_TICKS_PER_US / 2;
            symbols[0] = latch;
            *done = true;
            return 1;
        }

        uint8_t rgb[3] = { 0, 0, 0 };
        if (self->nextPixel < self->patternPixels) fxNext(rgb);
        self->nextPixel = self->nextPixel + 1;

        rmt_symbol_word_t zero, one;
        zero.level0 = 1; zero.duration0 = WS_TICKS_SHORT;
        zero.level1 = 0; zero.duration1 = WS_TICKS_LONG;
        one.level0 = 1;  one.duration0 = WS_TICKS_LONG;
        one.level1 = 0;  one.duration1 = WS_TICKS_SHORT;

        size_t n = 0;
        for (uint8_t k = 0; k < 3; k++) {
            uint8_t value = rgb[self->wireOrder[k]];
            for (uint8_t mask = 0x80; mask; mask >>= 1) {
                symbols[n++] = (value & mask) ? one : zero;
            }
        }
        return n;
    }

    bool showWs2812(uint16_t count, uint16_t total, const uint8_t order[3]) {
        nextPixel = 0;
        patternPixels = count;
        totalPixels = total;
        memcpy(wireOrder, order, 3);

        // The encoder ignores the payload, but the driver wants one.
        static const uint8_t placeholder = 0;
        rmt_transmit_config_t transmitConfig = {};
        if (rmt_transmit(channel, encoder, &placeholder, sizeof(placeholder),
                         &transmitConfig) != ESP_OK) {
            return false;
        }
        // The pattern engine is in use by the encoder until this returns.
        if (rmt_tx_wait_all_done(channel, FRAME_TIMEOUT_MS) != ESP_OK) return false;
        return nextPixel >= total;
    }

    bool showApa102(uint16_t count, uint16_t total, const uint8_t order[3]) {
        SPI.beginTransaction(SPISettings(APA102_SPI_HZ, MSBFIRST, SPI_MODE0));
        const uint8_t startFrame[4] = { 0, 0, 0, 0 };
        SPI.writeBytes(startFrame, sizeof(startFrame));

        uint8_t chunk[APA102_CHUNK_PIXELS * 4];
        uint16_t sent = 0;
        while (sent < total) {
            size_t n = 0;
            for (uint8_t i = 0; i < APA102_CHUNK_PIXELS && sent < total; i++, sent++) {
                if (sent < count) {
                    uint8_t rgb[3];
                    fxNext(rgb);
                    chunk[n++] = APA102_PIXEL_HEADER;
                    chunk[n++] = rgb[order[0]];
                    chunk[n++] = rgb[order[1]];
                    chunk[n++] = rgb[order[2]];
                } else {
                    chunk[n++] = APA102_BLACK_HEADER;
                    chunk[n++] = 0;
                    chunk[n++] = 0;
                    chunk[n++] = 0;
                }
            }
            SPI.writeBytes(chunk, n);
        }

        // End frame: half a clock per LED pushes the last pixels through the
        // chain, and 32 more zero bits make an SK9822 show the frame. Zeros,
        // not ones, so LEDs beyond `total` are not lit white.
        memset(chunk, 0, sizeof(chunk));
        uint16_t endBytes = total / 16 + 1 + 4;
        while (endBytes) {
            uint16_t n = endBytes > sizeof(chunk) ? sizeof(chunk) : endBytes;
            SPI.writeBytes(chunk, n);
            endBytes -= n;
        }
        SPI.endTransaction();
        return true;
    }
};

#endif // LTP_STREAM_LED_OUTPUT_H
