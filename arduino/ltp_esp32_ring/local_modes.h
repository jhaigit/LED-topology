/**
 * ESP32 Ring Controller - Local Display Modes
 *
 * Ring-adapted animations that seamlessly wrap around the 202-pixel ring.
 * Unlike linear strips, these animations have no visible start/end point.
 */

#ifndef LOCAL_MODES_H
#define LOCAL_MODES_H

#include <Arduino.h>
#include <FastLED.h>
#include "config.h"
#include "ring_driver.h"

class LocalModes {
public:
    LocalModes(RingDriver& driver)
        : leds(driver)
        , active(false)
        , currentMode(LOCAL_MODE_BLANK)
        , displayMode(LOCAL_MODE_BLANK)
        , position(0)
        , hue(0)
        , lastUpdate(0)
        , modeStartTime(0)
    {}

    // Start or switch to a local mode
    void start(uint8_t mode) {
        currentMode = mode;
        position = 0;
        hue = 0;
        lastUpdate = millis();
        modeStartTime = millis();

        if (mode == LOCAL_MODE_BLANK) {
            active = false;
            leds.clear();
            leds.show();
        } else {
            active = true;
            if (mode == LOCAL_MODE_CYCLE) {
                displayMode = LOCAL_MODE_CYLON;
            } else {
                displayMode = mode;
            }
        }
    }

    // Stop local mode (external command takes over)
    void stop() {
        if (active) {
            active = false;
            leds.clear();
        }
    }

    // Advance to next mode (for touch cycling)
    void nextMode() {
        uint8_t next;
        if (currentMode == LOCAL_MODE_CYCLE || currentMode == LOCAL_MODE_BLANK) {
            next = LOCAL_MODE_CYLON;
        } else {
            next = currentMode + 1;
            if (next >= LOCAL_MODE_COUNT) {
                next = LOCAL_MODE_CYLON;
            }
        }
        start(next);
    }

    // Update animation (call from loop)
    void update() {
        if (!active) return;

        uint32_t now = millis();

        // Get update interval for current mode
        uint32_t interval;
        switch (displayMode) {
            case LOCAL_MODE_CYLON:   interval = 15; break;
            case LOCAL_MODE_RAINBOW: interval = 20; break;
            case LOCAL_MODE_FIRE:    interval = 30; break;
            case LOCAL_MODE_SPARKLE: interval = 30; break;
            case LOCAL_MODE_CHASE:   interval = 40; break;
            default: interval = 50; break;
        }

        if (now - lastUpdate < interval) return;
        lastUpdate = now;

        // Handle cycle mode timing
        if (currentMode == LOCAL_MODE_CYCLE) {
            if (now - modeStartTime >= LOCAL_MODE_CYCLE_TIME) {
                modeStartTime = now;
                displayMode++;
                if (displayMode >= LOCAL_MODE_COUNT) {
                    displayMode = LOCAL_MODE_CYLON;
                }
                position = 0;
                leds.clear();
            }
        }

        // Run the current animation
        switch (displayMode) {
            case LOCAL_MODE_CYLON:   updateCylon(); break;
            case LOCAL_MODE_RAINBOW: updateRainbow(); break;
            case LOCAL_MODE_FIRE:    updateFire(); break;
            case LOCAL_MODE_SPARKLE: updateSparkle(); break;
            case LOCAL_MODE_CHASE:   updateChase(); break;
        }

        leds.show();
    }

    bool isActive() const { return active; }
    uint8_t getCurrentMode() const { return currentMode; }

private:
    RingDriver& leds;
    bool active;
    uint8_t currentMode;    // Configured mode (may be CYCLE)
    uint8_t displayMode;    // Actual mode being displayed
    uint16_t position;
    uint8_t hue;
    uint32_t lastUpdate;
    uint32_t modeStartTime;

    // Fire effect heat map
    uint8_t heat[RING_NUM_PIXELS];

    // Cylon: scanning eye that wraps around the ring seamlessly
    void updateCylon() {
        const uint8_t eyeSize = 5;
        const uint8_t fadeAmount = 64;

        CRGB* ring = leds.getRingLeds();

        // Fade all pixels
        for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
            ring[i].fadeToBlackBy(fadeAmount);
        }

