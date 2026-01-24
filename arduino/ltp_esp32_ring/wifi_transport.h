/**
 * ESP32 Ring Controller - WiFi Transport
 *
 * Manages WiFi connection and TCP server for JSON control channel.
 * Messages are newline-delimited JSON.
 */

#ifndef WIFI_TRANSPORT_H
#define WIFI_TRANSPORT_H

#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include "config.h"

// Connection state enum
enum class WifiState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    CLIENT_ACTIVE
};

// mDNS TXT record builder
class MdnsTxtBuilder {
public:
    void add(const char* key, const char* value) {
        if (count < MAX_TXT) {
            keys[count] = key;
            values[count] = value;
            count++;
        }
    }

    void add(const char* key, int value) {
        if (count < MAX_TXT) {
            snprintf(intBufs[intCount], 16, "%d", value);
            keys[count] = key;
            values[count] = intBufs[intCount];
            intCount++;
            count++;
        }
    }

    void apply() {
        for (int i = 0; i < count; i++) {
            MDNS.addServiceTxt("_ltp-sink", "_tcp", keys[i], values[i]);
        }
    }

private:
    static const int MAX_TXT = 16;
    const char* keys[MAX_TXT];
    const char* values[MAX_TXT];
    char intBufs[8][16];
    int count = 0;
    int intCount = 0;
};

class WiFiTransport {
public:
    WiFiTransport(uint16_t tcpPort = TCP_SERVER_PORT)
        : server(tcpPort)
        , serverPort(tcpPort)
        , state(WifiState::DISCONNECTED)
        , connectStartTime(0)
        , lastReconnectAttempt(0)
        , mdnsStarted(false)
        , linePos(0)
    {}

    // Initialize WiFi connection
    bool begin(const char* ssid, const char* password, const char* hostname) {
        if (strlen(ssid) == 0) {
            Serial.println("WiFi: No SSID configured");
            return false;
        }

        strncpy(this->hostname, hostname, sizeof(this->hostname) - 1);
        this->hostname[sizeof(this->hostname) - 1] = '\0';

        WiFi.mode(WIFI_STA);
        WiFi.setHostname(hostname);
        WiFi.begin(ssid, password);

        state = WifiState::CONNECTING;
        connectStartTime = millis();

        Serial.printf("WiFi: Connecting to %s...\r\n", ssid);
        return true;
    }

    // Update WiFi state (call from loop)
    void update() {
        switch (state) {
            case WifiState::CONNECTING:
                handleConnecting();
                break;

            case WifiState::CONNECTED:
            case WifiState::CLIENT_ACTIVE:
                handleConnected();
                break;

            case WifiState::DISCONNECTED:
                // Try to reconnect periodically
                if (millis() - lastReconnectAttempt >= WIFI_RECONNECT_DELAY) {
                    if (WiFi.SSID().length() > 0) {
                        Serial.println("WiFi: Attempting reconnection...");
                        WiFi.reconnect();
                        state = WifiState::CONNECTING;
                        connectStartTime = millis();
                    }
                    lastReconnectAttempt = millis();
                }
                break;
        }
    }

    // Start mDNS with sink service advertisement
    void startMdns(const char* deviceId, const char* displayName,
                   int pixels, const char* colorFormat, int maxRate) {
        if (mdnsStarted) return;

        if (!MDNS.begin(hostname)) {
            Serial.println("mDNS: Failed to start");
            return;
        }

        // Add sink service
        MDNS.addService("_ltp-sink", "_tcp", serverPort);

        // Add TXT records
        MdnsTxtBuilder txt;
        txt.add("ver", PROTOCOL_VERSION);
        txt.add("name", displayName);
        txt.add("desc", "ESP32 LED Ring");
        txt.add("id", deviceId);
        txt.add("ctrl", "1");
        txt.add("type", "custom");
        txt.add("pixels", pixels);
        txt.add("dim", String(pixels).c_str());
        txt.add("color", colorFormat);
        txt.add("rate", maxRate);
        txt.add("data", "visual");
        txt.apply();

        mdnsStarted = true;
        Serial.printf("mDNS: Started as %s.local, service _ltp-sink._tcp\r\n", hostname);
    }

    // Check if client is connected
    bool hasClient() {
        return client && client.connected();
    }

    // Get current WiFi state
    WifiState getState() const { return state; }

    // Check if WiFi is connected
    bool isWifiConnected() const {
        return WiFi.status() == WL_CONNECTED;
    }

    // Get IP address
    IPAddress getIP() const {
        return WiFi.localIP();
    }

    // Get RSSI (signal strength)
    int32_t getRSSI() const {
        return WiFi.RSSI();
    }

    // Get TCP server port
    uint16_t getPort() const { return serverPort; }

    // Read a line from client (returns empty string if no complete line)
    String readLine() {
        if (!client || !client.connected()) {
            return String();
        }

        while (client.available()) {
            char c = client.read();
            if (c == '\n') {
                lineBuffer[linePos] = '\0';
                String line = String(lineBuffer);
                linePos = 0;
                return line;
            } else if (c != '\r' && linePos < sizeof(lineBuffer) - 1) {
                lineBuffer[linePos++] = c;
            }
        }

        return String();
    }

    // Send a string to client
    void send(const String& data) {
        if (client && client.connected()) {
            client.print(data);
        }
    }

    // Disconnect client
    void disconnectClient() {
        if (client) {
            client.stop();
        }
        if (state == WifiState::CLIENT_ACTIVE) {
            state = WifiState::CONNECTED;
        }
        linePos = 0;
    }

private:
    WiFiServer server;
    WiFiClient client;
    uint16_t serverPort;
    WifiState state;
    uint32_t connectStartTime;
    uint32_t lastReconnectAttempt;
    bool mdnsStarted;
    char hostname[32];
    char lineBuffer[1024];
    size_t linePos;

    void handleConnecting() {
        if (WiFi.status() == WL_CONNECTED) {
            state = WifiState::CONNECTED;
            Serial.printf("WiFi: Connected! IP: %s\r\n", WiFi.localIP().toString().c_str());

            // Start TCP server
            server.begin();
            Serial.printf("WiFi: TCP server started on port %d\r\n", serverPort);
        } else if (millis() - connectStartTime >= WIFI_CONNECT_TIMEOUT) {
            Serial.println("WiFi: Connection timeout");
            state = WifiState::DISCONNECTED;
            lastReconnectAttempt = millis();
        }
    }

    void handleConnected() {
        // Check if WiFi is still connected
        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("WiFi: Disconnected");
            state = WifiState::DISCONNECTED;
            if (client) {
                client.stop();
            }
            linePos = 0;
            return;
        }

        // Check for new client connections (accept even if one exists)
        WiFiClient newClient = server.available();
        if (newClient) {
            // Close existing client if any
            if (client && client.connected()) {
                Serial.println("WiFi: Closing existing client for new connection");
                client.stop();
            }
            client = newClient;
            state = WifiState::CLIENT_ACTIVE;
            linePos = 0;
            Serial.printf("WiFi: Client connected from %s\r\n",
                          client.remoteIP().toString().c_str());
        } else if (state == WifiState::CLIENT_ACTIVE && (!client || !client.connected())) {
            state = WifiState::CONNECTED;
            Serial.println("WiFi: Client disconnected");
        }
    }
};

#endif // WIFI_TRANSPORT_H
