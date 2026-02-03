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
#include "telnet_server.h"
#include "ring_driver.h"
#include "touch_handler.h"
#include "local_modes.h"
#include "wifi_transport.h"
#include "udp_receiver.h"
#include "sink_protocol.h"
#include "usb_terminal.h"
#include "build_info.h"

// Global dual output (Serial + telnet)
DualPrint dualOut;

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
TelnetServer telnet;

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
    config.localMode = preferences.getUChar("localMode", LOCAL_MODE_BLANK);
    config.ws2812Offset = preferences.getUChar("ws2812Offset", WS2812_DEFAULT_OFFSET);
    config.inputEventsEnabled = preferences.getBool("inputEvents", true);
    config.touchSensitivity = preferences.getFloat("touchSens", TOUCH_THRESHOLD_RATIO);
    preferences.getString("timezone", config.timezone, sizeof(config.timezone));
    if (strlen(config.timezone) == 0) {
        strncpy(config.timezone, CLOCK_DEFAULT_TZ, sizeof(config.timezone));
    }
    config.cycleTime = preferences.getUShort("cycleTime", 10);

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
    preferences.putFloat("touchSens", config.touchSensitivity);
    preferences.putString("timezone", config.timezone);
    preferences.putUShort("cycleTime", config.cycleTime);

    preferences.end();
    dualOut.println("Config saved to NVS");
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
    config.localMode = LOCAL_MODE_BLANK;  // Touch-overlay only by default
    config.ws2812Offset = WS2812_DEFAULT_OFFSET;
    config.inputEventsEnabled = true;
    config.touchSensitivity = TOUCH_THRESHOLD_RATIO;
    strncpy(config.timezone, CLOCK_DEFAULT_TZ, sizeof(config.timezone));
    config.cycleTime = 10;

    saveConfig();
    dualOut.println("Config reset to defaults");
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
        dualOut.println("Activity: waking from idle");
    }
}

void checkIdleTimeout() {
    if (config.idleTimeout == 0) return;

    uint32_t elapsed = (millis() - lastActivityTime) / 1000;
    if (!isIdle && elapsed >= config.idleTimeout) {
        isIdle = true;
        // Go dark when idle
        localModes.stop();
        leds.clear();
        leds.show();
        dualOut.println("Idle timeout: LEDs off");
    }
}

// ============================================================================
// Touch Callbacks and Navigation State Machine
// ============================================================================

// Navigation state machine:
// - Mode 0 (touch-only, default) → long hold 2 → Mode 255 (cycling)
// - Mode 255 (cycling) → tap advances pattern, long hold 2 → Mode 11 (clock)
// - Any pattern mode (1-10) → long hold 2 → Mode 11 (clock)
// - Mode 11 (clock) → long hold 2 → Mode 0
// Navigation is runtime only - does not persist to config

// Called on touch press - handles ripple trigger and mode advancement in cycle
void onTouchPress(uint8_t touchIdx) {
    dualOut.printf("Touch %d pressed\r\n", touchIdx);

    // Reset idle timer on any touch
    resetActivityTimer();

    // Always trigger a ripple effect (touch overlay is always active)
    localModes.triggerTouchRipple(touchIdx);

    // Flash corresponding WS2812 (brief visual feedback)
    leds.flashWS2812(touchIdx);

    // In cycle mode (255), single tap advances to next pattern
    if (localModes.getCurrentMode() == LOCAL_MODE_CYCLE) {
        localModes.nextMode();
        // Don't save to config - runtime only
    }
    // In other modes, single tap just shows the ripple (no mode change)
}

