/**
 * LTP Serial Protocol v2 - OctoWS2811 LED Driver
 *
 * Driver for Teensy 3.x with OctoWS2811 adapter.
 * Supports 8 parallel WS2811/WS2812 outputs.
 */

#ifndef LTP_LED_DRIVER_OCTO_H
#define LTP_LED_DRIVER_OCTO_H

#include <OctoWS2811.h>
#include "config.h"
#include "protocol.h"

// OctoWS2811 requires these memory arrays at global scope for DMAMEM
DMAMEM static uint32_t octoDisplayMemory[PIXELS_PER_STRIP * NUM_STRIPS];
static uint32_t octoDrawingMemory[PIXELS_PER_STRIP * NUM_STRIPS];

class LedDriverOcto {
public:
    LedDriverOcto()
        : leds(PIXELS_PER_STRIP, octoDisplayMemory, octoDrawingMemory, LED_COLOR_ORDER)
        , brightness(255)
    {}

    void begin() {
        leds.begin();
        clear();
        show();
    }

    void show() {
        leds.show();
    }

    /**
     * Map logical pixel index to physical pixel index.
     *
     * In matrix modes, this handles:
     * - Mapping linear index to strip and position
     * - Serpentine folding for MATRIX_16 mode
     *
     * @param logicalIndex The pixel index from the client (0 to REPORT_PIXELS-1)
     * @return Physical pixel index for OctoWS2811 (0 to TOTAL_PIXELS-1)
     */
    uint16_t mapPixel(uint16_t logicalIndex) {
#if MATRIX_MODE
    #if MATRIX_FOLD == 2
        // MATRIX_16: Each physical strip is 2 logical rows with serpentine
        // Logical layout: 16 rows of (PIXELS_PER_STRIP/2) pixels each
        // Physical layout: 8 strips of PIXELS_PER_STRIP pixels each

        uint16_t row = logicalIndex / MATRIX_WIDTH;
        uint16_t col = logicalIndex % MATRIX_WIDTH;

        // Which physical strip (0-7)?
        uint8_t physStrip = row / 2;

        // Which half of the strip (0=first half, 1=second half)?
        uint8_t halfIndex = row % 2;

        uint16_t physPos;
        if (halfIndex == 0) {
            // First half: pixels 0 to (MATRIX_WIDTH-1), direct mapping
            physPos = col;
        } else {
            // Second half: pixels MATRIX_WIDTH to (PIXELS_PER_STRIP-1)
            // Serpentine: reverse direction
            physPos = PIXELS_PER_STRIP - 1 - col;
        }

        return physStrip * PIXELS_PER_STRIP + physPos;

    #else
        // MATRIX_8: Simple row-major mapping, no serpentine
        uint16_t row = logicalIndex / MATRIX_WIDTH;
        uint16_t col = logicalIndex % MATRIX_WIDTH;
        return row * PIXELS_PER_STRIP + col;
    #endif
#else
        // STRIPS mode: direct mapping
        return logicalIndex;
#endif
    }

    /**
     * Set a pixel using logical coordinates.
     * The mapping handles serpentine and matrix layouts.
     */
    void setPixel(uint16_t logicalIndex, uint8_t r, uint8_t g, uint8_t b) {
        if (logicalIndex >= getLogicalPixelCount()) return;

        uint16_t physIndex = mapPixel(logicalIndex);

        // Apply brightness
        r = scale8(r);
        g = scale8(g);
        b = scale8(b);

        // OctoWS2811 uses a packed format
        uint32_t color = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
        leds.setPixel(physIndex, color);
    }

    /**
     * Set a pixel on a specific strip (for STRIPS mode).
     * In matrix mode, stripId must be 0.
     */
    void setStripPixel(uint8_t stripId, uint16_t pos, uint8_t r, uint8_t g, uint8_t b) {
#if MATRIX_MODE
        // In matrix mode, there's only "strip 0" which is the whole matrix
        if (stripId != 0) return;
        setPixel(pos, r, g, b);
#else
        // In strips mode, directly address physical strip
        if (stripId >= NUM_STRIPS) return;
        if (pos >= PIXELS_PER_STRIP) return;

        r = scale8(r);
        g = scale8(g);
        b = scale8(b);

        uint16_t physIndex = stripId * PIXELS_PER_STRIP + pos;
        uint32_t color = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
        leds.setPixel(physIndex, color);
#endif
    }

    void clear() {
        for (uint16_t i = 0; i < TOTAL_PIXELS; i++) {
            leds.setPixel(i, 0);
        }
    }

    void fill(uint8_t r, uint8_t g, uint8_t b) {
        r = scale8(r);
        g = scale8(g);
        b = scale8(b);
        uint32_t color = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;

        for (uint16_t i = 0; i < TOTAL_PIXELS; i++) {
            leds.setPixel(i, color);
        }
    }

    void fillStrip(uint8_t stripId, uint8_t r, uint8_t g, uint8_t b) {
#if MATRIX_MODE
        if (stripId != 0) return;
        fill(r, g, b);
#else
        if (stripId >= NUM_STRIPS) return;

        r = scale8(r);
        g = scale8(g);
        b = scale8(b);
        uint32_t color = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;

        uint16_t start = stripId * PIXELS_PER_STRIP;
        for (uint16_t i = 0; i < PIXELS_PER_STRIP; i++) {
            leds.setPixel(start + i, color);
        }
#endif
    }

    void fillRange(uint8_t stripId, uint16_t start, uint16_t end, uint8_t r, uint8_t g, uint8_t b) {
#if MATRIX_MODE
        if (stripId != 0) return;
        for (uint16_t i = start; i < end && i < getLogicalPixelCount(); i++) {
            setPixel(i, r, g, b);
        }
#else
        if (stripId >= NUM_STRIPS) return;
        end = min(end, (uint16_t)PIXELS_PER_STRIP);

        r = scale8(r);
        g = scale8(g);
        b = scale8(b);
        uint32_t color = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;

        uint16_t physStart = stripId * PIXELS_PER_STRIP + start;
        for (uint16_t i = start; i < end; i++) {
            leds.setPixel(physStart + i - start, color);
        }
#endif
    }

    // Getters
    uint8_t getStripCount() const { return REPORT_STRIPS; }
    uint16_t getPixelsPerStrip() const { return REPORT_PIXELS; }
    uint16_t getLogicalPixelCount() const { return REPORT_STRIPS * REPORT_PIXELS; }
    uint16_t getPhysicalPixelCount() const { return TOTAL_PIXELS; }
    uint8_t getColorFormat() const { return COLOR_GRB; }
    uint8_t getBytesPerPixel() const { return 3; }
    uint8_t getLedType() const { return LED_TYPE_WS2812; }

    // Brightness
    void setBrightness(uint8_t b) { brightness = b; }
    uint8_t getBrightness() const { return brightness; }

#if MATRIX_MODE
    uint16_t getMatrixWidth() const { return MATRIX_WIDTH; }
    uint16_t getMatrixHeight() const { return MATRIX_HEIGHT; }
#endif

    // Get raw pixel data for readback (physical buffer)
    uint32_t getPixelColor(uint16_t physIndex) {
        if (physIndex >= TOTAL_PIXELS) return 0;
        return octoDrawingMemory[physIndex];
    }

private:
    OctoWS2811 leds;
    uint8_t brightness;

    uint8_t scale8(uint8_t value) const {
        return ((uint16_t)value * (uint16_t)(brightness + 1)) >> 8;
    }
};

#endif // LTP_LED_DRIVER_OCTO_H
