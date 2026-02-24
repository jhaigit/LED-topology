/**
 * ESP32-C3 OLED Sink - Local Display Modes
 *
 * Simple display modes for standalone operation.
 * All rendering via u8g2 text/graphics primitives.
 */

#ifndef LOCAL_MODES_H
#define LOCAL_MODES_H

#include <Arduino.h>
#include <WiFi.h>
#include "config.h"
#include "oled_driver.h"

class LocalModes {
public:
    LocalModes(OledDriver& oled)
        : oled(oled)
        , currentMode(LOCAL_MODE_BLANK)
        , active(false)
        , lastUpdate(0)
        , cycleStart(0)
        , cycleTime(LOCAL_MODE_CYCLE_TIME)
        , cycleIdx(1)  // Start cycling from mode 1 (INFO)
        , patternFrame(0)
    {}

    void start(uint8_t mode) {
        currentMode = mode;
        active = true;
        lastUpdate = 0;
        cycleStart = millis();
        patternFrame = 0;

        if (mode == LOCAL_MODE_CYCLE) {
            cycleIdx = 1;  // Start from INFO mode
        }
    }

    void stop() {
        active = false;
    }

    void setCycleTime(uint16_t seconds) {
        cycleTime = (uint32_t)seconds * 1000;
    }

    bool isActive() const { return active; }
    uint8_t getCurrentMode() const { return currentMode; }

    void update() {
        if (!active) return;

        uint32_t now = millis();

        // Handle cycle mode
        if (currentMode == LOCAL_MODE_CYCLE) {
            if (now - cycleStart >= cycleTime) {
                cycleStart = now;
                cycleIdx++;
                if (cycleIdx >= LOCAL_MODE_COUNT) {
                    cycleIdx = 1;  // Skip BLANK in cycle
                }
            }
        }

        // Rate limit display updates (~15 fps for I2C OLED)
        if (now - lastUpdate < 66) return;
        lastUpdate = now;

        uint8_t modeToRender = currentMode;
        if (currentMode == LOCAL_MODE_CYCLE) {
            modeToRender = cycleIdx;
        }

        switch (modeToRender) {
            case LOCAL_MODE_BLANK:
                renderBlank();
                break;
            case LOCAL_MODE_INFO:
                renderInfo();
                break;
            case LOCAL_MODE_CLOCK:
                renderClock();
                break;
            case LOCAL_MODE_PATTERN:
                renderPattern();
                break;
        }
    }

private:
    OledDriver& oled;
    uint8_t currentMode;
    bool active;
    uint32_t lastUpdate;
    uint32_t cycleStart;
    uint32_t cycleTime;
    uint8_t cycleIdx;
    uint16_t patternFrame;

    void renderBlank() {
        oled.clearBuffer();
        oled.sendBuffer();
    }

    void renderInfo() {
        oled.clearBuffer();
        oled.setFont(u8g2_font_5x7_tr);

        // Device name
        oled.drawText(0, 7, WiFi.getHostname());

        // IP address
        if (WiFi.status() == WL_CONNECTED) {
            oled.drawText(0, 16, WiFi.localIP().toString().c_str());

            // Signal strength
            oled.drawTextF(0, 25, "RSSI: %d dBm", WiFi.RSSI());
        } else {
            oled.drawText(0, 16, "No WiFi");
        }

        // Uptime
        uint32_t upSec = millis() / 1000;
        uint16_t hours = upSec / 3600;
        uint8_t mins = (upSec % 3600) / 60;
        uint8_t secs = upSec % 60;
        oled.drawTextF(0, 34, "Up: %dh%02dm%02ds", hours, mins, secs);

        oled.sendBuffer();
    }

    void renderClock() {
        oled.clearBuffer();

        struct tm timeinfo;
        if (!getLocalTime(&timeinfo, 0)) {
            // NTP not synced yet
            oled.setFont(u8g2_font_5x7_tr);
            oled.drawText(4, 20, "Syncing NTP...");
            oled.sendBuffer();
            return;
        }

        // Large time display
        char timeBuf[16];
        strftime(timeBuf, sizeof(timeBuf), "%H:%M:%S", &timeinfo);

        oled.setFont(u8g2_font_7x14B_tr);
        // Center horizontally: 8 chars * 7px = 56px, (72-56)/2 = 8
        oled.drawText(8, 18, timeBuf);

        // Date below
        char dateBuf[16];
        strftime(dateBuf, sizeof(dateBuf), "%Y-%m-%d", &timeinfo);

        oled.setFont(u8g2_font_5x7_tr);
        // Center: 10 chars * 5px = 50px, (72-50)/2 = 11
        oled.drawText(11, 34, dateBuf);

        oled.sendBuffer();
    }

    void renderPattern() {
        oled.clearBuffer();

        patternFrame++;
        uint8_t phase = patternFrame >> 2;  // Slow down animation

        // Checkerboard with shifting phase
        uint8_t blockSize = 4;
        for (uint8_t y = 0; y < OLED_HEIGHT; y++) {
            for (uint8_t x = 0; x < OLED_WIDTH; x++) {
                bool on = ((x / blockSize + y / blockSize + phase) & 1) != 0;
                if (on) {
                    oled.setDrawColor(1);
                    oled.drawPixel(x, y);
                }
            }
        }

        oled.sendBuffer();
    }
};

#endif // LOCAL_MODES_H
