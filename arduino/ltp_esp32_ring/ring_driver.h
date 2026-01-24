/**
 * ESP32 Ring Controller - Ring LED Driver
 *
 * Manages 202 APA102 LEDs in a continuous ring plus 4 WS2812 satellite LEDs.
 * WS2812 LEDs can mirror colors from corresponding ring positions or flash
 * independently for touch feedback.
 */

#ifndef RING_DRIVER_H
#define RING_DRIVER_H

#include <Arduino.h>
#include <FastLED.h>
#include "config.h"

class RingDriver {
public:
    RingDriver()
        : brightness(255)
        , ws2812Offset(WS2812_DEFAULT_OFFSET)
        , flashingLed(-1)
        , flashCount(0)
        , flashState(false)
        , lastFlashTime(0)
    {}

    void begin() {
        // Initialize APA102 ring on hardware SPI
        FastLED.addLeds<APA102, RING_DATA_PIN, RING_CLOCK_PIN, RING_COLOR_ORDER, DATA_RATE_MHZ(8)>(
            ringLeds, RING_NUM_PIXELS);

        // Initialize WS2812 satellites (each on separate pin)
        FastLED.addLeds<WS2812, WS2812_PIN_0, WS2812_COLOR_ORDER>(ws2812Leds + 0, 1);
        FastLED.addLeds<WS2812, WS2812_PIN_1, WS2812_COLOR_ORDER>(ws2812Leds + 1, 1);
        FastLED.addLeds<WS2812, WS2812_PIN_2, WS2812_COLOR_ORDER>(ws2812Leds + 2, 1);
        FastLED.addLeds<WS2812, WS2812_PIN_3, WS2812_COLOR_ORDER>(ws2812Leds + 3, 1);

        FastLED.setBrightness(brightness);
        clear();
        show();
    }

    // Show updates ring LEDs and optionally updates WS2812 from ring
    void show() {
        // Update WS2812 mirroring from ring (unless currently flashing)
        if (flashingLed < 0) {
            updateWS2812FromRing();
        }
        FastLED.show();
    }

    // Set a single ring pixel (RGB order)
    void setRingPixel(uint16_t idx, uint8_t r, uint8_t g, uint8_t b) {
        if (idx < RING_NUM_PIXELS) {
            ringLeds[idx] = CRGB(r, g, b);
        }
    }

    // Get a single ring pixel (RGB order)
    void getRingPixel(uint16_t idx, uint8_t& r, uint8_t& g, uint8_t& b) {
        if (idx < RING_NUM_PIXELS) {
            r = ringLeds[idx].r;
            g = ringLeds[idx].g;
            b = ringLeds[idx].b;
        } else {
            r = g = b = 0;
        }
    }

    // Alias for protocol compatibility
    void setPixel(uint16_t idx, uint8_t r, uint8_t g, uint8_t b) {
        setRingPixel(idx, r, g, b);
    }

    void getPixel(uint16_t idx, uint8_t& r, uint8_t& g, uint8_t& b) {
        getRingPixel(idx, r, g, b);
    }

    // Clear all pixels
    void clear() {
        fill_solid(ringLeds, RING_NUM_PIXELS, CRGB::Black);
        fill_solid(ws2812Leds, WS2812_NUM_LEDS, CRGB::Black);
    }

    // Fill all ring pixels with a color
    void fill(uint8_t r, uint8_t g, uint8_t b) {
        fill_solid(ringLeds, RING_NUM_PIXELS, CRGB(r, g, b));
    }

    // Fill a range of ring pixels
    void fillRange(uint16_t start, uint16_t end, uint8_t r, uint8_t g, uint8_t b) {
        if (start >= RING_NUM_PIXELS) return;
        if (end > RING_NUM_PIXELS) end = RING_NUM_PIXELS;
        for (uint16_t i = start; i < end; i++) {
            ringLeds[i] = CRGB(r, g, b);
        }
    }

    // Brightness control
    void setBrightness(uint8_t b) {
        brightness = b;
        FastLED.setBrightness(brightness);
    }

