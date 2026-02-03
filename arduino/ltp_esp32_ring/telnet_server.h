/**
 * ESP32 Ring Controller - Telnet Server
 *
 * Provides a telnet interface on port 23 that mirrors USB serial:
 * - All Serial output is duplicated to connected telnet clients
 * - Terminal commands can be entered via telnet (same as USB)
 * - Supports one telnet client at a time
 */

#ifndef TELNET_SERVER_H
#define TELNET_SERVER_H

#include <Arduino.h>
#include <WiFi.h>

#define TELNET_PORT         23
#define TELNET_LINE_MAX     128

// DualPrint: writes to both Serial and an optional telnet client
// Use as a drop-in replacement for Serial in logging
class DualPrint : public Print {
public:
    DualPrint() : telnetClient(nullptr) {}

    void setTelnetClient(WiFiClient* client) {
        telnetClient = client;
    }

    size_t write(uint8_t c) override {
        Serial.write(c);
        if (telnetClient && telnetClient->connected()) {
            telnetClient->write(c);
        }
        return 1;
    }

    size_t write(const uint8_t* buffer, size_t size) override {
        Serial.write(buffer, size);
        if (telnetClient && telnetClient->connected()) {
            telnetClient->write(buffer, size);
        }
        return size;
    }

private:
    WiFiClient* telnetClient;
};

// Global dual output - all code uses this instead of Serial for output
extern DualPrint dualOut;

class TelnetServer {
public:
    TelnetServer(uint16_t port = TELNET_PORT)
        : server(port)
        , serverPort(port)
        , started(false)
        , linePos(0)
    {}

    void begin() {
        server.begin();
        server.setNoDelay(true);
        started = true;
        dualOut.println("Telnet: Server started on port 23");
    }

    // Check for new connections, return true if a command line is ready
    bool update() {
        if (!started) return false;

        // Accept new client
        if (server.hasClient()) {
            WiFiClient newClient = server.available();
            if (newClient) {
                if (client && client.connected()) {
                    // Already have a client, reject
                    newClient.println("Another session is active. Disconnecting.");
                    newClient.stop();
                } else {
                    client = newClient;
                    client.setNoDelay(true);
                    dualOut.setTelnetClient(&client);
                    linePos = 0;

                    dualOut.println();
                    dualOut.println("================================");
                    dualOut.println("LTP Ring Controller - Telnet");
                    dualOut.println("Type 'help' for commands");
                    dualOut.println("================================");
                    dualOut.println();

                    Serial.println("Telnet: Client connected");
                }
            }
        }

        // Check for disconnection
        if (client && !client.connected()) {
            client.stop();
            dualOut.setTelnetClient(nullptr);
            linePos = 0;
            Serial.println("Telnet: Client disconnected");
        }

        // Read input from telnet client
        if (client && client.connected() && client.available()) {
            while (client.available()) {
                char c = client.read();

                if (c == '\r' || c == '\n') {
                    if (linePos > 0) {
                        lineBuffer[linePos] = '\0';
                        linePos = 0;
                        return true;  // Command ready
                    }
                } else if (c == 0x03) {
                    // Ctrl+C: cancel current line, print new prompt
                    linePos = 0;
                    client.print("^C\r\n");
                } else if (c == '\b' || c == 127) {
                    if (linePos > 0) {
                        linePos--;
                        client.print("\b \b");
                    }
                } else if (c >= 32 && linePos < TELNET_LINE_MAX - 1) {
                    // Ignore telnet IAC sequences (0xFF)
                    if ((uint8_t)c == 0xFF) {
                        // Skip next 2 bytes of IAC command
                        if (client.available()) client.read();
                        if (client.available()) client.read();
                        continue;
                    }
                    lineBuffer[linePos++] = c;
                    client.write(c);  // Echo
                }
            }
        }

        return false;
    }

    // Get the last command line read from telnet
    const char* getLine() const { return lineBuffer; }

    bool hasClient() { return client && client.connected(); }

private:
    WiFiServer server;
    WiFiClient client;
    uint16_t serverPort;
    bool started;
    char lineBuffer[TELNET_LINE_MAX];
    uint8_t linePos;
};

#endif // TELNET_SERVER_H
