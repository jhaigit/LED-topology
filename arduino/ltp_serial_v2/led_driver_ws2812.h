/**
 * LTP Serial Protocol v2 - WS2812/NeoPixel LED Driver
 *
 * Example driver for WS2812B LED strips using the Adafruit NeoPixel library.
 *
 * To use this driver:
 *   1. Install Adafruit NeoPixel library: arduino-cli lib install "Adafruit NeoPixel"
 *   2. Uncomment the #include and driver instantiation in ltp_serial_v2.ino
 *
 * WS2812 characteristics:
 * - Single data wire (no clock)
 * - 800 KHz data rate
 * - Native color order is GRB
 * - Timing-critical (uses interrupts disabled during transmission)
 */

#ifndef LTP_LED_DRIVER_WS2812_H
#define LTP_LED_DRIVER_WS2812_H

#include "led_driver.h"

// Uncomment to use Adafruit NeoPixel library
// #include <Adafruit_NeoPixel.h>

#ifdef Adafruit_NeoPixel_h

class LedDriverWS2812 : public LedDriver {
public:
    /**
     * Constructor
     * @param numPixels Number of LEDs in the strip
     * @param pin Data pin
     * @param type NeoPixel type (NEO_GRB + NEO_KHZ800 for WS2812B)
     */
    LedDriverWS2812(uint16_t numPixels, uint8_t pin, neoPixelType type = NEO_GRB + NEO_KHZ800)
        : LedDriver(numPixels, COLOR_GRB)
        , strip(numPixels, pin, type)
    {}

    void begin() override {
        strip.begin();
        strip.clear();
        strip.show();
    }

    void show() override {
        strip.setBrightness(brightness);
        strip.show();
    }

    uint8_t* getPixelBuffer() override {
        return strip.getPixels();
    }

    void setPixel(uint16_t index, uint8_t r, uint8_t g, uint8_t b) override {
        if (index < numPixels) {
            strip.setPixelColor(index, r, g, b);
        }
    }

    void clear() override {
        strip.clear();
    }

    uint8_t getLedType() const override {
        return LED_TYPE_WS2812;
    }

private:
    Adafruit_NeoPixel strip;
};

#else

// Stub driver when NeoPixel library is not installed
class LedDriverWS2812 : public LedDriver {
public:
    LedDriverWS2812(uint16_t numPixels, uint8_t pin, uint8_t type = 0)
        : LedDriver(numPixels, COLOR_GRB)
        , pixelBuffer(nullptr)
    {
        pixelBuffer = new uint8_t[numPixels * 3];
        if (pixelBuffer) memset(pixelBuffer, 0, numPixels * 3);
    }

    ~LedDriverWS2812() {
        if (pixelBuffer) delete[] pixelBuffer;
    }

    void begin() override {
        // NeoPixel library not installed - this is a stub
    }

    void show() override {
        // NeoPixel library not installed - this is a stub
    }

    uint8_t* getPixelBuffer() override {
        return pixelBuffer;
    }

    void setPixel(uint16_t index, uint8_t r, uint8_t g, uint8_t b) override {
        if (index < numPixels && pixelBuffer) {
            uint16_t offset = index * 3;
            pixelBuffer[offset + 0] = g; // GRB order
            pixelBuffer[offset + 1] = r;
            pixelBuffer[offset + 2] = b;
        }
    }

    uint8_t getLedType() const override {
        return LED_TYPE_WS2812;
    }

private:
    uint8_t* pixelBuffer;
};

#endif // Adafruit_NeoPixel_h

#endif // LTP_LED_DRIVER_WS2812_H
