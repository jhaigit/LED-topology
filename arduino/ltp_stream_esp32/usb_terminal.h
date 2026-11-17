/**
 * LTP Stream (ESP32) - USB Terminal
 *
 * Line-based command interface over USB Serial for setting the device up
 * (WiFi, name, auth) and for reading or changing any control by name.
 * Command implementations are in the .ino, beside the settings they touch.
 */

#ifndef USB_TERMINAL_H
#define USB_TERMINAL_H

#include <Arduino.h>
#include "config.h"

class UsbTerminal {
public:
    UsbTerminal() : linePos(0) {}

    void begin() {
        linePos = 0;
        Serial.println();
        Serial.println("================================");
        Serial.println("LTP Stream (ESP32) v1.0");
        Serial.println("Type 'help' for commands");
        Serial.println("================================");
        Serial.println();
    }

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

    void cmdHelp();
    void cmdInfo();
    void cmdControls();
    void cmdGet(const char* args);
    void cmdSet(const char* args);
    void cmdWifi(const char* args);
    void cmdName(const char* args);
    void cmdAuthKey(const char* args);
    void cmdAuthOff();
    void cmdPair();
};

#endif // USB_TERMINAL_H
