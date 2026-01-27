/**
 * ESP32 Ring Controller - Touch Handler
 *
 * Manages 4 capacitive touch sensors with auto-calibration,
 * debouncing, and callback support for touch events.
 */

#ifndef TOUCH_HANDLER_H
#define TOUCH_HANDLER_H

#include <Arduino.h>
#include "config.h"

// Touch callback function type (idx, pressed)
typedef void (*TouchCallback)(uint8_t touchIdx);
typedef void (*TouchStateCallback)(uint8_t touchIdx, bool pressed);

class TouchHandler {
public:
    TouchHandler()
        : onTouchCallback(nullptr)
        , onTouchStateCallback(nullptr)
        , calibrated(false)
        , sensitivity(TOUCH_THRESHOLD_RATIO)
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
    }

    // Set touch callback (press only, for local actions)
    void setOnTouch(TouchCallback callback) {
        onTouchCallback = callback;
    }

    // Set touch state callback (press and release, for input events)
    void setOnTouchState(TouchStateCallback callback) {
        onTouchStateCallback = callback;
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

    bool isCalibrated() const { return calibrated; }

private:
    uint8_t touchPins[TOUCH_NUM_SENSORS];
    uint16_t baselines[TOUCH_NUM_SENSORS];
    uint16_t thresholds[TOUCH_NUM_SENSORS];
    bool lastState[TOUCH_NUM_SENSORS];
    uint32_t lastTouchTime[TOUCH_NUM_SENSORS];
    TouchCallback onTouchCallback;
    TouchStateCallback onTouchStateCallback;
    bool calibrated;
    float sensitivity;
};

#endif // TOUCH_HANDLER_H