// Called on long hold of 2+ sensors - handles navigation
void onLongHold(uint8_t numSensors) {
    if (numSensors < 2) return;

    dualOut.printf("Long hold detected (%d sensors)\r\n", numSensors);

    uint8_t currentMode = localModes.getCurrentMode();

    // State machine navigation (runtime only, not saved to config)
    if (currentMode == LOCAL_MODE_BLANK) {
        // Mode 0 (touch-only) → Mode 255 (cycling patterns)
        dualOut.println("Navigation: 0 (touch-only) -> 255 (cycle)");
        localModes.start(LOCAL_MODE_CYCLE);
    } else if (currentMode == LOCAL_MODE_CLOCK) {
        // Clock → Mode 0 (touch-only)
        dualOut.println("Navigation: 11 (clock) -> 0 (touch-only)");
        localModes.start(LOCAL_MODE_BLANK);
    } else {
        // Any other mode (1-10, 255) → Clock
        dualOut.printf("Navigation: %d -> 11 (clock)\r\n", currentMode);
        localModes.start(LOCAL_MODE_CLOCK);
    }

    // Flash all WS2812s to indicate navigation
    for (uint8_t i = 0; i < WS2812_NUM_LEDS; i++) {
        leds.flashWS2812(i);
    }
}

// Called on both press and release - sends input events to controller
void onTouchStateChange(uint8_t touchIdx, bool pressed) {
    dualOut.printf("Touch %d %s\r\n", touchIdx, pressed ? "pressed" : "released");

    // Send input event if enabled and connected
    if (config.inputEventsEnabled && wifi.hasClient()) {
        char inputName[16];
        snprintf(inputName, sizeof(inputName), "Touch%d", touchIdx);
        String event = protocol.buildInputEvent(touchIdx, inputName, pressed);
        wifi.send(event);
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
    dualOut.println();

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
    } else if (strcmp(cmd, "sensitivity") == 0) {
        cmdSensitivity(args);
    } else if (strcmp(cmd, "calibrate") == 0) {
        cmdCalibrate();
    } else if (strcmp(cmd, "touchmon") == 0) {
        cmdTouchMon(args);
    } else if (strcmp(cmd, "threshold") == 0) {
        cmdThreshold(args);
    } else if (strcmp(cmd, "timezone") == 0 || strcmp(cmd, "tz") == 0) {
        cmdTimezone(args);
    } else if (strcmp(cmd, "test") == 0) {
        cmdTest();
    } else {
        dualOut.printf("Unknown command: %s\r\n", cmd);
        dualOut.println("Type 'help' for available commands");
    }
}

void UsbTerminal::cmdHelp() {
    dualOut.println("Available commands:");
    dualOut.println("  wifi <ssid> <password>  - Set WiFi credentials");
    dualOut.println("  name <device_name>      - Set device name (max 16 chars)");
    dualOut.println("  offset <0-201>          - Set WS2812 rotational offset");
    dualOut.println("  brightness <0-255>      - Set global brightness");
    dualOut.println("  mode <0-5|255>          - Set local mode (255=cycle)");
    dualOut.println("  status                  - Show current status");
    dualOut.println("  touch                   - Show touch sensor values");
    dualOut.println("  sensitivity <0.1-0.95>  - Set touch sensitivity (higher=more sensitive)");
    dualOut.println("  calibrate               - Recalibrate touch sensors (do not touch during)");
    dualOut.println("  touchmon [seconds]      - Monitor touch values continuously (default 10s)");
    dualOut.println("  threshold <0-3> <value> - Manually set threshold for a sensor");
    dualOut.println("  timezone <tz_string>    - Set POSIX timezone (e.g. PST8PDT,M3.2.0,M11.1.0)");
    dualOut.println("  test                    - Run LED test pattern");
    dualOut.println("  save                    - Save config to NVS");
    dualOut.println("  reset                   - Factory reset");
    dualOut.println("  help                    - Show this help");
}

