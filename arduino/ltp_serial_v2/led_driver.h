/**
 * LTP Serial Protocol v2 - LED Driver Abstraction
 *
 * Base class for LED strip drivers. Implement a subclass for each LED chip type.
 */

#ifndef LTP_LED_DRIVER_H
#define LTP_LED_DRIVER_H

#include <Arduino.h>
#include "protocol.h"

class LedDriver {
public:
    LedDriver(uint16_t numPixels, uint8_t colorFormat = COLOR_GRB)
        : numPixels(numPixels)
        , colorFormat(colorFormat)
        , bytesPerPixel((colorFormat & 0x0F) == 0x04 ? 4 : 3)
        , brightness(255)
    {}

    virtual ~LedDriver() {}

    // Initialize the driver (call in setup())
    virtual void begin() = 0;

    // Push pixel buffer to LEDs
    virtual void show() = 0;

    // Get pixel buffer for direct manipulation
    virtual uint8_t* getPixelBuffer() = 0;

    // Set a single pixel (RGB order, driver converts internally)
    virtual void setPixel(uint16_t index, uint8_t r, uint8_t g, uint8_t b) = 0;

    // Set a single pixel with white channel (RGBW strips)
    virtual void setPixelW(uint16_t index, uint8_t r, uint8_t g, uint8_t b, uint8_t w) {
        setPixel(index, r, g, b); // Default: ignore white
    }

    // Clear all pixels to black
    virtual void clear() {
        uint8_t* buf = getPixelBuffer();
        memset(buf, 0, numPixels * bytesPerPixel);
    }

    // Fill all pixels with a color
    virtual void fill(uint8_t r, uint8_t g, uint8_t b) {
        for (uint16_t i = 0; i < numPixels; i++) {
            setPixel(i, r, g, b);
        }
    }

    // Fill a range with a color (exclusive end)
    virtual void fillRange(uint16_t start, uint16_t end, uint8_t r, uint8_t g, uint8_t b) {
        end = min(end, numPixels);
        for (uint16_t i = start; i < end; i++) {
            setPixel(i, r, g, b);
        }
    }

    // Getters
    uint16_t getNumPixels() const { return numPixels; }
    uint8_t getColorFormat() const { return colorFormat; }
    uint8_t getBytesPerPixel() const { return bytesPerPixel; }
    uint16_t getBufferSize() const { return numPixels * bytesPerPixel; }

    // Brightness control (0-255)
    void setBrightness(uint8_t b) { brightness = b; }
    uint8_t getBrightness() const { return brightness; }

    // Get LED type identifier (override in subclass)
    virtual uint8_t getLedType() const = 0;

protected:
    uint16_t numPixels;
    uint8_t colorFormat;
    uint8_t bytesPerPixel;
    uint8_t brightness;

    // Apply brightness scaling to a color component
    uint8_t scale8(uint8_t value) const {
        return ((uint16_t)value * (uint16_t)(brightness + 1)) >> 8;
    }
};

#endif // LTP_LED_DRIVER_H
