/**
 * LTP ESP32-C3 OLED Sink
 *
 * ESP32-C3 based LTP sink displaying pixel data on a 72x40 I2C OLED:
 * - TCP control channel with JSON messages
 * - UDP data channel for pixel streaming
 * - mDNS service advertisement
 * - RGB-to-monochrome conversion for OLED display
 *
 * Hardware:
 * - ESP32-C3 (SuperMini or similar)
 * - 72x40 I2C OLED (SSD1306), address 0x3C
 * - SDA=GPIO5, SCL=GPIO6
 */

#include <Preferences.h>
#include <ArduinoJson.h>
#include "esp_system.h"
#include "config.h"
#include "telnet_server.h"
#include "oled_driver.h"
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

OledDriver oled;
LocalModes localModes(oled);
WiFiTransport wifi;
UdpReceiver udpReceiver;
SinkProtocol protocol;
UsbTerminal terminal;
TelnetServer telnet;

// Device ID (generated from MAC)
char deviceId[48];

// Last reset reason (captured at boot)
esp_reset_reason_t lastResetReason;

// Idle timeout tracking
uint32_t lastActivityTime = 0;
bool isIdle = false;

// Pixel buffer for UDP data (72*40*3 = 8640 bytes)
uint8_t pixelBuffer[OLED_TOTAL_PIXELS * 3];

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
    config.idleTimeout = preferences.getUShort("idleTimeout", DEFAULT_IDLE_TIMEOUT);
    config.localMode = preferences.getUChar("localMode", LOCAL_MODE_INFO);
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
    preferences.putUShort("idleTimeout", config.idleTimeout);
    preferences.putUChar("localMode", config.localMode);
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
    config.idleTimeout = DEFAULT_IDLE_TIMEOUT;
    config.localMode = LOCAL_MODE_INFO;
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
        localModes.start(config.localMode);
    }
}

void checkIdleTimeout() {
    if (config.idleTimeout == 0) return;

    uint32_t elapsed = (millis() - lastActivityTime) / 1000;

    if (!isIdle && elapsed >= config.idleTimeout) {
        isIdle = true;
        dualOut.println("Idle timeout: switching to blank");
        localModes.start(LOCAL_MODE_BLANK);
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
    } else if (strcmp(cmd, "info") == 0 || strcmp(cmd, "status") == 0) {
        cmdInfo();
    } else if (strcmp(cmd, "wifi") == 0) {
        cmdWifi(args);
    } else if (strcmp(cmd, "name") == 0) {
        cmdName(args);
    } else if (strcmp(cmd, "brightness") == 0) {
        cmdBrightness(args);
    } else if (strcmp(cmd, "contrast") == 0) {
        cmdContrast(args);
    } else if (strcmp(cmd, "mode") == 0) {
        cmdMode(args);
    } else if (strcmp(cmd, "timezone") == 0 || strcmp(cmd, "tz") == 0) {
        cmdTimezone(args);
    } else if (strcmp(cmd, "save") == 0) {
        cmdSave();
    } else if (strcmp(cmd, "reset") == 0) {
        cmdReset();
    } else if (strcmp(cmd, "reboot") == 0) {
        cmdReboot();
    } else {
        dualOut.printf("Unknown command: %s\r\n", cmd);
        dualOut.println("Type 'help' for available commands");
    }
}

void UsbTerminal::cmdHelp() {
    dualOut.println("Available commands:");
    dualOut.println("  wifi <ssid> <password>  - Set WiFi credentials");
    dualOut.println("  name <device_name>      - Set device name (max 16 chars)");
    dualOut.println("  brightness <0-255>      - Set OLED contrast");
    dualOut.println("  contrast <0-255>        - Alias for brightness");
    dualOut.println("  mode <0-3|255>          - Set local mode");
    dualOut.println("     0=blank, 1=info, 2=clock, 3=pattern, 255=cycle");
    dualOut.println("  timezone <tz_string>    - Set POSIX timezone");
    dualOut.println("  info                    - Show current status");
    dualOut.println("  save                    - Save config to NVS");
    dualOut.println("  reboot                  - Reboot the device");
    dualOut.println("  reset                   - Factory reset");
    dualOut.println("  help                    - Show this help");
}