void UsbTerminal::cmdStatus() {
    dualOut.println("=== Device Status ===");
    dualOut.printf("Name: %s\r\n", config->deviceName);
    dualOut.printf("Device ID: %s\r\n", deviceId);
    dualOut.printf("WiFi SSID: %s\r\n", config->wifiSsid);

    if (transport) {
        const char* stateStr;
        switch (transport->getState()) {
            case WifiState::DISCONNECTED: stateStr = "Disconnected"; break;
            case WifiState::CONNECTING:   stateStr = "Connecting..."; break;
            case WifiState::CONNECTED:    stateStr = "Connected"; break;
            case WifiState::CLIENT_ACTIVE: stateStr = "Client Active"; break;
            default: stateStr = "Unknown"; break;
        }
        dualOut.printf("WiFi State: %s\r\n", stateStr);

        if (transport->isWifiConnected()) {
            dualOut.printf("IP Address: %s\r\n", transport->getIP().toString().c_str());
            dualOut.printf("Signal: %d dBm\r\n", transport->getRSSI());
            dualOut.printf("Control Port: %d (TCP)\r\n", transport->getPort());
        }
    }

    dualOut.printf("UDP Data Port: %d\r\n", udpReceiver.getPort());
    dualOut.println("--- Controls ---");
    dualOut.printf("Brightness: %d\r\n", config->brightness);
    dualOut.printf("Gamma: %.1f\r\n", config->gamma / 10.0);
    dualOut.printf("Local Mode: %d\r\n", config->localMode);
    dualOut.printf("WS2812 Offset: %d\r\n", config->ws2812Offset);
    dualOut.printf("Idle Timeout: %d sec\r\n", config->idleTimeout);
    dualOut.printf("Input Events: %s\r\n", config->inputEventsEnabled ? "enabled" : "disabled");
    if (touch) {
        dualOut.printf("Touch Sensitivity: %.2f\r\n", touch->getSensitivity());
    }
    dualOut.printf("Timezone: %s\r\n", config->timezone);
    struct tm timeinfo;
    if (getLocalTime(&timeinfo, 0)) {
        char timeBuf[32];
        strftime(timeBuf, sizeof(timeBuf), "%Y-%m-%d %H:%M:%S", &timeinfo);
        dualOut.printf("Current Time: %s\r\n", timeBuf);
    } else {
        dualOut.println("Current Time: not synced");
    }
    dualOut.println("--- Stats ---");
    dualOut.printf("UDP Packets: %lu received, %lu dropped\r\n",
                  udpReceiver.getPacketsReceived(), udpReceiver.getPacketsDropped());
}

void UsbTerminal::cmdWifi(const char* args) {
    if (strlen(args) == 0) {
        dualOut.println("Usage: wifi <ssid> <password>");
        dualOut.printf("Current SSID: %s\r\n", config->wifiSsid);
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

    dualOut.printf("WiFi credentials set: %s\r\n", ssid);
    dualOut.println("Use 'save' to persist, then restart to connect");
}

void UsbTerminal::cmdName(const char* args) {
    if (strlen(args) == 0) {
        dualOut.printf("Current name: %s\r\n", config->deviceName);
        return;
    }

    strncpy(config->deviceName, args, DEVICE_NAME_MAX_LEN);
    config->deviceName[DEVICE_NAME_MAX_LEN] = '\0';
    dualOut.printf("Device name set to: %s\r\n", config->deviceName);
}

void UsbTerminal::cmdOffset(const char* args) {
    if (strlen(args) == 0) {
        dualOut.printf("Current WS2812 offset: %d\r\n", config->ws2812Offset);
        return;
    }

    int offset = atoi(args);
    if (offset < 0 || offset >= RING_NUM_PIXELS) {
        dualOut.printf("Offset must be 0-%d\r\n", RING_NUM_PIXELS - 1);
        return;
    }

    config->ws2812Offset = offset;
    if (leds) leds->setWS2812Offset(offset);
    dualOut.printf("WS2812 offset set to: %d\r\n", offset);
}

void UsbTerminal::cmdBrightness(const char* args) {
    if (strlen(args) == 0) {
        dualOut.printf("Current brightness: %d\r\n", config->brightness);
        return;
    }

    int bright = atoi(args);
    if (bright < 0 || bright > 255) {
        dualOut.println("Brightness must be 0-255");
        return;
    }

    config->brightness = bright;
    if (leds) leds->setBrightness(bright);
    dualOut.printf("Brightness set to: %d\r\n", bright);
}

void UsbTerminal::cmdMode(const char* args) {
    if (strlen(args) == 0) {
        dualOut.printf("Current mode: %d\r\n", config->localMode);
        dualOut.println("Modes: 0=touch-only, 1=cylon, 2=rainbow, 3=fire, 4=sparkle, 5=chase,");
        dualOut.println("       6=mitosis, 7=touch, 8-10=sinwave, 11=clock, 255=cycle");
        dualOut.println("Touch: long-hold 2 globes to navigate (0->255->11->0)");
        return;
    }

    int mode = atoi(args);
    if (mode < 0 || (mode >= LOCAL_MODE_COUNT && mode != LOCAL_MODE_CYCLE)) {
        dualOut.println("Invalid mode");
        return;
    }

    config->localMode = mode;
    localModes.start(mode);
    dualOut.printf("Local mode set to: %d\r\n", mode);
}

void UsbTerminal::cmdSave() {
    if (saveCallback) saveCallback();
}

void UsbTerminal::cmdReset() {
    dualOut.println("Resetting to factory defaults...");
    if (resetCallback) resetCallback();
}

void UsbTerminal::cmdTouch() {
    dualOut.println("Touch sensor values:");
    if (touch) {
        dualOut.printf("Sensitivity: %.2f\r\n", touch->getSensitivity());
        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            dualOut.printf("  Touch %d: value=%d, baseline=%d, threshold=%d, touched=%s\r\n",
                          i, touch->getRawValue(i), touch->getBaseline(i),
                          touch->getThreshold(i), touch->isTouched(i) ? "YES" : "no");
        }
    }
}

