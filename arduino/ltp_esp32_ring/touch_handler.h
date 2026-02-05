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
#define TOUCH_MULTI_HOLD_WINDOW 1000    // Max time diff between sensors (1 second)

// Histogram configuration
#define TOUCH_HIST_BINS         20      // Number of histogram bins
#define TOUCH_HIST_BIN_WIDTH    25      // Width of each bin (0-24 in bin 0, 25-49 in bin 1, etc.)

// History buffer configuration
#define TOUCH_HISTORY_SIZE      64      // Samples per sensor in circular buffer

// Adaptive baseline configuration
#define TOUCH_BASELINE_ALPHA    0.001f  // How fast baseline adapts (very slow)
#define TOUCH_BASELINE_MARGIN   1.2f    // Only adapt when reading > threshold * margin

class TouchHandler {
public:
    TouchHandler()
        : onTouchCallback(nullptr)
        , onTouchStateCallback(nullptr)
        , onLongHoldCallback(nullptr)
        , calibrated(false)
        , sensitivity(TOUCH_THRESHOLD_RATIO)
        , longHoldTriggered(false)
        , smoothingAlpha(1.0f)          // Default: no smoothing (raw values)
        , adaptiveBaseline(true)        // Default: adaptive baseline on
        , baselineAlpha(TOUCH_BASELINE_ALPHA)
    {
        touchPins[0] = TOUCH_PIN_0;
        touchPins[1] = TOUCH_PIN_1;
        touchPins[2] = TOUCH_PIN_2;
        touchPins[3] = TOUCH_PIN_3;

        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            baselines[i] = 0;
            thresholds[i] = 0;
            smoothedValues[i] = 0;
            floatBaselines[i] = 0;
            lastState[i] = false;
            lastTouchTime[i] = 0;
            pressStartTime[i] = 0;
            historyIdx[i] = 0;
            snapshotValid[i] = false;
        }
        resetHistograms();
        clearHistory();
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
            floatBaselines[i] = baselines[i];
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
            uint16_t rawValue = touchRead(touchPins[i]);

            // Record raw value in histogram
            recordHistogram(i, rawValue);

            // Record in circular history buffer
            recordHistory(i, rawValue);

            // Apply exponential smoothing
            // smoothed = alpha * raw + (1 - alpha) * smoothed
            if (smoothingAlpha >= 1.0f) {
                smoothedValues[i] = rawValue;
            } else {
                smoothedValues[i] = smoothingAlpha * rawValue + (1.0f - smoothingAlpha) * smoothedValues[i];
            }

            // Adaptive baseline: slowly track drift when sensor is clearly untouched
            if (adaptiveBaseline && !lastState[i]) {
                // Only adapt when reading is well above threshold (clearly not touched)
                float margin = thresholds[i] * TOUCH_BASELINE_MARGIN;
                if (smoothedValues[i] > margin) {
                    floatBaselines[i] = floatBaselines[i] * (1.0f - baselineAlpha)
                                      + smoothedValues[i] * baselineAlpha;
                    baselines[i] = (uint16_t)(floatBaselines[i] + 0.5f);
                    thresholds[i] = (uint16_t)(floatBaselines[i] * sensitivity);
                }
            }

            // Use smoothed value for threshold comparison
            bool touched = smoothedValues[i] < thresholds[i];