    uint8_t getBrightness() const { return brightness; }

    // WS2812 offset control
    void setWS2812Offset(uint8_t offset) {
        ws2812Offset = offset % RING_NUM_PIXELS;
    }

    uint8_t getWS2812Offset() const { return ws2812Offset; }

    // Flash a WS2812 LED for touch feedback
    void flashWS2812(uint8_t ledIdx) {
        if (ledIdx >= WS2812_NUM_LEDS) return;

        flashingLed = ledIdx;
        flashCount = WS2812_FLASH_COUNT * 2; // On/off pairs
        flashState = true;
        lastFlashTime = millis();

        // Set to white for first flash
        ws2812Leds[ledIdx] = CRGB::White;
    }

    // Update flash animation (call from loop)
    void updateFlash() {
        if (flashingLed < 0) return;

        uint32_t now = millis();
        uint32_t elapsed = now - lastFlashTime;
        uint32_t interval = flashState ? WS2812_FLASH_ON_MS : WS2812_FLASH_OFF_MS;

        if (elapsed >= interval) {
            lastFlashTime = now;
            flashCount--;

            if (flashCount <= 0) {
                // Flash complete, return to mirroring
                flashingLed = -1;
                return;
            }

            flashState = !flashState;
            ws2812Leds[flashingLed] = flashState ? CRGB::White : CRGB::Black;
            FastLED.show();
        }
    }

    // Check if currently flashing
    bool isFlashing() const { return flashingLed >= 0; }

    // Get pixel buffer for protocol compatibility
    uint8_t* getPixelBuffer() {
        return (uint8_t*)ringLeds;
    }

    // Number of pixels (ring only for protocol)
    uint16_t getNumPixels() const { return RING_NUM_PIXELS; }
    uint8_t getBytesPerPixel() const { return 3; }
    uint8_t getColorFormat() const { return 0x03; } // COLOR_RGB
    uint8_t getLedType() const { return 0x02; } // LED_TYPE_APA102

    // Direct access to LED arrays for local modes
    CRGB* getRingLeds() { return ringLeds; }
    CRGB* getWS2812Leds() { return ws2812Leds; }

private:
    CRGB ringLeds[RING_NUM_PIXELS];
    CRGB ws2812Leds[WS2812_NUM_LEDS];
    uint8_t brightness;
    uint8_t ws2812Offset;

    // Flash state
    int8_t flashingLed;
    int8_t flashCount;
    bool flashState;
    uint32_t lastFlashTime;

    // Update WS2812 colors from corresponding ring positions
    void updateWS2812FromRing() {
        for (uint8_t i = 0; i < WS2812_NUM_LEDS; i++) {
            // Calculate ring position for this WS2812
            uint16_t ringPos = (ws2812Offset + i * WS2812_RING_SPACING) % RING_NUM_PIXELS;

            // Weighted average of nearby pixels
            uint16_t rSum = 0, gSum = 0, bSum = 0;
            uint8_t weightSum = 0;

            for (int8_t offset = -WS2812_SAMPLE_RADIUS; offset <= WS2812_SAMPLE_RADIUS; offset++) {
                uint16_t samplePos = (ringPos + offset + RING_NUM_PIXELS) % RING_NUM_PIXELS;

                uint8_t weight;
                if (offset == 0) {
                    weight = WS2812_WEIGHT_CENTER;
                } else if (abs(offset) == 1) {
                    weight = WS2812_WEIGHT_ADJACENT;
                } else {
                    weight = WS2812_WEIGHT_OUTER;
                }

                rSum += ringLeds[samplePos].r * weight;
                gSum += ringLeds[samplePos].g * weight;
                bSum += ringLeds[samplePos].b * weight;
                weightSum += weight;
            }

            ws2812Leds[i].r = rSum / weightSum;
            ws2812Leds[i].g = gSum / weightSum;
            ws2812Leds[i].b = bSum / weightSum;
        }
    }
};

#endif // RING_DRIVER_H
