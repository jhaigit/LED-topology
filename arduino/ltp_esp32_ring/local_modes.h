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
#include <time.h>
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
        , cycleTimeMs(LOCAL_MODE_CYCLE_TIME)
        , touchOverlayEnabled(true)
    {
        initTouch();  // Always init touch ripple data
    }

    // Set touch handler for touch-reactive modes
    void setTouchHandler(TouchHandler* handler) {
        touchHandler = handler;
    }

    // Set cycle time (seconds per mode when cycling)
    void setCycleTime(uint16_t seconds) {
        cycleTimeMs = (uint32_t)seconds * 1000;
    }

    // Enable/disable touch overlay (ripple effects on top of all modes)
    void setTouchOverlayEnabled(bool enabled) {
        touchOverlayEnabled = enabled;
    }

    bool isTouchOverlayEnabled() const {
        return touchOverlayEnabled;
    }

    // Trigger a ripple at a touch sensor position (called from touch callback)
    void triggerTouchRipple(uint8_t touchIdx) {
        if (touchIdx >= TOUCH_NUM_SENSORS) return;
        uint16_t globePos = getGlobeRingPosition(touchIdx);
        spawnRipple(globePos, nextRippleHue[touchIdx], 200);
        nextRippleHue[touchIdx] += random8(30, 60);
    }

    // Start or switch to a local mode
    void start(uint8_t mode) {
        currentMode = mode;
        position = 0;
        hue = 0;
        lastUpdate = millis();
        modeStartTime = millis();

        // Mode 0 (blank) now means "touch overlay only" - still active for overlay
        if (mode == LOCAL_MODE_BLANK) {
            active = true;  // Keep active for touch overlay
            displayMode = LOCAL_MODE_BLANK;
        } else {
            active = true;
            if (mode == LOCAL_MODE_CYCLE) {
                displayMode = LOCAL_MODE_CYLON;
            } else {
                displayMode = mode;
            }

            // Initialize chase mode
            if (displayMode == LOCAL_MODE_CHASE) {
                chaseCount = random8(1, 6);  // 1 to 5 chasers
            }

            // Initialize mitosis mode
            if (displayMode == LOCAL_MODE_MITOSIS) {
                initMitosis();
            }

            // Initialize clock mode
            if (displayMode == LOCAL_MODE_CLOCK) {
                lastClockSec = 255;  // Force first-second detection
                secChangeMillis = millis();
            }

            // Initialize sin wave modes
            if (displayMode == LOCAL_MODE_SINWAVE ||
                displayMode == LOCAL_MODE_SINWAVE_RGB ||
                displayMode == LOCAL_MODE_SINWAVE_RGB2) {
                initSinWave(displayMode == LOCAL_MODE_SINWAVE_RGB2);
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
    // Skips touch mode — it can only be entered via the control variable
    void nextMode() {
        uint8_t next;
        if (currentMode == LOCAL_MODE_CYCLE || currentMode == LOCAL_MODE_BLANK) {
            next = LOCAL_MODE_CYLON;
        } else {
            next = currentMode + 1;
            // Skip touch and clock modes (control-variable only)
            while (next == LOCAL_MODE_TOUCH || next == LOCAL_MODE_CLOCK) next++;
            if (next >= LOCAL_MODE_COUNT) {
                next = LOCAL_MODE_CYLON;
            }
        }
        start(next);
    }

    // Advance to next pattern while staying in cycle mode
    // Returns true if advanced, false if not in cycle mode
    bool advanceCyclePattern() {
        if (currentMode != LOCAL_MODE_CYCLE) return false;

        // Advance display mode
        displayMode++;
        // Skip touch and clock modes
        while (displayMode == LOCAL_MODE_TOUCH || displayMode == LOCAL_MODE_CLOCK) {
            displayMode++;
        }
        if (displayMode >= LOCAL_MODE_COUNT) {
            displayMode = LOCAL_MODE_CYLON;
        }

        // Reset timer so new pattern gets full cycle time
        modeStartTime = millis();
        position = 0;
        leds.clear();

        // Initialize modes that need it
        if (displayMode == LOCAL_MODE_MITOSIS) {
            initMitosis();
        } else if (displayMode == LOCAL_MODE_SINWAVE ||
                   displayMode == LOCAL_MODE_SINWAVE_RGB ||
                   displayMode == LOCAL_MODE_SINWAVE_RGB2) {
            initSinWave(displayMode == LOCAL_MODE_SINWAVE_RGB2);
        }

        return true;
    }

    // Update animation (call from loop)
    void update() {
        if (!active) return;

        uint32_t now = millis();

        // Get update interval for current mode
        // Touch overlay needs fast updates, so use minimum of mode interval and 20ms
        uint32_t interval;
        switch (displayMode) {
            case LOCAL_MODE_BLANK:      interval = 20; break;  // Touch overlay only
            case LOCAL_MODE_CYLON:      interval = 15; break;
            case LOCAL_MODE_RAINBOW:    interval = 20; break;
            case LOCAL_MODE_FIRE:       interval = 30; break;
            case LOCAL_MODE_SPARKLE:    interval = 30; break;
            case LOCAL_MODE_CHASE:      interval = 40; break;
            case LOCAL_MODE_MITOSIS:    interval = 25; break;
            case LOCAL_MODE_TOUCH:      interval = 20; break;  // Legacy dedicated touch mode
            case LOCAL_MODE_SINWAVE:    interval = 20; break;
            case LOCAL_MODE_SINWAVE_RGB:  interval = 20; break;
            case LOCAL_MODE_SINWAVE_RGB2: interval = 20; break;
            case LOCAL_MODE_CLOCK:        interval = 50; break;  // 20 FPS for smooth overlay
            default: interval = 50; break;
        }

        // If touch overlay is enabled, use faster interval for smooth ripples
        if (touchOverlayEnabled && interval > 20) {
            interval = 20;
        }

        if (now - lastUpdate < interval) return;
        lastUpdate = now;

        // Handle cycle mode timing
        if (currentMode == LOCAL_MODE_CYCLE) {
            if (now - modeStartTime >= cycleTimeMs) {
                modeStartTime = now;
                displayMode++;
                // Skip touch and clock modes in cycle (touch is now overlay, clock is separate)
                while (displayMode == LOCAL_MODE_TOUCH || displayMode == LOCAL_MODE_CLOCK) {
                    displayMode++;
                }
                if (displayMode >= LOCAL_MODE_COUNT) {
                    displayMode = LOCAL_MODE_CYLON;
                }
                position = 0;
                leds.clear();

                // Initialize modes that need it
                if (displayMode == LOCAL_MODE_MITOSIS) {
                    initMitosis();
                } else if (displayMode == LOCAL_MODE_SINWAVE ||
                           displayMode == LOCAL_MODE_SINWAVE_RGB ||
                           displayMode == LOCAL_MODE_SINWAVE_RGB2) {
                    initSinWave(displayMode == LOCAL_MODE_SINWAVE_RGB2);
                }
            }
        }

        // Run the current animation (or just clear for blank/touch-overlay-only mode)
        switch (displayMode) {
            case LOCAL_MODE_BLANK:
                // Touch overlay only - fade to black slowly
                {
                    CRGB* ring = leds.getRingLeds();
                    for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
                        ring[i].fadeToBlackBy(25);
                    }
                }
                break;
            case LOCAL_MODE_CYLON:      updateCylon(); break;
            case LOCAL_MODE_RAINBOW:    updateRainbow(); break;
            case LOCAL_MODE_FIRE:       updateFire(); break;
            case LOCAL_MODE_SPARKLE:    updateSparkle(); break;
            case LOCAL_MODE_CHASE:      updateChase(); break;
            case LOCAL_MODE_MITOSIS:    updateMitosis(); break;
            case LOCAL_MODE_TOUCH:      updateTouch(); break;  // Legacy dedicated mode
            case LOCAL_MODE_SINWAVE:    updateSinWave(); break;
            case LOCAL_MODE_SINWAVE_RGB:  updateSinWaveRGB(false); break;
            case LOCAL_MODE_SINWAVE_RGB2: updateSinWaveRGB(true); break;
            case LOCAL_MODE_CLOCK:        updateClock(); break;
        }

        // Draw touch overlay on top of any animation (except dedicated touch mode which handles its own)
        if (touchOverlayEnabled && displayMode != LOCAL_MODE_TOUCH) {
            updateTouchOverlay();
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
    uint32_t cycleTimeMs;   // Milliseconds per mode when cycling
    bool touchOverlayEnabled;  // Draw touch ripples on top of all animations

    // Fire effect heat map
    uint8_t heat[RING_NUM_PIXELS];

    // Chase mode data
    uint8_t chaseCount;  // Number of chasers (1-5), randomized on mode start

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

    // Clock mode data
    uint8_t lastClockSec;       // Track second changes
    uint32_t secChangeMillis;   // millis() when second last changed

    // Sin wave mode data
    float sinPhaseOffset;       // Rotation phase for the wave pattern
    float sinTimePhase;         // Phase for overall brightness throb
    float sinColorOffsets[3];   // Phase offsets for R, G, B channels
    float sinColorSpeeds[3];    // Rotation speeds for each color (mode 10)

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

    // Fire: bidirectional fire effect (flames rise from bottom toward top)
    // Bottom is between globes 0 and 3, top is between globes 1 and 2
    void updateFire() {
        const uint8_t cooling = 55;
        const uint8_t sparking = 120;

        CRGB* ring = leds.getRingLeds();

        uint16_t bottom = getFireBottomPosition();
        uint16_t top = (bottom + RING_NUM_PIXELS / 2) % RING_NUM_PIXELS;
        uint16_t halfLen = RING_NUM_PIXELS / 2;

        // Cool down every cell
        for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
            uint8_t cooldown = random8(0, ((cooling * 10) / halfLen) + 2);
            heat[i] = (heat[i] > cooldown) ? heat[i] - cooldown : 0;
        }

        // Heat rises from bottom toward top along both sides of the ring
        // Side 1: bottom -> top going clockwise
        for (int16_t dist = halfLen - 1; dist >= 2; dist--) {
            uint16_t pos = (bottom + dist) % RING_NUM_PIXELS;
            uint16_t src1 = (bottom + dist - 1) % RING_NUM_PIXELS;
            uint16_t src2 = (bottom + dist - 2) % RING_NUM_PIXELS;
            heat[pos] = (heat[src1] + heat[src2] + heat[src2]) / 3;
        }
        // Side 2: bottom -> top going counter-clockwise
        for (int16_t dist = halfLen - 1; dist >= 2; dist--) {
            uint16_t pos = (bottom - dist + RING_NUM_PIXELS) % RING_NUM_PIXELS;
            uint16_t src1 = (bottom - dist + 1 + RING_NUM_PIXELS) % RING_NUM_PIXELS;
            uint16_t src2 = (bottom - dist + 2 + RING_NUM_PIXELS) % RING_NUM_PIXELS;
            heat[pos] = (heat[src1] + heat[src2] + heat[src2]) / 3;
        }

        // Blend heat at the top where both sides meet
        uint16_t topM1 = (top - 1 + RING_NUM_PIXELS) % RING_NUM_PIXELS;
        uint16_t topP1 = (top + 1) % RING_NUM_PIXELS;
        heat[top] = (heat[topM1] + heat[topP1]) / 2;

        // Blend heat at the bottom for smooth transitions
        uint16_t botM1 = (bottom - 1 + RING_NUM_PIXELS) % RING_NUM_PIXELS;
        uint16_t botP1 = (bottom + 1) % RING_NUM_PIXELS;
        uint8_t avgBot = (heat[botM1] + heat[botP1]) / 2;
        heat[bottom] = (heat[bottom] + avgBot) / 2;

        // Randomly ignite sparks at the bottom
        if (random8() < sparking) {
            int8_t sparkOffset = random8(10) - 5;  // -5 to +4 pixels from bottom
            uint16_t pos = (bottom + sparkOffset + RING_NUM_PIXELS) % RING_NUM_PIXELS;
            heat[pos] = qadd8(heat[pos], random8(160, 255));
        }

        // Map heat to colors
        for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
            ring[i] = heatToColor(heat[i]);
        }
    }

    // Sparkle: random sparkles around the ring with slow rotation
    void updateSparkle() {
        const uint8_t fadeAmount = 20;

        CRGB* ring = leds.getRingLeds();

        // Slow rotation: shift all pixels by 1 every 4 frames
        if (position % 4 == 0) {
            CRGB last = ring[RING_NUM_PIXELS - 1];
            for (int i = RING_NUM_PIXELS - 1; i > 0; i--) {
                ring[i] = ring[i - 1];
            }
            ring[0] = last;
        }
        position++;

        // Fade all pixels
        for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
            ring[i].fadeToBlackBy(fadeAmount);
        }

        // Add ~1.8 sparkles per frame (probabilistic)
        for (uint8_t i = 0; i < 2; i++) {
            if (i == 0 || random8() < 204) {  // 1st always, 2nd ~80%
                uint16_t pos = random16(RING_NUM_PIXELS);
                ring[pos] = CHSV(random8(), 200, 255);
            }
        }
    }

    // Chase: color chase that wraps seamlessly around the ring
    void updateChase() {
        const uint8_t chaseLength = 8;

        CRGB* ring = leds.getRingLeds();

        // Clear ring
        fill_solid(ring, RING_NUM_PIXELS, CRGB::Black);

        // Draw multiple chasers evenly spaced around the ring
        uint16_t spacing = RING_NUM_PIXELS / chaseCount;
        for (uint8_t c = 0; c < chaseCount; c++) {
            uint16_t chaserBase = (position + c * spacing) % RING_NUM_PIXELS;
            uint8_t chaserHue = hue + c * (256 / chaseCount);  // Different hue per chaser

            // Draw chase pattern with gradient tail
            for (uint8_t i = 0; i < chaseLength; i++) {
                uint16_t pos = (chaserBase + i) % RING_NUM_PIXELS;
                uint8_t brightness = 255 - i * 30;
                // Blend if another chaser already drew here
                CRGB newColor = CHSV(chaserHue, 255, brightness);
                ring[pos] = blend(ring[pos], newColor, 180);
            }
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

    // Get the "bottom" of the ring - midpoint between globe 0 and globe 3
    // This is where fire sparks should originate
    uint16_t getFireBottomPosition() {
        uint16_t globe0 = getGlobeRingPosition(0);
        uint16_t globe3 = getGlobeRingPosition(3);
        // Midpoint going from globe 3 to globe 0 (the short way, ~50 pixels)
        // Globe 3 is about 25 pixels before globe 0 (wrapping around)
        // So midpoint is about 12-13 pixels before globe 0
        uint16_t gap = (globe0 - globe3 + RING_NUM_PIXELS) % RING_NUM_PIXELS;
        return (globe3 + gap / 2) % RING_NUM_PIXELS;
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

    // Update and draw touch overlay (ripples only, blended on top of existing animation)
    // Called after main animation to overlay ripple effects
    void updateTouchOverlay() {
        CRGB* ring = leds.getRingLeds();
        const float rippleSpeed = 2.5f;
        const uint8_t rippleWidth = 8;

        // Spawn continuous ripples while sensors are held
        if (touchHandler) {
            uint8_t touchedCount = 0;
            for (uint8_t t = 0; t < TOUCH_NUM_SENSORS; t++) {
                if (touchHandler->isTouched(t)) {
                    touchedCount++;
                    // ~6% chance per frame to spawn a ripple while held
                    if (random8() < 15) {
                        uint16_t globePos = getGlobeRingPosition(t);
                        spawnRipple(globePos, nextRippleHue[t], 180);
                        nextRippleHue[t] += random8(20, 50);
                    }
                }
            }
            // Debug: show if touch detection drops out
            static uint8_t lastTouchedCount = 0;
            if (touchedCount != lastTouchedCount) {
                dualOut.printf("TouchOverlay: %d sensors touched\r\n", touchedCount);
                lastTouchedCount = touchedCount;
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

    // Initialize sin wave modes
    void initSinWave(bool differentSpeeds) {
        sinPhaseOffset = 0;
        sinTimePhase = 0;

        // Random starting offsets for each color channel
        for (uint8_t i = 0; i < 3; i++) {
            sinColorOffsets[i] = random8() * (2.0f * PI / 256.0f);
        }

        // Set rotation speeds
        if (differentSpeeds) {
            // Different speeds for each color (mode 10)
            sinColorSpeeds[0] = 0.02f + random8() * 0.0002f;  // R
            sinColorSpeeds[1] = 0.025f + random8() * 0.0002f; // G
            sinColorSpeeds[2] = 0.03f + random8() * 0.0002f;  // B
        } else {
            // Same speed for all colors (mode 9)
            float speed = 0.025f;
            sinColorSpeeds[0] = speed;
            sinColorSpeeds[1] = speed;
            sinColorSpeeds[2] = speed;
        }
    }

    // Sin wave mode 8: Red only with 8 waves, rotating and throbbing
    void updateSinWave() {
        CRGB* ring = leds.getRingLeds();

        // Update phases (wrap to prevent float precision loss)
        sinPhaseOffset += 0.02f;  // Wave rotation speed
        sinTimePhase += 0.03f;    // Throb speed
        if (sinPhaseOffset > 2.0f * PI) sinPhaseOffset -= 2.0f * PI;
        if (sinTimePhase > 2.0f * PI) sinTimePhase -= 2.0f * PI;

        // Calculate overall brightness multiplier (throb effect)
        // Goes from near-zero to full brightness
        float throb = 0.5f + 0.5f * sin(sinTimePhase);
        uint8_t maxBrightness = (uint8_t)(throb * throb * 255);  // 0-255, squared for dramatic fade

        for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
            // Calculate angle for this pixel (0 to 2*PI around the ring)
            float angle = (float)i * 2.0f * PI / RING_NUM_PIXELS;

            // Apply 8x frequency and add rotation offset
            float sinAngle = 8.0f * angle + sinPhaseOffset;

            // Calculate brightness using abs(sin())
            float sinVal = fabs(sin(sinAngle));
            uint8_t brightness = (uint8_t)(sinVal * maxBrightness);

            ring[i] = CRGB(brightness, 0, 0);
        }
    }

    // Sin wave modes 9 & 10: RGB with separate offsets
    void updateSinWaveRGB(bool differentSpeeds) {
        CRGB* ring = leds.getRingLeds();

        // Update phases (wrap to prevent float precision loss)
        sinPhaseOffset += 0.015f;  // Base rotation (for visual interest)
        sinTimePhase += 0.02f;     // Subtle overall modulation
        if (sinPhaseOffset > 2.0f * PI) sinPhaseOffset -= 2.0f * PI;
        if (sinTimePhase > 2.0f * PI) sinTimePhase -= 2.0f * PI;

        // Update color offsets
        for (uint8_t c = 0; c < 3; c++) {
            sinColorOffsets[c] += sinColorSpeeds[c];
            if (sinColorOffsets[c] > 2.0f * PI) {
                sinColorOffsets[c] -= 2.0f * PI;
            }
        }

        // Overall brightness variation - goes low to high
        float throbRaw = 0.5f + 0.5f * sin(sinTimePhase);
        float throb = throbRaw * throbRaw;  // Square for more dramatic low end (0-1)

        for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
            // Calculate base angle for this pixel
            float angle = (float)i * 2.0f * PI / RING_NUM_PIXELS;

            // Calculate each color channel with its own offset
            uint8_t r, g, b;

            float sinR = fabs(sin(8.0f * angle + sinColorOffsets[0]));
            float sinG = fabs(sin(8.0f * angle + sinColorOffsets[1]));
            float sinB = fabs(sin(8.0f * angle + sinColorOffsets[2]));

            r = (uint8_t)(sinR * 255.0f * throb);
            g = (uint8_t)(sinG * 255.0f * throb);
            b = (uint8_t)(sinB * 255.0f * throb);

            ring[i] = CRGB(r, g, b);
        }
    }

    // Get the 12-o'clock pixel position (halfway between globes 1 and 2)
    uint16_t getClock12Position() {
        uint8_t offset = leds.getWS2812Offset();
        // Globe 1 pos: offset + round(1 * 202 / 4) = offset + 50 (with rounding)
        // Globe 2 pos: offset + round(2 * 202 / 4) = offset + 101
        // Midpoint: offset + 75 (rounding of (50+101)/2)
        uint16_t globe1 = (offset + (1 * RING_NUM_PIXELS + WS2812_NUM_LEDS / 2) / WS2812_NUM_LEDS) % RING_NUM_PIXELS;
        uint16_t globe2 = (offset + (2 * RING_NUM_PIXELS + WS2812_NUM_LEDS / 2) / WS2812_NUM_LEDS) % RING_NUM_PIXELS;
        // Handle wrap-around for midpoint
        if (globe2 < globe1) globe2 += RING_NUM_PIXELS;
        return ((globe1 + globe2) / 2) % RING_NUM_PIXELS;
    }

    // Draw a clock hand centered at a pixel position with soft Gaussian-like falloff
    void drawClockHand(CRGB* ring, uint16_t centerPixel, uint8_t handSize, CRGB color) {
        int8_t halfSize = handSize / 2;
        for (int8_t i = -halfSize; i <= halfSize; i++) {
            uint16_t pos = (centerPixel + i + RING_NUM_PIXELS) % RING_NUM_PIXELS;
            // Gaussian-like falloff: exp(-k * x^2) approximated with quadratic
            float normalized = (float)abs(i) / (float)(halfSize + 1);  // 0 at center, ~1 at edge
            float falloff = 1.0f - normalized * normalized;            // Quadratic: soft edges
            uint8_t brightness = (uint8_t)(falloff * falloff * 255);   // Square again for extra softness
            CRGB pixelColor = color;
            pixelColor.nscale8(brightness);
            ring[pos] = blend(ring[pos], pixelColor, 200);
        }
    }

    // Analog clock mode: hour/minute/second hands on the ring
    void updateClock() {
        struct tm timeinfo;
        if (!getLocalTime(&timeinfo, 0)) {
            // No time yet — show dim rotating dot as "waiting for NTP"
            CRGB* ring = leds.getRingLeds();
            for (uint16_t i = 0; i < RING_NUM_PIXELS; i++) {
                ring[i].fadeToBlackBy(40);
            }
            uint16_t pos = (millis() / 20) % RING_NUM_PIXELS;
            ring[pos] = CRGB(40, 40, 40);
            return;
        }

        CRGB* ring = leds.getRingLeds();
        fill_solid(ring, RING_NUM_PIXELS, CRGB::Black);

        uint16_t top = getClock12Position();

        // Track second transitions for smooth sub-second interpolation
        if (timeinfo.tm_sec != lastClockSec) {
            lastClockSec = timeinfo.tm_sec;
            secChangeMillis = millis();
        }
        // Sub-second offset: millis since the second changed, clamped to 999
        float subSec = min((uint32_t)999, millis() - secChangeMillis) / 1000.0f;

        // Calculate hand positions (subtract: strip runs counter-clockwise)
        float secFrac = (timeinfo.tm_sec + subSec) / 60.0f;
        float minFrac = (timeinfo.tm_min + timeinfo.tm_sec / 60.0f) / 60.0f;
        float hourFrac = ((timeinfo.tm_hour % 12) + timeinfo.tm_min / 60.0f) / 12.0f;

        // Subtract fraction from 1.0 to reverse direction, then map to pixels
        uint16_t secPixel  = (top + (uint16_t)((1.0f - secFrac)  * RING_NUM_PIXELS)) % RING_NUM_PIXELS;
        uint16_t minPixel  = (top + (uint16_t)((1.0f - minFrac)  * RING_NUM_PIXELS)) % RING_NUM_PIXELS;
        uint16_t hourPixel = (top + (uint16_t)((1.0f - hourFrac) * RING_NUM_PIXELS)) % RING_NUM_PIXELS;

        // Draw hands (hour first so minute and second draw on top)
        drawClockHand(ring, hourPixel, CLOCK_HAND_HOUR_SIZE, CRGB(0, 200, 0));   // Green hour
        drawClockHand(ring, minPixel,  CLOCK_HAND_MIN_SIZE,  CRGB(200, 0, 0));   // Red minute
        drawClockHand(ring, secPixel,  CLOCK_HAND_SEC_SIZE,  CRGB(0, 80, 255));  // Blue second

        // Draw subtle tick marks at 12, 3, 6, 9 positions
        for (uint8_t h = 0; h < 12; h++) {
            uint16_t tickPos = (top + RING_NUM_PIXELS - (h * RING_NUM_PIXELS) / 12) % RING_NUM_PIXELS;
            CRGB tickColor = (h % 3 == 0) ? CRGB(30, 30, 30) : CRGB(10, 10, 10);
            ring[tickPos] = blend(ring[tickPos], tickColor, 128);
        }
    }
};

#endif // LOCAL_MODES_H