            // Debounce check
            if (touched != lastState[i]) {
                if (now - lastTouchTime[i] >= TOUCH_DEBOUNCE_MS) {
                    lastState[i] = touched;
                    lastTouchTime[i] = now;

                    if (touched) {
                        // Press started - record time and capture history snapshot
                        pressStartTime[i] = now;
                        captureSnapshot(i);
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

        // Count how many sensors are currently held
        uint8_t heldCount = getHeldCount();
        if (heldCount < 2) return;  // Quick exit if not enough sensors held

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

        // Debug: periodically show long hold progress when 2+ sensors held
        static uint32_t lastDebug = 0;
        if (heldCount >= 2 && now - lastDebug >= 500) {
            lastDebug = now;
            dualOut.printf("LongHold: %d held, %d long enough, callback=%s\r\n",
                          heldCount, longHeldCount, onLongHoldCallback ? "set" : "NULL");
        }

        // Trigger callback if 2+ sensors have been held long enough
        // and they were pressed within the multi-hold window of each other
        if (longHeldCount >= 2 && onLongHoldCallback) {
            uint32_t pressDiff = latestPress - earliestPress;
            if (pressDiff <= TOUCH_MULTI_HOLD_WINDOW) {
                longHoldTriggered = true;
                onLongHoldCallback(longHeldCount);
            } else {
                // Debug: show why it didn't trigger
                static uint32_t lastWindowDebug = 0;
                if (now - lastWindowDebug >= 1000) {
                    lastWindowDebug = now;
                    dualOut.printf("LongHold: press diff %lu > window %d\r\n",
                                  pressDiff, TOUCH_MULTI_HOLD_WINDOW);
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
            thresholds[i] = (uint16_t)(floatBaselines[i] * sensitivity);
        }
        dualOut.printf("Touch sensitivity set to %.2f\r\n", sensitivity);
    }

    float getSensitivity() const { return sensitivity; }

    // Adaptive baseline control
    void setAdaptiveBaseline(bool enabled) {
        adaptiveBaseline = enabled;
        dualOut.printf("Adaptive baseline: %s\r\n", enabled ? "ON" : "OFF");
    }

    bool isAdaptiveBaseline() const { return adaptiveBaseline; }

    void setBaselineAlpha(float alpha) {
        baselineAlpha = constrain(alpha, 0.0001f, 0.1f);
        dualOut.printf("Baseline drift alpha set to %.4f\r\n", baselineAlpha);
    }

    float getBaselineAlpha() const { return baselineAlpha; }

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

    // ========== Exponential Smoothing ==========

    // Set smoothing alpha (0.0 = heavy smoothing, 1.0 = no smoothing/raw only)
    void setSmoothingAlpha(float alpha) {
        smoothingAlpha = constrain(alpha, 0.01f, 1.0f);
        dualOut.printf("Touch smoothing alpha set to %.2f\r\n", smoothingAlpha);
    }

    float getSmoothingAlpha() const { return smoothingAlpha; }

    // Get smoothed value (for debugging)
    float getSmoothedValue(uint8_t idx) const {
        if (idx >= TOUCH_NUM_SENSORS) return 0;
        return smoothedValues[idx];
    }

    // ========== Histograms ==========

    // Reset all histograms
    void resetHistograms() {
        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            for (uint8_t b = 0; b < TOUCH_HIST_BINS; b++) {
                histograms[i][b] = 0;
            }
            histogramSamples[i] = 0;
        }
    }

    // Print histogram for one sensor
    void printHistogram(uint8_t idx) {
        if (idx >= TOUCH_NUM_SENSORS) return;

        dualOut.printf("Touch %d histogram (%lu samples):\r\n", idx, histogramSamples[idx]);

        // Find max count for scaling
        uint32_t maxCount = 1;
        for (uint8_t b = 0; b < TOUCH_HIST_BINS; b++) {
            if (histograms[idx][b] > maxCount) maxCount = histograms[idx][b];
        }

        // Print each bin with bar
        for (uint8_t b = 0; b < TOUCH_HIST_BINS; b++) {
            uint16_t low = b * TOUCH_HIST_BIN_WIDTH;
            uint16_t high = low + TOUCH_HIST_BIN_WIDTH - 1;
            uint32_t count = histograms[idx][b];

            // Scale bar to max 30 chars
            uint8_t barLen = (count * 30) / maxCount;

            dualOut.printf("  %3d-%3d: %6lu |", low, high, count);
            for (uint8_t j = 0; j < barLen; j++) dualOut.print('#');
            dualOut.println();
        }
    }

    // Print all histograms
    void printAllHistograms() {
        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            printHistogram(i);
            dualOut.println();
        }
    }

    // ========== History Buffer ==========

    // Clear all history buffers
    void clearHistory() {
        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            for (uint8_t j = 0; j < TOUCH_HISTORY_SIZE; j++) {
                historyBuffer[i][j] = 0;
                snapshotBuffer[i][j] = 0;
            }
            historyIdx[i] = 0;
            snapshotValid[i] = false;
        }
    }

    // Check if snapshot is available for sensor
    bool hasSnapshot(uint8_t idx) const {
        if (idx >= TOUCH_NUM_SENSORS) return false;
        return snapshotValid[idx];
    }

    // Print snapshot for one sensor (samples leading up to press trigger)
    void printSnapshot(uint8_t idx) {
        if (idx >= TOUCH_NUM_SENSORS) return;

        if (!snapshotValid[idx]) {
            dualOut.printf("Touch %d: No snapshot available (touch sensor to capture)\r\n", idx);
            return;
        }

        dualOut.printf("Touch %d snapshot (last %d samples before press, threshold=%d):\r\n",
                      idx, TOUCH_HISTORY_SIZE, thresholds[idx]);

        // Print in chronological order (oldest first)
        // snapshotIdx[idx] points to where next write would go, so oldest is at snapshotIdx
        for (uint8_t j = 0; j < TOUCH_HISTORY_SIZE; j++) {
            uint8_t pos = (snapshotIdx[idx] + j) % TOUCH_HISTORY_SIZE;
            uint16_t val = snapshotBuffer[idx][pos];

            // Mark values below threshold
            char marker = (val < thresholds[idx]) ? '*' : ' ';

            // Print 8 values per line
            if (j % 8 == 0) {
                if (j > 0) dualOut.println();
                dualOut.printf("  [%2d]: ", j);
            }
            dualOut.printf("%4d%c ", val, marker);
        }
        dualOut.println();
    }

    // Print all snapshots
    void printAllSnapshots() {
        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            printSnapshot(i);
            dualOut.println();
        }
    }

private:
    uint8_t touchPins[TOUCH_NUM_SENSORS];
    uint16_t baselines[TOUCH_NUM_SENSORS];
    uint16_t thresholds[TOUCH_NUM_SENSORS];
    float smoothedValues[TOUCH_NUM_SENSORS];    // Exponentially smoothed values
    bool lastState[TOUCH_NUM_SENSORS];
    uint32_t lastTouchTime[TOUCH_NUM_SENSORS];
    uint32_t pressStartTime[TOUCH_NUM_SENSORS];  // When each sensor was pressed (0 if not pressed)
    TouchCallback onTouchCallback;
    TouchStateCallback onTouchStateCallback;
    LongHoldCallback onLongHoldCallback;
    bool calibrated;
    float sensitivity;
    bool longHoldTriggered;  // Prevents repeated triggers until all released
    float smoothingAlpha;    // Smoothing factor (1.0 = no smoothing)
    bool adaptiveBaseline;   // Whether baseline tracks drift
    float baselineAlpha;     // How fast baseline adapts
    float floatBaselines[TOUCH_NUM_SENSORS];  // Float baselines for smooth tracking

