/**
 * LTP ESP32 Ring Controller - Sink Interface
 *
 * ESP32-based LED controller implementing the LTP sink interface:
 * - TCP control channel with JSON messages
 * - UDP data channel for pixel streaming
 * - mDNS service advertisement
 *
 * Hardware:
 * - 202 APA102 LEDs in a continuous ring
 * - 4 WS2812 LEDs positioned outside the ring (satellite indicators)
 * - 4 capacitive touch sensors (one per WS2812)
 * - WiFi connectivity
 * - USB terminal for configuration
 */

#include <Preferences.h>
#include <ArduinoJson.h>
#include "config.h"
#include "ring_driver.h"
#include "touch_handler.h"
#include "local_modes.h"
#include "wifi_transport.h"
#include "udp_receiver.h"
#include "sink_protocol.h"
#include "usb_terminal.h"

// ============================================================================
// Configuration (defined in config.h, stored in NVS)
// ============================================================================

DeviceConfig config;
Preferences preferences;

// ============================================================================
// Global Objects
// ============================================================================

RingDriver leds;
TouchHandler touch;
LocalModes localModes(leds);
WiFiTransport wifi;
UdpReceiver udpReceiver;
SinkProtocol protocol;
UsbTerminal terminal;

// Device ID (generated from MAC)
char deviceId[48];

// Status LED
bool statusLedState = false;
uint32_t lastStatusBlink = 0;

// Idle timeout tracking
uint32_t lastActivityTime = 0;
bool isIdle = false;

// Pixel buffer for UDP data
uint8_t pixelBuffer[RING_NUM_PIXELS * 3];

// Previous client state (for disconnect detection)
bool hadClient = false;

// ============================================================================
// Configuration Persistence (NVS)
// ============================================================================

void loadConfig() {
    preferences.begin(NVS_NAMESPACE, true);  // Read-only

    uint32_t magic = preferences.getUInt("magic", 0);
    if (magic != CONFIG_MAGIC) {
        preferences.end();
        resetConfig();
        return;
    }

    config.magic = magic;
    config.version = preferences.getUChar("version", CONFIG_VERSION);

    preferences.getString("ssid", config.wifiSsid, sizeof(config.wifiSsid));
    preferences.getString("password", config.wifiPassword, sizeof(config.wifiPassword));
    preferences.getString("name", config.deviceName, sizeof(config.deviceName));

    config.brightness = preferences.getUChar("brightness", 255);
    config.gamma = preferences.getUChar("gamma", 22);
    config.idleTimeout = preferences.getUShort("idleTimeout", DEFAULT_IDLE_TIMEOUT);
    config.localMode = preferences.getUChar("localMode", LOCAL_MODE_RAINBOW);
    config.ws2812Offset = preferences.getUChar("ws2812Offset", WS2812_DEFAULT_OFFSET);
    config.inputEventsEnabled = preferences.getBool("inputEvents", true);

    preferences.end();

    // Ensure device name is set
    if (strlen(config.deviceName) == 0) {
        strncpy(config.deviceName, DEVICE_NAME_DEFAULT, sizeof(config.deviceName));
    }
}

void saveConfig() {
    preferences.begin(NVS_NAMESPACE, false);  // Read-write

    preferences.putUInt("magic", CONFIG_MAGIC);
    preferences.putUChar("version", CONFIG_VERSION);
    preferences.putString("ssid", config.wifiSsid);
    preferences.putString("password", config.wifiPassword);
    preferences.putString("name", config.deviceName);
    preferences.putUChar("brightness", config.brightness);
    preferences.putUChar("gamma", config.gamma);
    preferences.putUShort("idleTimeout", config.idleTimeout);
    preferences.putUChar("localMode", config.localMode);
    preferences.putUChar("ws2812Offset", config.ws2812Offset);
    preferences.putBool("inputEvents", config.inputEventsEnabled);

    preferences.end();
    Serial.println("Config saved to NVS");
}