void UsbTerminal::cmdInfo() {
    dualOut.println("=== Device Status ===");
    dualOut.printf("Name: %s\r\n", config->deviceName);
    dualOut.printf("Device ID: %s\r\n", deviceId);
    dualOut.printf("Firmware: %s\r\n", FIRMWARE_NAME);
    dualOut.printf("Git: %s  Build: %s\r\n", GIT_COMMIT, BUILD_DATE);
    dualOut.printf("Last Reset: %s\r\n", getResetReasonStr(lastResetReason));
    dualOut.printf("Uptime: %lu sec\r\n", millis() / 1000);
    dualOut.printf("Display: %dx%d OLED\r\n", OLED_WIDTH, OLED_HEIGHT);
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
    dualOut.printf("Local Mode: %d\r\n", config->localMode);
    dualOut.printf("Idle Timeout: %d sec\r\n", config->idleTimeout);
    dualOut.printf("Cycle Time: %d sec\r\n", config->cycleTime);
    dualOut.printf("Timezone: %s\r\n", config->timezone);
    struct tm timeinfo;
    if (getLocalTime(&timeinfo, 0)) {
        char timeBuf[32];
        strftime(timeBuf, sizeof(timeBuf), "%Y-%m-%d %H:%M:%S", &timeinfo);
        dualOut.printf("Current Time: %s\r\n", timeBuf);
    } else {
        dualOut.println("Current Time: not synced");
    }
    dualOut.println("--- Stream ---");
    dualOut.printf("Stream active: %s\r\n", protocol.isStreamActive() ? "YES" : "NO");
    dualOut.println("--- Stats ---");
    dualOut.printf("UDP Packets: %lu received, %lu dropped\r\n",
                  udpReceiver.getPacketsReceived(), udpReceiver.getPacketsDropped());
    dualOut.printf("Free heap: %lu bytes\r\n", (unsigned long)ESP.getFreeHeap());
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
    if (oled) oled->setContrast(bright);
    dualOut.printf("Brightness set to: %d\r\n", bright);
}

void UsbTerminal::cmdContrast(const char* args) {
    // Alias for brightness
    cmdBrightness(args);
}