        // Draw the eye (wrapping around ring)
        for (int8_t i = -eyeSize/2; i <= eyeSize/2; i++) {
            uint16_t pos = (position + i + RING_NUM_PIXELS) % RING_NUM_PIXELS;
            uint8_t brightness = 255 - abs(i) * 45;
            ring[pos] = CRGB(brightness, 0, 0);
        }

        // Move position around ring
        position = (position + 1) % RING_NUM_PIXELS;
    }

    // Rainbow: seamless hue rotation around the ring
    void updateRainbow() {
        CRGB* ring = leds.getRingLeds();

        for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
            // Offset hue by position in ring for seamless gradient
            uint8_t pixelHue = hue + (i * 256 / RING_NUM_PIXELS);
            ring[i] = CHSV(pixelHue, 255, 200);
        }

        hue++;  // Rotate the whole rainbow
    }

    // Fire: bidirectional fire effect (flames rise from bottom/top toward middle)
    void updateFire() {
        const uint8_t cooling = 55;
        const uint8_t sparking = 120;

        CRGB* ring = leds.getRingLeds();

        // Process ring as two halves with fire rising from each end toward the middle
        uint16_t halfLen = RING_NUM_PIXELS / 2;  // 101 for 202 pixels

        // Cool down every cell
        for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
            uint8_t cooldown = random8(0, ((cooling * 10) / halfLen) + 2);
            heat[i] = (heat[i] > cooldown) ? heat[i] - cooldown : 0;
        }

        // Heat rises (from each end toward middle)
        // First half: heat rises from pixel 0 toward halfLen (0 -> 100)
        for (int16_t i = halfLen; i >= 2; i--) {
            heat[i] = (heat[i - 1] + heat[i - 2] + heat[i - 2]) / 3;
        }
        // Second half: heat rises from end toward middle (201 -> 101)
        for (int16_t i = halfLen + 1; i <= RING_NUM_PIXELS - 3; i++) {
            heat[i] = (heat[i + 1] + heat[i + 2] + heat[i + 2]) / 3;
        }
        // Handle edge pixels near the end
        if (RING_NUM_PIXELS >= 2) {
            heat[RING_NUM_PIXELS - 2] = (heat[RING_NUM_PIXELS - 1] + heat[RING_NUM_PIXELS - 1]) / 2;
        }

        // Randomly ignite sparks at both ends
        if (random8() < sparking) {
            uint8_t y = random8(7);
            heat[y] = qadd8(heat[y], random8(160, 255));
        }
        if (random8() < sparking) {
            uint8_t y = random8(7);
            uint16_t pos = RING_NUM_PIXELS - 1 - y;
            heat[pos] = qadd8(heat[pos], random8(160, 255));
        }

        // Map heat to colors
        for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
            ring[i] = heatToColor(heat[i]);
        }
    }

    // Sparkle: random sparkles around the ring
    void updateSparkle() {
        const uint8_t fadeAmount = 20;

        CRGB* ring = leds.getRingLeds();

        // Fade all pixels
        for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
            ring[i].fadeToBlackBy(fadeAmount);
        }

        // Add random sparkles
        for (uint8_t i = 0; i < 4; i++) {
            uint16_t pos = random16(RING_NUM_PIXELS);
            ring[pos] = CHSV(random8(), 200, 255);
        }
    }

    // Chase: color chase that wraps seamlessly around the ring
    void updateChase() {
        const uint8_t chaseLength = 8;

        CRGB* ring = leds.getRingLeds();

        // Clear ring
        fill_solid(ring, RING_NUM_PIXELS, CRGB::Black);

        // Draw chase pattern with gradient tail
        for (uint8_t i = 0; i < chaseLength; i++) {
            uint16_t pos = (position + i) % RING_NUM_PIXELS;
            uint8_t brightness = 255 - i * 30;
            ring[pos] = CHSV(hue, 255, brightness);
        }

        position = (position + 1) % RING_NUM_PIXELS;
        hue += 2;
    }

    // Convert heat value to fire color
    CRGB heatToColor(uint8_t temperature) {
        // Scale to 0-191
        uint8_t t192 = scale8(temperature, 191);

        uint8_t heatRamp = t192 & 0x3F;  // 0-63
        heatRamp <<= 2;  // Scale to 0-252

        if (t192 < 64) {
            return CRGB(heatRamp, 0, 0);
        } else if (t192 < 128) {
            return CRGB(255, heatRamp, 0);
        } else {
            return CRGB(255, 255, heatRamp);
        }
    }
};

#endif // LOCAL_MODES_H