void resetConfig() {
    config.magic = CONFIG_MAGIC;
    config.version = CONFIG_VERSION;
    memset(config.wifiSsid, 0, sizeof(config.wifiSsid));
    memset(config.wifiPassword, 0, sizeof(config.wifiPassword));
    strncpy(config.deviceName, DEVICE_NAME_DEFAULT, sizeof(config.deviceName));
    config.brightness = 255;
    config.gamma = 22;
    config.idleTimeout = DEFAULT_IDLE_TIMEOUT;
    config.localMode = LOCAL_MODE_RAINBOW;
    config.ws2812Offset = WS2812_DEFAULT_OFFSET;
    config.inputEventsEnabled = true;

    saveConfig();
    Serial.println("Config reset to defaults");
}

// ============================================================================
// Device ID Generation
// ============================================================================

void generateDeviceId() {
    uint8_t mac[6];
    WiFi.macAddress(mac);
    snprintf(deviceId, sizeof(deviceId),
             "%02x%02x%02x%02x-%02x%02x-4000-8000-%02x%02x%02x%02x%02x%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

// ============================================================================
// Idle Timeout
// ============================================================================

void resetActivityTimer() {
    lastActivityTime = millis();
    if (isIdle) {
        isIdle = false;
        // Stop local mode when activity resumes
        localModes.stop();
    }
}

void checkIdleTimeout() {
    if (config.idleTimeout == 0) return;

    uint32_t elapsed = (millis() - lastActivityTime) / 1000;
    if (!isIdle && elapsed >= config.idleTimeout) {
        isIdle = true;
        // Start local mode when idle
        if (config.localMode != LOCAL_MODE_BLANK) {
            localModes.start(config.localMode);
        } else {
            leds.clear();
            leds.show();
        }
    }
}

// ============================================================================
// Touch Callback
// ============================================================================

void onTouchEvent(uint8_t touchIdx) {
    Serial.printf("Touch %d detected\n", touchIdx);

    // Flash corresponding WS2812
    leds.flashWS2812(touchIdx);

    // Send input event if enabled and connected
    if (config.inputEventsEnabled && wifi.hasClient()) {
        char inputName[16];
        snprintf(inputName, sizeof(inputName), "Touch%d", touchIdx);
        String event = protocol.buildInputEvent(touchIdx, inputName, true);
        wifi.send(event);
    }

    // Handle local mode switching
    if (!localModes.isActive()) {
        // Enter local mode with cycling
        localModes.start(LOCAL_MODE_CYCLE);
        config.localMode = LOCAL_MODE_CYCLE;
    } else {
        // Advance to next mode
        localModes.nextMode();
        config.localMode = localModes.getCurrentMode();
    }
}

// ============================================================================
// Status LED
// ============================================================================

void updateStatusLed() {
    uint32_t now = millis();
    uint32_t interval;

    switch (wifi.getState()) {
        case WifiState::CONNECTING:
            interval = STATUS_WIFI_CONNECTING;
            break;
        case WifiState::CONNECTED:
            interval = STATUS_WIFI_CONNECTED;
            break;
        case WifiState::CLIENT_ACTIVE:
            interval = STATUS_CLIENT_ACTIVE;
            break;
        default:
            interval = STATUS_ERROR;
            break;
    }

    if (interval == 0) {
        // Solid on
        digitalWrite(STATUS_LED_PIN, HIGH);
        statusLedState = true;
    } else if (now - lastStatusBlink >= interval) {
        lastStatusBlink = now;
        statusLedState = !statusLedState;
        digitalWrite(STATUS_LED_PIN, statusLedState ? HIGH : LOW);
    }
}

// ============================================================================
// USB Terminal Command Implementation
// ============================================================================

void UsbTerminal::processCommand(const char* line) {
    Serial.println();

    char cmd[32];
    const char* args = "";

    const char* space = strchr(line, ' ');
    if (space) {
        size_t cmdLen = space - line;
        if (cmdLen > sizeof(cmd) - 1) cmdLen = sizeof(cmd) - 1;
        strncpy(cmd, line, cmdLen);
        cmd[cmdLen] = '\0';
        args = space + 1;
        while (*args == ' ') args++;
    } else {
        strncpy(cmd, line, sizeof(cmd) - 1);
        cmd[sizeof(cmd) - 1] = '\0';
    }

    if (strcmp(cmd, "help") == 0) {
        cmdHelp();
    } else if (strcmp(cmd, "status") == 0) {
        cmdStatus();
    } else if (strcmp(cmd, "wifi") == 0) {
        cmdWifi(args);
    } else if (strcmp(cmd, "name") == 0) {
        cmdName(args);
    } else if (strcmp(cmd, "offset") == 0) {
        cmdOffset(args);
    } else if (strcmp(cmd, "brightness") == 0) {
        cmdBrightness(args);
    } else if (strcmp(cmd, "mode") == 0) {
        cmdMode(args);
    } else if (strcmp(cmd, "save") == 0) {
        cmdSave();
    } else if (strcmp(cmd, "reset") == 0) {
        cmdReset();
    } else if (strcmp(cmd, "touch") == 0) {
        cmdTouch();
    } else if (strcmp(cmd, "test") == 0) {
        cmdTest();
    } else {
        Serial.printf("Unknown command: %s\n", cmd);
        Serial.println("Type 'help' for available commands");
    }
}

void UsbTerminal::cmdHelp() {
    Serial.println("Available commands:");
    Serial.println("  wifi <ssid> <password>  - Set WiFi credentials");
    Serial.println("  name <device_name>      - Set device name (max 16 chars)");
    Serial.println("  offset <0-201>          - Set WS2812 rotational offset");
    Serial.println("  brightness <0-255>      - Set global brightness");
    Serial.println("  mode <0-5|255>          - Set local mode (255=cycle)");
    Serial.println("  status                  - Show current status");
    Serial.println("  touch                   - Show touch sensor values");
    Serial.println("  test                    - Run LED test pattern");
    Serial.println("  save                    - Save config to NVS");
    Serial.println("  reset                   - Factory reset");
    Serial.println("  help                    - Show this help");
}

void UsbTerminal::cmdStatus() {
    Serial.println("=== Device Status ===");
    Serial.printf("Name: %s\n", config->deviceName);
    Serial.printf("Device ID: %s\n", deviceId);
    Serial.printf("WiFi SSID: %s\n", config->wifiSsid);

    if (transport) {
        const char* stateStr;
        switch (transport->getState()) {
            case WifiState::DISCONNECTED: stateStr = "Disconnected"; break;
            case WifiState::CONNECTING:   stateStr = "Connecting..."; break;
            case WifiState::CONNECTED:    stateStr = "Connected"; break;
            case WifiState::CLIENT_ACTIVE: stateStr = "Client Active"; break;
            default: stateStr = "Unknown"; break;
        }
        Serial.printf("WiFi State: %s\n", stateStr);

        if (transport->isWifiConnected()) {
            Serial.printf("IP Address: %s\n", transport->getIP().toString().c_str());
            Serial.printf("Signal: %d dBm\n", transport->getRSSI());
            Serial.printf("Control Port: %d (TCP)\n", transport->getPort());
        }
    }

    Serial.printf("UDP Data Port: %d\n", udpReceiver.getPort());
    Serial.printf("Brightness: %d\n", config->brightness);
    Serial.printf("Local Mode: %d\n", config->localMode);
    Serial.printf("WS2812 Offset: %d\n", config->ws2812Offset);
    Serial.printf("UDP Packets: %lu received, %lu dropped\n",
                  udpReceiver.getPacketsReceived(), udpReceiver.getPacketsDropped());
}

void UsbTerminal::cmdWifi(const char* args) {
    if (strlen(args) == 0) {
        Serial.println("Usage: wifi <ssid> <password>");
        Serial.printf("Current SSID: %s\n", config->wifiSsid);
        return;
    }

    char ssid[33], password[65];
    const char* space = strchr(args, ' ');
    if (!space) {
        strncpy(ssid, args, sizeof(ssid) - 1);
        ssid[sizeof(ssid) - 1] = '\0';
        password[0] = '\0';
    } else {
        size_t ssidLen = space - args;
        if (ssidLen > sizeof(ssid) - 1) ssidLen = sizeof(ssid) - 1;
        strncpy(ssid, args, ssidLen);
        ssid[ssidLen] = '\0';
        strncpy(password, space + 1, sizeof(password) - 1);
        password[sizeof(password) - 1] = '\0';
    }

    strncpy(config->wifiSsid, ssid, sizeof(config->wifiSsid));
    strncpy(config->wifiPassword, password, sizeof(config->wifiPassword));

    Serial.printf("WiFi credentials set: %s\n", ssid);
    Serial.println("Use 'save' to persist, then restart to connect");
}

void UsbTerminal::cmdName(const char* args) {
    if (strlen(args) == 0) {
        Serial.printf("Current name: %s\n", config->deviceName);
        return;
    }

    strncpy(config->deviceName, args, DEVICE_NAME_MAX_LEN);
    config->deviceName[DEVICE_NAME_MAX_LEN] = '\0';
    Serial.printf("Device name set to: %s\n", config->deviceName);
}

void UsbTerminal::cmdOffset(const char* args) {
    if (strlen(args) == 0) {
        Serial.printf("Current WS2812 offset: %d\n", config->ws2812Offset);
        return;
    }

    int offset = atoi(args);
    if (offset < 0 || offset >= RING_NUM_PIXELS) {
        Serial.printf("Offset must be 0-%d\n", RING_NUM_PIXELS - 1);
        return;
    }

    config->ws2812Offset = offset;
    if (leds) leds->setWS2812Offset(offset);
    Serial.printf("WS2812 offset set to: %d\n", offset);
}

void UsbTerminal::cmdBrightness(const char* args) {
    if (strlen(args) == 0) {
        Serial.printf("Current brightness: %d\n", config->brightness);
        return;
    }

    int bright = atoi(args);
    if (bright < 0 || bright > 255) {
        Serial.println("Brightness must be 0-255");
        return;
    }

    config->brightness = bright;
    if (leds) leds->setBrightness(bright);
    Serial.printf("Brightness set to: %d\n", bright);
}

void UsbTerminal::cmdMode(const char* args) {
    if (strlen(args) == 0) {
        Serial.printf("Current mode: %d\n", config->localMode);
        Serial.println("Modes: 0=blank, 1=cylon, 2=rainbow, 3=fire, 4=sparkle, 5=chase, 255=cycle");
        return;
    }

    int mode = atoi(args);
    if (mode < 0 || (mode >= LOCAL_MODE_COUNT && mode != LOCAL_MODE_CYCLE)) {
        Serial.println("Invalid mode");
        return;
    }

    config->localMode = mode;
    localModes.start(mode);
    Serial.printf("Local mode set to: %d\n", mode);
}

void UsbTerminal::cmdSave() {
    if (saveCallback) saveCallback();
}

void UsbTerminal::cmdReset() {
    Serial.println("Resetting to factory defaults...");
    if (resetCallback) resetCallback();
}

void UsbTerminal::cmdTouch() {
    Serial.println("Touch sensor values:");
    if (touch) {
        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            Serial.printf("  Touch %d: value=%d, baseline=%d, threshold=%d, touched=%s\n",
                          i, touch->getRawValue(i), touch->getBaseline(i),
                          touch->getThreshold(i), touch->isTouched(i) ? "YES" : "no");
        }
    }
}

void UsbTerminal::cmdTest() {
    Serial.println("Running LED test pattern...");
    localModes.stop();

    for (int i = 0; i < RING_NUM_PIXELS; i++) {
        leds->clear();
        leds->setPixel(i, 255, 0, 0);
        leds->show();
        delay(5);
    }

    for (int i = 0; i < RING_NUM_PIXELS; i++) {
        leds->clear();
        leds->setPixel(i, 0, 255, 0);
        leds->show();
        delay(5);
    }

    for (int i = 0; i < RING_NUM_PIXELS; i++) {
        leds->clear();
        leds->setPixel(i, 0, 0, 255);
        leds->show();
        delay(5);
    }

    for (int j = 0; j < 3; j++) {
        for (int i = 0; i < WS2812_NUM_LEDS; i++) {
            leds->flashWS2812(i);
            while (leds->isFlashing()) {
                leds->updateFlash();
                delay(10);
            }
        }
    }

    leds->clear();
    leds->show();
    localModes.start(config->localMode);
    Serial.println("Test complete");
}

// ============================================================================
// Arduino Setup & Loop
// ============================================================================

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(100);

    Serial.println();
    Serial.println("LTP ESP32 Ring Controller (Sink Interface) starting...");

    // Status LED
    pinMode(STATUS_LED_PIN, OUTPUT);
    digitalWrite(STATUS_LED_PIN, LOW);

    // Load configuration
    loadConfig();

    // Generate device ID from MAC
    generateDeviceId();
    Serial.printf("Device ID: %s\n", deviceId);

    // Initialize LED driver
    leds.begin();
    leds.setBrightness(config.brightness);
    leds.setWS2812Offset(config.ws2812Offset);

    // Initialize touch sensors
    touch.begin();
    touch.setOnTouch(onTouchEvent);

    // Start local mode if configured
    if (config.localMode != LOCAL_MODE_BLANK) {
        localModes.start(config.localMode);
    }

    // Initialize UDP receiver
    udpReceiver.begin(5001);  // Fixed port for UDP data

    // Initialize protocol handler
    protocol.begin(&config, &leds, udpReceiver.getPort());

    // Initialize WiFi
    if (strlen(config.wifiSsid) > 0) {
        wifi.begin(config.wifiSsid, config.wifiPassword, config.deviceName);
    } else {
        Serial.println("WiFi: No credentials configured. Use terminal to set.");
    }

    // Initialize USB terminal
    terminal.begin(&config, &wifi, &leds, &touch);
    terminal.setSaveCallback(saveConfig);
    terminal.setResetCallback(resetConfig);

    lastActivityTime = millis();

    Serial.println("Initialization complete!");
    Serial.printf("Ring: %d pixels, WS2812: %d satellites\n", RING_NUM_PIXELS, WS2812_NUM_LEDS);
}

