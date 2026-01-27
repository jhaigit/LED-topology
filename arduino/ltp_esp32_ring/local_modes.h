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
#include "touch_handler.h"

class LocalModes {
public:
    LocalModes(RingDriver& driver)
        : leds(driver)
        , touchHandler(nullptr)
        , active(false)
        , currentMode(LOCAL_MODE_BLANK)
        , displayMode(LOCAL_MODE_BLANK)
        , position(0)
        , hue(0)
        , lastUpdate(0)
        , modeStartTime(0)
    {}

    // Set touch handler for touch-reactive modes
    void setTouchHandler(TouchHandler* handler) {
        touchHandler = handler;
    }

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

            // Initialize mitosis mode
            if (displayMode == LOCAL_MODE_MITOSIS) {
                initMitosis();
            }

            // Initialize touch mode
            if (displayMode == LOCAL_MODE_TOUCH) {
                initTouch();
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
            case LOCAL_MODE_MITOSIS: interval = 25; break;
            case LOCAL_MODE_TOUCH:   interval = 20; break;
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
            case LOCAL_MODE_MITOSIS: updateMitosis(); break;
            case LOCAL_MODE_TOUCH:   updateTouch(); break;
        }

        leds.show();
    }

    bool isActive() const { return active; }
    uint8_t getCurrentMode() const { return currentMode; }