void UsbTerminal::cmdSensitivity(const char* args) {
    if (!touch) {
        dualOut.println("Touch handler not available");
        return;
    }

    if (strlen(args) == 0) {
        dualOut.printf("Current sensitivity: %.2f (higher = more sensitive)\r\n",
                      touch->getSensitivity());
        dualOut.println("Usage: sensitivity <0.1-0.95>");
        return;
    }

    float sens = atof(args);
    if (sens < 0.1 || sens > 0.95) {
        dualOut.println("Sensitivity must be 0.1-0.95");
        return;
    }

    touch->setSensitivity(sens);
    config->touchSensitivity = sens;
    // Update threshold display
    for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
        dualOut.printf("  Touch %d: new threshold=%d\r\n", i, touch->getThreshold(i));
    }
}

void UsbTerminal::cmdCalibrate() {
    if (!touch) {
        dualOut.println("Touch handler not available");
        return;
    }
    dualOut.println("Starting calibration - do NOT touch sensors!");
    delay(500);  // Give user time to release
    touch->calibrate();
}

void UsbTerminal::cmdTouchMon(const char* args) {
    if (!touch) {
        dualOut.println("Touch handler not available");
        return;
    }

    int duration = 10;  // Default 10 seconds
    if (strlen(args) > 0) {
        duration = atoi(args);
        if (duration < 1) duration = 1;
        if (duration > 300) duration = 300;
    }

    dualOut.printf("Monitoring touch sensors for %d seconds...\r\n", duration);
    dualOut.println("Legend: value (baseline) [threshold] <TOUCHED if below threshold>");
    dualOut.println("Press any key to stop early.\r\n");

    uint32_t startTime = millis();
    uint32_t endTime = startTime + (duration * 1000);
    uint32_t lastPrint = 0;

    while (millis() < endTime) {
        // Check for key press to stop early (both Serial and telnet would need handling)
        if (Serial.available()) {
            Serial.read();  // Consume the character
            dualOut.println("\r\nMonitoring stopped by user.");
            return;
        }

        // Print every 200ms
        if (millis() - lastPrint >= 200) {
            lastPrint = millis();
            dualOut.printf("T0: %4d (%4d) [%4d]%s  T1: %4d (%4d) [%4d]%s  T2: %4d (%4d) [%4d]%s  T3: %4d (%4d) [%4d]%s\r\n",
                touch->getRawValue(0), touch->getBaseline(0), touch->getThreshold(0),
                touch->isTouched(0) ? " *" : "  ",
                touch->getRawValue(1), touch->getBaseline(1), touch->getThreshold(1),
                touch->isTouched(1) ? " *" : "  ",
                touch->getRawValue(2), touch->getBaseline(2), touch->getThreshold(2),
                touch->isTouched(2) ? " *" : "  ",
                touch->getRawValue(3), touch->getBaseline(3), touch->getThreshold(3),
                touch->isTouched(3) ? " *" : "  ");
        }

        // Still run normal updates so touches work
        touch->update();
        delay(10);
    }
    dualOut.println("\r\nMonitoring complete.");
}