void loop() {
    // Update WiFi connection
    wifi.update();

    // Start mDNS when WiFi connects
    static bool mdnsStarted = false;
    if (wifi.isWifiConnected() && !mdnsStarted) {
        wifi.startMdns(deviceId, config.deviceName, RING_NUM_PIXELS, "rgb", 60);
        mdnsStarted = true;
    }

    // Handle client connection/disconnection
    bool hasClient = wifi.hasClient();
    if (hadClient && !hasClient) {
        // Client disconnected - stop stream
        protocol.stopStream();
        Serial.println("Client disconnected, stream stopped");
    }
    hadClient = hasClient;

    // Process control channel messages
    if (hasClient) {
        String line = wifi.readLine();
        if (line.length() > 0) {
            String response = protocol.processMessage(line);
            if (response.length() > 0) {
                wifi.send(response);
            }
        }
    }

    // Receive UDP pixel data
    if (protocol.isStreamActive()) {
        uint16_t pixelsReceived = udpReceiver.receive(pixelBuffer, RING_NUM_PIXELS);
        if (pixelsReceived > 0) {
            // Stop local mode and apply pixels
            localModes.stop();
            resetActivityTimer();

            // Copy pixels to LED driver
            for (uint16_t i = 0; i < pixelsReceived; i++) {
                leds.setPixel(i, pixelBuffer[i * 3],
                              pixelBuffer[i * 3 + 1],
                              pixelBuffer[i * 3 + 2]);
            }
            leds.show();
        }
    }

    // Update touch sensors
    touch.update();

    // Update LED flash animation
    leds.updateFlash();

    // Update local mode animation
    localModes.update();

    // Check idle timeout
    checkIdleTimeout();

    // Update status LED
    updateStatusLed();

    // Update USB terminal
    terminal.update();
}
