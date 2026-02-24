/**
 * ESP32-C3 OLED Sink - WiFi Transport
 *
 * Manages WiFi connection and TCP server for JSON control channel.
 * Messages are newline-delimited JSON.
 * Supports multiple simultaneous client connections.
 */

#ifndef WIFI_TRANSPORT_H
#define WIFI_TRANSPORT_H

#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include "config.h"

// Maximum simultaneous TCP clients
#define MAX_TCP_CLIENTS 4

// Per-client line buffer size
#define CLIENT_LINE_BUFFER_SIZE 1024

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
        , activeClientIdx(-1)
    {
        for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
            linePos[i] = 0;
        }
    }

    // Initialize WiFi connection
    bool begin(const char* ssid, const char* password, const char* hostname) {
        if (strlen(ssid) == 0) {
            dualOut.println("WiFi: No SSID configured");
            return false;
        }

        strncpy(this->hostname, hostname, sizeof(this->hostname) - 1);
        this->hostname[sizeof(this->hostname) - 1] = '\0';

        // Hostname must be set before WiFi.mode() for DHCP to use it
        WiFi.setHostname(hostname);
        WiFi.mode(WIFI_STA);
        WiFi.begin(ssid, password);

        state = WifiState::CONNECTING;
        connectStartTime = millis();

        dualOut.printf("WiFi: Connecting to %s...\r\n", ssid);
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
                        dualOut.println("WiFi: Attempting reconnection...");
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
            dualOut.println("mDNS: Failed to start");
            return;
        }

        // Add sink service
        MDNS.addService("_ltp-sink", "_tcp", serverPort);

        // Add TXT records
        MdnsTxtBuilder txt;
        txt.add("ver", PROTOCOL_VERSION);
        txt.add("name", displayName);
        txt.add("desc", "ESP32-C3 OLED Display");
        txt.add("id", deviceId);
        txt.add("ctrl", "1");
        txt.add("type", "matrix");
        txt.add("pixels", pixels);

        char dimStr[16];
        snprintf(dimStr, sizeof(dimStr), "%dx%d", OLED_WIDTH, OLED_HEIGHT);
        txt.add("dim", dimStr);

        txt.add("color", colorFormat);
        txt.add("rate", maxRate);
        txt.add("data", "visual");
        txt.apply();

        mdnsStarted = true;
        dualOut.printf("mDNS: Started as %s.local, service _ltp-sink._tcp\r\n", hostname);
    }

    // Check if any client is connected
    bool hasClient() {
        for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
            if (clients[i] && clients[i].connected()) {
                return true;
            }
        }
        return false;
    }

    // Get number of connected clients
    int clientCount() {
        int count = 0;
        for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
            if (clients[i] && clients[i].connected()) {
                count++;
            }
        }
        return count;
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

    // Get active client index (for debugging)
    int getActiveClient() const { return activeClientIdx; }

    // Read a line from any client (returns empty string if no complete line)
    // Sets activeClientIdx to the client that sent the message
    String readLine() {
        for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
            if (!clients[i] || !clients[i].connected()) {
                continue;
            }

            while (clients[i].available()) {
                char c = clients[i].read();
                if (c == '\n') {
                    lineBuffers[i][linePos[i]] = '\0';
                    String line = String(lineBuffers[i]);
                    linePos[i] = 0;
                    activeClientIdx = i;  // Remember which client sent this
                    return line;
                } else if (c != '\r' && linePos[i] < CLIENT_LINE_BUFFER_SIZE - 1) {
                    lineBuffers[i][linePos[i]++] = c;
                }
            }
        }

        return String();
    }

    // Send a string to the client that last sent a message
    void send(const String& data) {
        if (activeClientIdx >= 0 && activeClientIdx < MAX_TCP_CLIENTS) {
            if (clients[activeClientIdx] && clients[activeClientIdx].connected()) {
                clients[activeClientIdx].print(data);
            }
        }
    }

    // Send a string to all connected clients
    void broadcast(const String& data) {
        for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
            if (clients[i] && clients[i].connected()) {
                clients[i].print(data);
            }
        }
    }

    // Disconnect all clients
    void disconnectAllClients() {
        for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
            if (clients[i]) {
                clients[i].stop();
            }
            linePos[i] = 0;
        }
        activeClientIdx = -1;
        if (state == WifiState::CLIENT_ACTIVE) {
            state = WifiState::CONNECTED;
        }
    }

private:
    WiFiServer server;
    WiFiClient clients[MAX_TCP_CLIENTS];
    uint16_t serverPort;
    WifiState state;
    uint32_t connectStartTime;
    uint32_t lastReconnectAttempt;
    bool mdnsStarted;
    char hostname[32];
    char lineBuffers[MAX_TCP_CLIENTS][CLIENT_LINE_BUFFER_SIZE];
    size_t linePos[MAX_TCP_CLIENTS];
    int activeClientIdx;  // Index of client that last sent a complete message

    void handleConnecting() {
        if (WiFi.status() == WL_CONNECTED) {
            state = WifiState::CONNECTED;
            dualOut.printf("WiFi: Connected! IP: %s\r\n", WiFi.localIP().toString().c_str());

            // Start TCP server
            server.begin();
            dualOut.printf("WiFi: TCP server started on port %d\r\n", serverPort);
        } else if (millis() - connectStartTime >= WIFI_CONNECT_TIMEOUT) {
            dualOut.println("WiFi: Connection timeout");
            state = WifiState::DISCONNECTED;
            lastReconnectAttempt = millis();
        }
    }

    void handleConnected() {
        // Check if WiFi is still connected
        if (WiFi.status() != WL_CONNECTED) {
            dualOut.println("WiFi: Disconnected");
            state = WifiState::DISCONNECTED;
            disconnectAllClients();
            return;
        }

        // Check for new client connections
        WiFiClient newClient = server.available();
        if (newClient) {
            // Find an empty slot
            int slot = -1;
            for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
                if (!clients[i] || !clients[i].connected()) {
                    slot = i;
                    break;
                }
            }

            if (slot >= 0) {
                clients[slot] = newClient;
                linePos[slot] = 0;
                state = WifiState::CLIENT_ACTIVE;
                dualOut.printf("WiFi: Client %d connected from %s (%d total)\r\n",
                              slot, newClient.remoteIP().toString().c_str(), clientCount());
            } else {
                // No slots available, reject connection
                dualOut.println("WiFi: Max clients reached, rejecting connection");
                newClient.stop();
            }
        }

        // Update state based on client count
        int count = clientCount();
        if (count > 0 && state != WifiState::CLIENT_ACTIVE) {
            state = WifiState::CLIENT_ACTIVE;
        } else if (count == 0 && state == WifiState::CLIENT_ACTIVE) {
            state = WifiState::CONNECTED;
            dualOut.println("WiFi: All clients disconnected");
        }

        // Clean up disconnected clients
        for (int i = 0; i < MAX_TCP_CLIENTS; i++) {
            if (clients[i] && !clients[i].connected()) {
                dualOut.printf("WiFi: Client %d disconnected\r\n", i);
                clients[i].stop();
                linePos[i] = 0;
            }
        }
    }
};

#endif // WIFI_TRANSPORT_H