void UsbTerminal::cmdThreshold(const char* args) {
    if (!touch) {
        dualOut.println("Touch handler not available");
        return;
    }

    if (strlen(args) == 0) {
        dualOut.println("Current thresholds:");
        for (uint8_t i = 0; i < TOUCH_NUM_SENSORS; i++) {
            dualOut.printf("  Touch %d: %d (baseline=%d, ratio=%.2f)\r\n",
                i, touch->getThreshold(i), touch->getBaseline(i),
                (float)touch->getThreshold(i) / touch->getBaseline(i));
        }
        dualOut.println("Usage: threshold <sensor 0-3> <value>");
        dualOut.println("  Lower threshold = more sensitive (triggers sooner)");
        dualOut.println("  Typical range: 20-80% of baseline");
        return;
    }

    // Parse sensor index and value
    int sensor = -1;
    int value = -1;
    if (sscanf(args, "%d %d", &sensor, &value) != 2) {
        dualOut.println("Usage: threshold <sensor 0-3> <value>");
        return;
    }

    if (sensor < 0 || sensor >= TOUCH_NUM_SENSORS) {
        dualOut.printf("Sensor must be 0-%d\r\n", TOUCH_NUM_SENSORS - 1);
        return;
    }

    if (value < 1 || value > 1000) {
        dualOut.println("Value must be 1-1000");
        return;
    }

    touch->setThreshold(sensor, value);
    dualOut.printf("Touch %d threshold set to %d (baseline=%d, ratio=%.2f)\r\n",
        sensor, value, touch->getBaseline(sensor),
        (float)value / touch->getBaseline(sensor));
}

void UsbTerminal::cmdTimezone(const char* args) {
    if (strlen(args) == 0) {
        dualOut.printf("Current timezone: %s\r\n", config->timezone);
        dualOut.println("Usage: timezone <POSIX_TZ_string>");
        dualOut.println("Examples:");
        dualOut.println("  PST8PDT,M3.2.0,M11.1.0    (US Pacific)");
        dualOut.println("  EST5EDT,M3.2.0,M11.1.0     (US Eastern)");
        dualOut.println("  CST6CDT,M3.2.0,M11.1.0     (US Central)");
        dualOut.println("  MST7MDT,M3.2.0,M11.1.0     (US Mountain)");
        dualOut.println("  UTC0                        (UTC)");

        // Show current time if available
        struct tm timeinfo;
        if (getLocalTime(&timeinfo, 0)) {
            char timeBuf[32];
            strftime(timeBuf, sizeof(timeBuf), "%Y-%m-%d %H:%M:%S", &timeinfo);
            dualOut.printf("Current time: %s\r\n", timeBuf);
        } else {
            dualOut.println("Time not yet synced (NTP pending)");
        }
        return;
    }

    strncpy(config->timezone, args, sizeof(config->timezone) - 1);
    config->timezone[sizeof(config->timezone) - 1] = '\0';

    // Apply immediately
    configTzTime(config->timezone, CLOCK_NTP_SERVER);
    dualOut.printf("Timezone set to: %s\r\n", config->timezone);
    dualOut.println("Use 'save' to persist");
}

void UsbTerminal::cmdTest() {
    dualOut.println("Running LED test pattern...");
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
    dualOut.println("Test complete");
}