private:
    RingDriver& leds;
    TouchHandler* touchHandler;
    bool active;
    uint8_t currentMode;    // Configured mode (may be CYCLE)
    uint8_t displayMode;    // Actual mode being displayed
    uint16_t position;
    uint8_t hue;
    uint32_t lastUpdate;
    uint32_t modeStartTime;

    // Fire effect heat map
    uint8_t heat[RING_NUM_PIXELS];

    // Mitosis mode data
    static const uint8_t MAX_CIRCLERS = 8;
    struct Circler {
        float position;      // 0 to RING_NUM_PIXELS
        float speed;         // pixels per update, can be negative
        uint8_t hue;         // current color hue
        int8_t hueSpeed;     // hue change rate
        bool active;         // is this circler in use
    };
    Circler circlers[MAX_CIRCLERS];
    uint8_t circlerCount;
    uint32_t nextSplitTime;
    bool splitting;          // true = splitting phase, false = merging phase

    // Touch mode data
    static const uint8_t MAX_RIPPLES = 12;  // Max active ripples
    struct TouchRipple {
        uint16_t centerPos;     // Ring position of globe
        float radius;           // Current radius in pixels
        uint8_t hue;            // Color hue
        uint8_t brightness;     // Starting brightness
        bool active;
    };
    TouchRipple ripples[MAX_RIPPLES];
    uint8_t nextRippleHue[4];   // Next hue for each globe

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

    // Initialize mitosis mode with a single circler
    void initMitosis() {
        // Clear all circlers
        for (uint8_t i = 0; i < MAX_CIRCLERS; i++) {
            circlers[i].active = false;
        }

        // Start with one circler
        circlers[0].active = true;
        circlers[0].position = 0;
        circlers[0].speed = 0.8f + random8() * 0.01f;  // 0.8 to ~3.3 pixels/update
        circlers[0].hue = random8();
        circlers[0].hueSpeed = random8(1, 4);
        circlerCount = 1;

        // Schedule first split
        nextSplitTime = millis() + random16(2000, 5000);
        splitting = true;
    }

    // Mitosis: splitting and merging circlers
    void updateMitosis() {
        const uint8_t eyeSize = 5;
        const uint8_t fadeAmount = 40;
        const uint8_t mergeChance = 60;  // % chance to merge on overlap

        CRGB* ring = leds.getRingLeds();
        uint32_t now = millis();

        // Fade all pixels
        for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
            ring[i].fadeToBlackBy(fadeAmount);
        }

        // Update and draw each active circler
        for (uint8_t c = 0; c < MAX_CIRCLERS; c++) {
            if (!circlers[c].active) continue;

            // Update position (wrap around)
            circlers[c].position += circlers[c].speed;
            if (circlers[c].position >= RING_NUM_PIXELS) {
                circlers[c].position -= RING_NUM_PIXELS;
            } else if (circlers[c].position < 0) {
                circlers[c].position += RING_NUM_PIXELS;
            }

            // Update hue
            circlers[c].hue += circlers[c].hueSpeed;

            // Draw the circler (cylon-like eye)
            int16_t centerPos = (int16_t)circlers[c].position;
            for (int8_t i = -eyeSize/2; i <= eyeSize/2; i++) {
                uint16_t pos = (centerPos + i + RING_NUM_PIXELS) % RING_NUM_PIXELS;
                uint8_t brightness = 255 - abs(i) * 45;
                // Blend with existing color for overlapping circlers
                CRGB newColor = CHSV(circlers[c].hue, 255, brightness);
                ring[pos] = blend(ring[pos], newColor, 180);
            }
        }

        // Handle splitting phase
        if (splitting && circlerCount < MAX_CIRCLERS) {
            if (now >= nextSplitTime) {
                splitRandomCircler();
                // Schedule next split with increasing delay as count grows
                uint32_t baseDelay = 2000 + circlerCount * 500;
                nextSplitTime = now + random16(baseDelay, baseDelay + 3000);

                // Switch to merging when we have enough
                if (circlerCount >= MAX_CIRCLERS) {
                    splitting = false;
                }
            }
        }

        // Handle merging phase (check for overlaps)
        if (!splitting && circlerCount > 1) {
            checkAndMergeCirclers(mergeChance);

            // Switch back to splitting when down to one
            if (circlerCount <= 1) {
                splitting = true;
                nextSplitTime = now + random16(2000, 4000);
            }
        }

        // Also allow some merging during split phase if crowded
        if (splitting && circlerCount > 8) {
            checkAndMergeCirclers(mergeChance / 2);
        }
    }

    // Split a random active circler into two
    void splitRandomCircler() {
        // Find a random active circler to split
        uint8_t activeIndices[MAX_CIRCLERS];
        uint8_t activeCount = 0;
        for (uint8_t i = 0; i < MAX_CIRCLERS; i++) {
            if (circlers[i].active) {
                activeIndices[activeCount++] = i;
            }
        }
        if (activeCount == 0) return;

        uint8_t parentIdx = activeIndices[random8(activeCount)];

        // Find an inactive slot for the new circler
        int8_t newIdx = -1;
        for (uint8_t i = 0; i < MAX_CIRCLERS; i++) {
            if (!circlers[i].active) {
                newIdx = i;
                break;
            }
        }
        if (newIdx < 0) return;  // No room

        Circler& parent = circlers[parentIdx];
        Circler& child = circlers[newIdx];

        // Create child at same position
        child.active = true;
        child.position = parent.position;

        // Random speeds, opposite directions, not always same magnitude
        float baseSpeed = 0.5f + random8() * 0.015f;
        float speedVariation = random8() * 0.01f;

        if (random8() > 127) {
            parent.speed = baseSpeed + speedVariation;
            child.speed = -(baseSpeed - speedVariation * 0.5f);
        } else {
            parent.speed = -(baseSpeed + speedVariation);
            child.speed = baseSpeed - speedVariation * 0.5f;
        }

        // Independent colors
        child.hue = parent.hue + 30 + random8(60);
        child.hueSpeed = random8(1, 4);
        parent.hueSpeed = random8(1, 4);

        circlerCount++;
    }

    // Check for overlapping circlers and potentially merge them
    void checkAndMergeCirclers(uint8_t chance) {
        const float overlapThreshold = 6.0f;  // pixels apart to count as overlap

        for (uint8_t i = 0; i < MAX_CIRCLERS; i++) {
            if (!circlers[i].active) continue;

            for (uint8_t j = i + 1; j < MAX_CIRCLERS; j++) {
                if (!circlers[j].active) continue;

                // Calculate distance (accounting for wrap-around)
                float dist = abs(circlers[i].position - circlers[j].position);
                if (dist > RING_NUM_PIXELS / 2) {
                    dist = RING_NUM_PIXELS - dist;
                }

                if (dist < overlapThreshold) {
                    // Chance to merge
                    if (random8(100) < chance) {
                        // Merge j into i
                        circlers[i].speed = (circlers[i].speed + circlers[j].speed) / 2.0f;

                        // If resulting speed is too slow, give it a nudge
                        if (abs(circlers[i].speed) < 0.3f) {
                            circlers[i].speed = (random8() > 127) ? 0.5f : -0.5f;
                        }

                        // Keep blended hue
                        circlers[i].hue = (circlers[i].hue + circlers[j].hue) / 2;

                        // Deactivate j
                        circlers[j].active = false;
                        circlerCount--;

                        return;  // One merge per update to avoid cascading
                    }
                }
            }
        }
    }

    // Initialize touch mode
    void initTouch() {
        // Clear all ripples
        for (uint8_t i = 0; i < MAX_RIPPLES; i++) {
            ripples[i].active = false;
        }
        // Initialize starting hues for each globe
        for (uint8_t i = 0; i < 4; i++) {
            nextRippleHue[i] = random8();
        }
    }

    // Get ring position for a globe (WS2812) index
    uint16_t getGlobeRingPosition(uint8_t globeIdx) {
        uint8_t offset = leds.getWS2812Offset();
        uint16_t spacing = (globeIdx * RING_NUM_PIXELS + WS2812_NUM_LEDS / 2) / WS2812_NUM_LEDS;
        return (offset + spacing) % RING_NUM_PIXELS;
    }

    // Touch-reactive mode: ripples emanate from globe positions based on touch
    void updateTouch() {
        if (!touchHandler) return;

        CRGB* ring = leds.getRingLeds();
        const uint8_t fadeAmount = 25;
        const float rippleSpeed = 2.5f;
        const uint8_t rippleWidth = 8;

        // Fade all pixels
        for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
            ring[i].fadeToBlackBy(fadeAmount);
        }

        // Check each touch sensor and create ripples
        for (uint8_t t = 0; t < TOUCH_NUM_SENSORS; t++) {
            uint16_t rawValue = touchHandler->getRawValue(t);
            uint16_t baseline = touchHandler->getBaseline(t);
            uint16_t threshold = touchHandler->getThreshold(t);

            // Calculate touch intensity (0-255)
            // Lower raw value = more touch (capacitance pulls value down)
            uint8_t intensity = 0;
            if (rawValue < baseline) {
                // Map from threshold..0 to 0..255
                int32_t touchAmount = baseline - rawValue;
                int32_t maxTouch = baseline - threshold;
                if (maxTouch > 0) {
                    intensity = constrain(touchAmount * 255 / maxTouch, 0, 255);
                }
            }

            // Get ring position for this globe
            uint16_t globePos = getGlobeRingPosition(t);

            // Draw glow at globe position based on touch intensity
            if (intensity > 20) {
                uint8_t glowRadius = 3 + (intensity >> 5);  // 3-10 pixels
                for (int8_t offset = -glowRadius; offset <= glowRadius; offset++) {
                    uint16_t pos = (globePos + offset + RING_NUM_PIXELS) % RING_NUM_PIXELS;
                    uint8_t dist = abs(offset);
                    uint8_t brightness = intensity - (dist * intensity / glowRadius);
                    CRGB glowColor = CHSV(nextRippleHue[t], 255, brightness);
                    ring[pos] = blend(ring[pos], glowColor, 200);
                }

                // Spawn new ripple when touch exceeds threshold
                if (intensity > 100 && !touchHandler->isTouched(t)) {
                    // Only spawn if not already touching (on press)
                } else if (touchHandler->isTouched(t)) {
                    // While touching, occasionally spawn ripples
                    if (random8() < 15) {  // ~6% chance per frame
                        spawnRipple(globePos, nextRippleHue[t], intensity);
                        nextRippleHue[t] += random8(20, 50);  // Shift color
                    }
                }
            }
        }

        // Update and draw ripples
        for (uint8_t r = 0; r < MAX_RIPPLES; r++) {
            if (!ripples[r].active) continue;

            // Expand ripple
            ripples[r].radius += rippleSpeed;

            // Fade brightness as it expands
            if (ripples[r].brightness > 3) {
                ripples[r].brightness -= 3;
            } else {
                ripples[r].active = false;
                continue;
            }

            // Deactivate if too large
            if (ripples[r].radius > RING_NUM_PIXELS / 3) {
                ripples[r].active = false;
                continue;
            }

            // Draw ripple ring (expanding outward in both directions)
            int16_t innerRadius = (int16_t)(ripples[r].radius - rippleWidth / 2);
            int16_t outerRadius = (int16_t)(ripples[r].radius + rippleWidth / 2);

            for (int16_t dist = max((int16_t)0, innerRadius); dist <= outerRadius; dist++) {
                // Distance from ideal radius determines brightness
                float radiusDist = abs(dist - ripples[r].radius);
                uint8_t brightness = ripples[r].brightness * (1.0f - radiusDist / (rippleWidth / 2.0f));
                if (brightness < 5) continue;

                CRGB rippleColor = CHSV(ripples[r].hue, 255, brightness);

                // Draw at +dist and -dist from center
                uint16_t posPlus = (ripples[r].centerPos + dist) % RING_NUM_PIXELS;
                uint16_t posMinus = (ripples[r].centerPos - dist + RING_NUM_PIXELS) % RING_NUM_PIXELS;

                ring[posPlus] = blend(ring[posPlus], rippleColor, 180);
                if (dist > 0) {  // Don't double-draw center
                    ring[posMinus] = blend(ring[posMinus], rippleColor, 180);
                }
            }
        }
    }

    // Spawn a new ripple at the given position
    void spawnRipple(uint16_t centerPos, uint8_t hue, uint8_t brightness) {
        // Find inactive ripple slot
        for (uint8_t i = 0; i < MAX_RIPPLES; i++) {
            if (!ripples[i].active) {
                ripples[i].active = true;
                ripples[i].centerPos = centerPos;
                ripples[i].radius = 0;
                ripples[i].hue = hue;
                ripples[i].brightness = brightness;
                return;
            }
        }
        // If no slot, overwrite oldest (first in array)
        ripples[0].active = true;
        ripples[0].centerPos = centerPos;
        ripples[0].radius = 0;
        ripples[0].hue = hue;
        ripples[0].brightness = brightness;
    }
};

#endif // LOCAL_MODES_H