void UsbTerminal::cmdMode(const char* args) {
    if (strlen(args) == 0) {
        dualOut.printf("Current mode: %d\r\n", config->localMode);
        dualOut.println("Modes: 0=blank, 1=info, 2=clock, 3=pattern, 255=cycle");
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

void UsbTerminal::cmdTimezone(const char* args) {
    if (strlen(args) == 0) {
        dualOut.printf("Current timezone: %s\r\n", config->timezone);
        dualOut.println("Usage: timezone <POSIX_TZ_string>");
        dualOut.println("Examples:");
        dualOut.println("  PST8PDT,M3.2.0,M11.1.0    (US Pacific)");
        dualOut.println("  EST5EDT,M3.2.0,M11.1.0     (US Eastern)");
        dualOut.println("  CST6CDT,M3.2.0,M11.1.0     (US Central)");
        dualOut.println("  UTC0                        (UTC)");

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

void UsbTerminal::cmdSave() {
    if (saveCallback) saveCallback();
}

void UsbTerminal::cmdReset() {
    dualOut.println("Resetting to factory defaults...");
    if (resetCallback) resetCallback();
}

void UsbTerminal::cmdReboot() {
    dualOut.println("Rebooting...");
    delay(100);  // Allow message to flush
    ESP.restart();
}

// ============================================================================
// Arduino Setup & Loop
// ============================================================================

// Get reset reason as string
const char* getResetReasonStr(esp_reset_reason_t reason) {
    switch (reason) {
        case ESP_RST_POWERON:   return "Power-on";
        case ESP_RST_EXT:       return "External reset";
        case ESP_RST_SW:        return "Software reset";
        case ESP_RST_PANIC:     return "Exception/panic";
        case ESP_RST_INT_WDT:   return "Interrupt watchdog";
        case ESP_RST_TASK_WDT:  return "Task watchdog";
        case ESP_RST_WDT:       return "Other watchdog";
        case ESP_RST_DEEPSLEEP: return "Deep sleep wake";
        case ESP_RST_BROWNOUT:  return "Brownout";
        case ESP_RST_SDIO:      return "SDIO";
        default:                return "Unknown";
    }
}

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(100);

    // Capture and print reset reason
    lastResetReason = esp_reset_reason();
    Serial.printf("Reset reason: %s\n", getResetReasonStr(lastResetReason));

    dualOut.println();
    dualOut.println("LTP ESP32-C3 OLED Sink starting...");

    // Load configuration
    loadConfig();

    // Generate device ID from MAC (WiFi must be initialized first)
    WiFi.mode(WIFI_STA);
    generateDeviceId();
    dualOut.printf("Device ID: %s\r\n", deviceId);

    // Initialize OLED display
    oled.begin();
    oled.setContrast(config.brightness);

    // Show boot screen
    oled.clearBuffer();
    oled.setFont(u8g2_font_5x7_tr);
    oled.drawText(0, 7, config.deviceName);
    oled.drawText(0, 16, "Connecting...");
    oled.sendBuffer();

    // Start local mode (default to INFO)
    localModes.setCycleTime(config.cycleTime);
    localModes.start(config.localMode);

    // Initialize WiFi
    if (strlen(config.wifiSsid) > 0) {
        wifi.begin(config.wifiSsid, config.wifiPassword, config.deviceName);
    } else {
        dualOut.println("WiFi: No credentials configured. Use terminal to set.");
    }

    // Initialize protocol handler (UDP receiver started when WiFi connects)
    protocol.begin(&config, &oled, 5001);
    protocol.setLocalModeCallback([](uint8_t mode) {
        localModes.start(mode);
    });
    protocol.setCycleTimeCallback([](uint16_t seconds) {
        localModes.setCycleTime(seconds);
    });
    protocol.setBrightnessCallback([](uint8_t brightness) {
        oled.setContrast(brightness);
    });
    protocol.setSaveCallback(saveConfig);
    protocol.setRebootCallback([]() {
        dualOut.println("Rebooting...");
        delay(100);
        ESP.restart();
    });

    // Initialize USB terminal
    terminal.begin(&config, &wifi, &oled);
    terminal.setSaveCallback(saveConfig);
    terminal.setResetCallback(resetConfig);

    lastActivityTime = millis();

    dualOut.println("Initialization complete!");
    dualOut.printf("Display: %dx%d OLED (%d pixels)\r\n",
                   OLED_WIDTH, OLED_HEIGHT, OLED_TOTAL_PIXELS);
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
        wifi.startMdns(deviceId, config.deviceName, OLED_TOTAL_PIXELS, "rgb", 15);

        // Start telnet server
        telnet.begin();

        // Start NTP time sync
        configTzTime(config.timezone, CLOCK_NTP_SERVER);
        dualOut.printf("NTP: Syncing with TZ=%s\r\n", config.timezone);

        // Update OLED with IP address
        oled.clearBuffer();
        oled.setFont(u8g2_font_5x7_tr);
        oled.drawText(0, 7, config.deviceName);
        oled.drawText(0, 16, WiFi.localIP().toString().c_str());
        oled.drawText(0, 25, "Ready");
        oled.sendBuffer();
        delay(1000);  // Show IP briefly

        networkStarted = true;
    }

    // Handle client connection/disconnection
    bool hasClient = wifi.hasClient();
    if (hadClient && !hasClient) {
        // Client disconnected - stop stream
        protocol.stopStream();
        dualOut.println("Client disconnected, stream stopped");
        // Resume local mode
        localModes.start(config.localMode);
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
        uint16_t pixelsReceived = udpReceiver.receive(pixelBuffer, OLED_TOTAL_PIXELS);
        if (pixelsReceived > 0) {
            // Stop local mode and apply pixels
            localModes.stop();
            resetActivityTimer();

            // Convert RGB to monochrome and display
            oled.drawPixels(pixelBuffer, pixelsReceived);

            // Debug: print stats every 100 packets
            static uint32_t debugCounter = 0;
            if (++debugCounter % 100 == 1) {
                dualOut.printf("UDP: %d px, first=[%d,%d,%d]\r\n",
                    pixelsReceived, pixelBuffer[0], pixelBuffer[1], pixelBuffer[2]);
            }
        }
    }

    // Update local mode animation
    localModes.update();

    // Check idle timeout
    checkIdleTimeout();

    // Update USB terminal
    terminal.update();

    // Update telnet server and process commands
    if (telnet.update()) {
        terminal.processCommand(telnet.getLine());
    }

    yield();
}
