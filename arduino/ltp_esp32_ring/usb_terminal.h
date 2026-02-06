/**
 * ESP32 Ring Controller - USB Terminal
 *
 * Simple line-based command interface over USB Serial for
 * device configuration and status monitoring.
 */

#ifndef USB_TERMINAL_H
#define USB_TERMINAL_H

#include <Arduino.h>
#include "config.h"

// Forward declarations
struct DeviceConfig;
class WiFiTransport;
class RingDriver;
class TouchHandler;

// Terminal callback types
typedef void (*SaveConfigCallback)();
typedef void (*ResetConfigCallback)();

class UsbTerminal {
public:
    UsbTerminal()
        : linePos(0)
        , config(nullptr)
        , transport(nullptr)
        , leds(nullptr)
        , touch(nullptr)
        , saveCallback(nullptr)
        , resetCallback(nullptr)
    {}

    void begin(DeviceConfig* cfg, WiFiTransport* wifi, RingDriver* led, TouchHandler* tch) {
        config = cfg;
        transport = wifi;
        leds = led;
        touch = tch;
        linePos = 0;

        Serial.println();
        Serial.println("================================");
        Serial.println("LTP Ring Controller v1.0");
        Serial.println("Type 'help' for commands");
        Serial.println("================================");
        Serial.println();
    }

    void setSaveCallback(SaveConfigCallback cb) { saveCallback = cb; }
    void setResetCallback(ResetConfigCallback cb) { resetCallback = cb; }

    // Process serial input (call from loop)
    void update() {
        while (Serial.available()) {
            char c = Serial.read();

            if (c == '\r' || c == '\n') {
                if (linePos > 0) {
                    lineBuffer[linePos] = '\0';
                    processCommand(lineBuffer);
                    linePos = 0;
                }
            } else if (c == '\b' || c == 127) {
                // Backspace
                if (linePos > 0) {
                    linePos--;
                    Serial.print("\b \b");
                }
            } else if (linePos < TERMINAL_LINE_MAX - 1) {
                lineBuffer[linePos++] = c;
                Serial.print(c);  // Echo
            }
        }
    }

    // Public so telnet can also invoke commands
    void processCommand(const char* line);

private:
    char lineBuffer[TERMINAL_LINE_MAX];
    uint8_t linePos;

    DeviceConfig* config;
    WiFiTransport* transport;
    RingDriver* leds;
    TouchHandler* touch;
    SaveConfigCallback saveCallback;
    ResetConfigCallback resetCallback;
    void cmdHelp();
    void cmdStatus();
    void cmdWifi(const char* args);
    void cmdName(const char* args);
    void cmdOffset(const char* args);
    void cmdBrightness(const char* args);
    void cmdMode(const char* args);
    void cmdSave();
    void cmdReset();
    void cmdReboot();
    void cmdTouch();
    void cmdSensitivity(const char* args);
    void cmdCalibrate();
    void cmdTouchMon(const char* args);
    void cmdThreshold(const char* args);
    void cmdTimezone(const char* args);
    void cmdTest();
    void cmdSmoothing(const char* args);
    void cmdTouchHist(const char* args);
    void cmdTouchSnap(const char* args);
    void cmdBaseline(const char* args);
    void cmdLonghold(const char* args);
    void cmdHysteresis(const char* args);
    void cmdMultitap(const char* args);
};

// Implementation in main .ino file to access config structure
// This header just declares the interface

#endif // USB_TERMINAL_H