// ============================================================================
// Arduino Setup & Loop
// ============================================================================

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(100);

    dualOut.println();
    dualOut.println("LTP ESP32 Ring Controller (Sink Interface) starting...");

    // Status LED
    pinMode(STATUS_LED_PIN, OUTPUT);
    digitalWrite(STATUS_LED_PIN, LOW);

    // Load configuration
    loadConfig();

    // Generate device ID from MAC
    generateDeviceId();
    dualOut.printf("Device ID: %s\r\n", deviceId);

    // Initialize LED driver
    leds.begin();
    leds.setBrightness(config.brightness);
    leds.setWS2812Offset(config.ws2812Offset);

    // Initialize touch sensors
    touch.begin();
    touch.setSensitivity(config.touchSensitivity);
    touch.setOnTouch(onTouchPress);
    touch.setOnTouchState(onTouchStateChange);
    touch.setOnLongHold(onLongHold);

    // Give local modes access to touch handler for touch-reactive mode
    localModes.setTouchHandler(&touch);
    localModes.setCycleTime(config.cycleTime);

    // Start local mode (always start, even mode 0 for touch overlay)
    localModes.start(config.localMode);

    // Initialize WiFi first (required before UDP)
    if (strlen(config.wifiSsid) > 0) {
        wifi.begin(config.wifiSsid, config.wifiPassword, config.deviceName);
    } else {
        dualOut.println("WiFi: No credentials configured. Use terminal to set.");
        // Still need to init WiFi stack for UDP to work later
        WiFi.mode(WIFI_STA);
    }

    // Initialize protocol handler (UDP receiver started when WiFi connects)
    protocol.begin(&config, &leds, 5001);  // UDP port will be 5001
    protocol.setLocalModeCallback([](uint8_t mode) {
        localModes.start(mode);
    });
    protocol.setCycleTimeCallback([](uint16_t seconds) {
        localModes.setCycleTime(seconds);
    });
    protocol.setSaveCallback(saveConfig);
    protocol.setRebootCallback([]() {
        dualOut.println("Rebooting...");
        delay(100);  // Allow message to flush
        ESP.restart();
    });

    // Initialize USB terminal
    terminal.begin(&config, &wifi, &leds, &touch);
    terminal.setSaveCallback(saveConfig);
    terminal.setResetCallback(resetConfig);

    lastActivityTime = millis();

    dualOut.println("Initialization complete!");
    dualOut.printf("Ring: %d pixels, WS2812: %d satellites\r\n", RING_NUM_PIXELS, WS2812_NUM_LEDS);
}

void loop() {
    // Update WiFi connection
    wifi.update();

    // Start mDNS and UDP receiver when WiFi connects
    static bool networkStarted = false;
    if (wifi.isWifiConnected() && !networkStarted) {
        // Start UDP receiver now that WiFi is ready
        udpReceiver.begin(5001);

        // Start mDNS advertisement
        wifi.startMdns(deviceId, config.deviceName, RING_NUM_PIXELS, "rgb", 60);

        // Start telnet server
        telnet.begin();

        // Start NTP time sync
        configTzTime(config.timezone, CLOCK_NTP_SERVER);
        dualOut.printf("NTP: Syncing with TZ=%s\r\n", config.timezone);

        networkStarted = true;
    }

    // Handle client connection/disconnection
    bool hasClient = wifi.hasClient();
    if (hadClient && !hasClient) {
        // Client disconnected - stop stream
        protocol.stopStream();
        dualOut.println("Client disconnected, stream stopped");
    }
    hadClient = hasClient;

    // Process control channel messages
    if (hasClient) {
        String line = wifi.readLine();
        if (line.length() > 0) {
            dualOut.printf("TCP rx[%d]: %d bytes\r\n", wifi.getActiveClient(), line.length());
            String response = protocol.processMessage(line);
            if (response.length() > 0) {
                dualOut.printf("TCP tx[%d]: %d bytes\r\n", wifi.getActiveClient(), response.length());
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

            // Debug: print first few pixels every 100 packets
            static uint32_t debugCounter = 0;
            if (++debugCounter % 100 == 1) {
                dualOut.printf("UDP: %d px, first=[%d,%d,%d]\r\n",
                    pixelsReceived, pixelBuffer[0], pixelBuffer[1], pixelBuffer[2]);
            }
        }
    } else {
        // Check if there's UDP data waiting but stream not active
        static uint32_t lastStreamWarn = 0;
        if (millis() - lastStreamWarn > 5000) {
            int pending = udpReceiver.isRunning() ? 1 : 0;  // Just check if receiver running
            if (pending && wifi.hasClient()) {
                dualOut.println("UDP: Stream not active, data may be waiting");
                lastStreamWarn = millis();
            }
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

    // Update telnet server and process commands
    if (telnet.update()) {
        terminal.processCommand(telnet.getLine());
    }
}