    // Histogram data
    uint32_t histograms[TOUCH_NUM_SENSORS][TOUCH_HIST_BINS];
    uint32_t histogramSamples[TOUCH_NUM_SENSORS];

    // Circular history buffer
    uint16_t historyBuffer[TOUCH_NUM_SENSORS][TOUCH_HISTORY_SIZE];
    uint8_t historyIdx[TOUCH_NUM_SENSORS];      // Next write position

    // Snapshot buffer (captured on press trigger)
    uint16_t snapshotBuffer[TOUCH_NUM_SENSORS][TOUCH_HISTORY_SIZE];
    uint8_t snapshotIdx[TOUCH_NUM_SENSORS];     // Index at time of capture
    bool snapshotValid[TOUCH_NUM_SENSORS];

    // Record a value in the histogram
    void recordHistogram(uint8_t idx, uint16_t value) {
        uint8_t bin = value / TOUCH_HIST_BIN_WIDTH;
        if (bin >= TOUCH_HIST_BINS) bin = TOUCH_HIST_BINS - 1;
        histograms[idx][bin]++;
        histogramSamples[idx]++;
    }

    // Record a value in the circular history buffer
    void recordHistory(uint8_t idx, uint16_t value) {
        historyBuffer[idx][historyIdx[idx]] = value;
        historyIdx[idx] = (historyIdx[idx] + 1) % TOUCH_HISTORY_SIZE;
    }

    // Capture current history to snapshot buffer
    void captureSnapshot(uint8_t idx) {
        memcpy(snapshotBuffer[idx], historyBuffer[idx], sizeof(historyBuffer[idx]));
        snapshotIdx[idx] = historyIdx[idx];
        snapshotValid[idx] = true;
    }
};

#endif // TOUCH_HANDLER_H
