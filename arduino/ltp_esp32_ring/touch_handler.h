/**
 * ESP32 Ring Controller - Touch Handler
 *
 * Manages 4 capacitive touch sensors with auto-calibration,
 * debouncing, and callback support for touch events.
 * Supports long-press detection and multi-touch gestures.
 */

#ifndef TOUCH_HANDLER_H
#define TOUCH_HANDLER_H

#include <Arduino.h>
#include "config.h"

// Touch callback function types
typedef void (*TouchCallback)(uint8_t touchIdx);
typedef void (*TouchStateCallback)(uint8_t touchIdx, bool pressed);
typedef void (*LongHoldCallback)(uint8_t numSensors);  // Called when long hold detected

// Long hold configuration
#define TOUCH_LONG_HOLD_MS      1500    // Time to trigger long hold (1.5 seconds)
#define TOUCH_MULTI_HOLD_WINDOW 200     // Max time diff between sensors to count as simultaneous

class TouchHandler {
public:
    TouchHandler()
        : onTouchCallback(nullptr)
        , onTouchStateCallback(nullptr)
        , onLongHoldCallback(nullptr)
        , calibrated(false)
        , sensitivity(TOUCH_THRESHOLD_RATIO)
        , longHoldTriggered(false)
    {
        touchPins[0] = TOUCH_PIN_0;
        touchPins[1] = TOUCH_PIN_1;
        touchPins[2] = TOUCH_PIN_2;
        touchPins[3] = TOUCH_PIN_3;

        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            baselines[i] = 0;
            thresholds[i] = 0;
            lastState[i] = false;
            lastTouchTime[i] = 0;
            pressStartTime[i] = 0;
        }
    }

    void begin() {
        // Small delay to let touch hardware stabilize
        delay(100);
        calibrate();
    }

    // Calibrate touch sensors (sample baseline values)
    void calibrate() {
        dualOut.println("Calibrating touch sensors...");

        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            uint32_t sum = 0;

            // Take multiple samples for averaging
            for (uint8_t s = 0; s < TOUCH_CALIBRATION_SAMPLES; s++) {
                sum += touchRead(touchPins[i]);
                delay(10);
            }

            baselines[i] = sum / TOUCH_CALIBRATION_SAMPLES;
            thresholds[i] = (uint16_t)(baselines[i] * sensitivity);

            dualOut.printf("  Touch %d: baseline=%d, threshold=%d\r\n",
                          i, baselines[i], thresholds[i]);
        }

        calibrated = true;
        dualOut.println("Touch calibration complete.");
    }

    // Update touch states (call from loop)
    void update() {
        if (!calibrated) return;

        uint32_t now = millis();

        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            uint16_t value = touchRead(touchPins[i]);
            bool touched = value < thresholds[i];

            // Debounce check
            if (touched != lastState[i]) {
                if (now - lastTouchTime[i] >= TOUCH_DEBOUNCE_MS) {
                    lastState[i] = touched;
                    lastTouchTime[i] = now;

                    if (touched) {
                        // Press started - record time
                        pressStartTime[i] = now;
                    } else {
                        // Release - clear press time
                        pressStartTime[i] = 0;
                    }

                    // Trigger state callback for both press and release
                    if (onTouchStateCallback) {
                        onTouchStateCallback(i, touched);
                    }

                    // Trigger legacy callback on touch only (for flash/mode switching)
                    if (touched && onTouchCallback) {
                        onTouchCallback(i);
                    }
                }
            }
        }

        // Check for long hold on multiple sensors
        checkLongHold(now);
    }

    // Check for long hold gesture (multiple sensors held simultaneously)
    void checkLongHold(uint32_t now) {
        if (longHoldTriggered) {
            // Already triggered, wait for release of all sensors
            if (getHeldCount() == 0) {
                longHoldTriggered = false;
            }
            return;
        }

        // Count how many sensors have been held long enough
        uint8_t longHeldCount = 0;
        uint32_t earliestPress = UINT32_MAX;
        uint32_t latestPress = 0;

        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            if (lastState[i] && pressStartTime[i] > 0) {
                uint32_t holdTime = now - pressStartTime[i];
                if (holdTime >= TOUCH_LONG_HOLD_MS) {
                    longHeldCount++;
                    if (pressStartTime[i] < earliestPress) earliestPress = pressStartTime[i];
                    if (pressStartTime[i] > latestPress) latestPress = pressStartTime[i];
                }
            }
        }

        // Trigger callback if 2+ sensors have been held long enough
        // and they were pressed within the multi-hold window of each other
        if (longHeldCount >= 2 && onLongHoldCallback) {
            if (latestPress - earliestPress <= TOUCH_MULTI_HOLD_WINDOW) {
                longHoldTriggered = true;
                onLongHoldCallback(longHeldCount);
            }
        }
    }

    // Set touch callback (press only, for local actions)
    void setOnTouch(TouchCallback callback) {
        onTouchCallback = callback;
    }

    // Set touch state callback (press and release, for input events)
    void setOnTouchState(TouchStateCallback callback) {
        onTouchStateCallback = callback;
    }

    // Set long hold callback (for gesture navigation)
    void setOnLongHold(LongHoldCallback callback) {
        onLongHoldCallback = callback;
    }

    // Get number of currently held sensors
    uint8_t getHeldCount() const {
        uint8_t count = 0;
        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            if (lastState[i]) count++;
        }
        return count;
    }

    // Check if long hold was just triggered (useful for suppressing other actions)
    bool wasLongHoldTriggered() const {
        return longHoldTriggered;
    }

    // Get how long a sensor has been held (0 if not held)
    uint32_t getHoldTime(uint8_t idx) const {
        if (idx >= TOUCH_NUM_SENSORS || !lastState[idx] || pressStartTime[idx] == 0) {
            return 0;
        }
        return millis() - pressStartTime[idx];
    }

    // Set touch sensitivity (0.0-1.0, higher = more sensitive)
    void setSensitivity(float sens) {
        sensitivity = constrain(sens, 0.1f, 0.95f);
        // Recalculate thresholds with new sensitivity
        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            thresholds[i] = (uint16_t)(baselines[i] * sensitivity);
        }
        dualOut.printf("Touch sensitivity set to %.2f\r\n", sensitivity);
    }

    float getSensitivity() const { return sensitivity; }

    // Get current touch state for a sensor
    bool isTouched(uint8_t idx) const {
        if (idx >= TOUCH_NUM_SENSORS) return false;
        return lastState[idx];
    }

    // Get raw touch value (for debugging)
    uint16_t getRawValue(uint8_t idx) const {
        if (idx >= TOUCH_NUM_SENSORS) return 0;
        return touchRead(touchPins[idx]);
    }

    // Get baseline value (for debugging)
    uint16_t getBaseline(uint8_t idx) const {
        if (idx >= TOUCH_NUM_SENSORS) return 0;
        return baselines[idx];
    }

    // Get threshold value (for debugging)
    uint16_t getThreshold(uint8_t idx) const {
        if (idx >= TOUCH_NUM_SENSORS) return 0;
        return thresholds[idx];
    }

    // Set threshold value for a specific sensor (for manual tuning)
    void setThreshold(uint8_t idx, uint16_t value) {
        if (idx >= TOUCH_NUM_SENSORS) return;
        thresholds[idx] = value;
    }

    bool isCalibrated() const { return calibrated; }

private:
    uint8_t touchPins[TOUCH_NUM_SENSORS];
    uint16_t baselines[TOUCH_NUM_SENSORS];
    uint16_t thresholds[TOUCH_NUM_SENSORS];
    bool lastState[TOUCH_NUM_SENSORS];
    uint32_t lastTouchTime[TOUCH_NUM_SENSORS];
    uint32_t pressStartTime[TOUCH_NUM_SENSORS];  // When each sensor was pressed (0 if not pressed)
    TouchCallback onTouchCallback;
    TouchStateCallback onTouchStateCallback;
    LongHoldCallback onLongHoldCallback;
    bool calibrated;
    float sensitivity;
    bool longHoldTriggered;  // Prevents repeated triggers until all released
};

#endif // TOUCH_HANDLER_H
