/**
 * LTP Stream (ESP32) - pattern-only LED controller on WiFi
 *
 * An LTP sink (mDNS _ltp-sink._tcp, JSON control channel on TCP) that shows
 * its own patterns, chosen and tuned through controls, and takes no pixel
 * stream. Every pixel is calculated while the frame goes out, so there is no
 * frame buffer and the pixel count is not limited by RAM.
 *
 *   - Patterns and their maths:   libraries/LtpStreamFx/src/ltp_fx.cpp
 *   - Controls and settings:      libraries/LtpStreamFx/src/ltp_fx_controls.h
 *   - Pins, limits:               config.h
 *   - LED output (RMT, SPI):      led_output.h
 *   - JSON control channel:       sink_protocol.h
 *
 * Set WiFi up over USB: `wifi <ssid> <password>`, `save`, `reboot`.
 */

#include <Preferences.h>
#include <ArduinoJson.h>
#include "esp_system.h"
#include "config.h"
#include <ltp_fx.h>

#define FXC_MAX_PIXELS    MAX_PIXELS
#define FXC_DEFAULT_NAME  DEVICE_NAME_DEFAULT
#define FXC_HAS_APA102    1
#define FXC_HAS_LPD8806   0
#include <ltp_fx_controls.h>

#include "build_info.h"
#include "telnet_server.h"
#include "led_output.h"
#include "wifi_transport.h"
#include "sink_protocol.h"
#include "usb_terminal.h"

// Global dual output (Serial + telnet)
DualPrint dualOut;

// ============================================================================
// STATE
// ============================================================================

DeviceConfig deviceConfig;
Preferences preferences;

LedOutput leds;
WiFiTransport wifi;
SinkProtocol protocol;
UsbTerminal terminal;
TelnetServer telnet;

static uint16_t sinceFrameMs;      // time since the last frame started
static uint16_t sinceRefreshMs;    // time since a static frame was last sent
static uint16_t fpsWindowMs;       // frame-rate measurement window so far
static uint16_t fpsWindowFrames;
static uint32_t framesDisplayed;
static uint32_t framesFailed;      // frames the output could not send whole
static bool frameDirty = true;     // a setting changed since the last frame

#define FPS_WINDOW_MS 1000

// Time to let the reply to a reboot request leave before restarting.
#define REBOOT_DELAY_MS 200

// ============================================================================
// SETTINGS STORAGE (NVS)
// ============================================================================

void saveConfig() {
    fxConfig.magic = FXC_MAGIC;
    fxConfig.version = FXC_VERSION;
    preferences.begin(NVS_NAMESPACE, false);
    preferences.putBytes("fx", &fxConfig, sizeof(fxConfig));
    preferences.putString("ssid", deviceConfig.wifiSsid);
    preferences.putString("password", deviceConfig.wifiPassword);
    preferences.putUChar("authEn", deviceConfig.authEnabled);
    preferences.putUChar("authRdOpen", deviceConfig.authReadOpen);
    preferences.putBytes("authPsk", deviceConfig.authPsk, sizeof(deviceConfig.authPsk));
    preferences.end();
    dualOut.println("Config saved to NVS");
}

void loadConfig() {
    memset(&deviceConfig, 0, sizeof(deviceConfig));
    deviceConfig.authReadOpen = 1;
    fxcDefaults();

    preferences.begin(NVS_NAMESPACE, true);
    FxConfig stored;
    if (preferences.getBytes("fx", &stored, sizeof(stored)) == sizeof(stored)
        && fxcStoredValid(stored)) {
        fxConfig = stored;
    }
    preferences.getString("ssid", deviceConfig.wifiSsid, sizeof(deviceConfig.wifiSsid));
    preferences.getString("password", deviceConfig.wifiPassword, sizeof(deviceConfig.wifiPassword));
    deviceConfig.authEnabled = preferences.getUChar("authEn", 0);
    deviceConfig.authReadOpen = preferences.getUChar("authRdOpen", 1);
    if (preferences.getBytes("authPsk", deviceConfig.authPsk, sizeof(deviceConfig.authPsk))
        != sizeof(deviceConfig.authPsk)) {
        memset(deviceConfig.authPsk, 0, sizeof(deviceConfig.authPsk));
    }
    preferences.end();

    fxcSanitize();
    fxcRestartCycle();
    frameDirty = true;
}

// Factory reset of the pattern settings. WiFi and auth are kept: losing them
// would take the device off the network it is being reset over.
void resetConfig() {
    uint16_t oldPixels = fxConfig.numPixels;
    uint8_t oldLedType = fxConfig.ledType;
    fxcDefaults();
    fxcSanitize();
    fxcRestartCycle();
    fxcRequestBlank(oldPixels, oldLedType);
    saveConfig();
    frameDirty = true;
    dualOut.println("Pattern settings reset to defaults");
}

// ============================================================================
// FRAMES
// ============================================================================

// Milliseconds since the previous call.
static uint16_t elapsedMs() {
    static uint32_t last = micros();
    static uint32_t pendingUs;
    uint32_t now = micros();
    pendingUs += now - last;
    last = now;
    uint32_t ms = pendingUs / 1000;
    pendingUs -= ms * 1000;
    return ms > 0xFFFF ? 0xFFFF : (uint16_t)ms;
}

// An effect whose frame is the same every time needs sending only when a
// setting changes.
static bool isStaticEffect(uint8_t effect) {
    return effect == FX_OFF || effect == FX_SOLID;
}

static void renderFrame() {
    uint8_t order[3];

    // LEDs left lit on the output we just switched away from.
    if (fxRuntime.blankTo && fxRuntime.blankLedType != fxConfig.ledType) {
        fxcWireOrder(fxRuntime.blankLedType, order);
        leds.show(fxRuntime.blankLedType, 0, fxRuntime.blankTo, order);
        fxRuntime.blankTo = 0;
    }

    // If the strip got shorter, send black to the pixels dropped off the end.
    uint16_t total = fxConfig.numPixels;
    if (fxRuntime.blankTo > total) total = fxRuntime.blankTo;

    fxcBeginFrame();
    fxStart();
    fxcWireOrder(fxConfig.ledType, order);
    if (leds.show(fxConfig.ledType, fxConfig.numPixels, total, order)) {
        fxRuntime.blankTo = 0;
        frameDirty = false;
    } else {
        framesFailed++;
    }
    framesDisplayed++;
    fpsWindowFrames++;
}

static void updateFrames(uint16_t elapsed) {
    sinceFrameMs += elapsed;
    sinceRefreshMs += elapsed;

    fpsWindowMs += elapsed;
    if (fpsWindowMs >= FPS_WINDOW_MS) {
        fxRuntime.fps = (uint16_t)(((uint32_t)fpsWindowFrames * 1000) / fpsWindowMs);
        fpsWindowMs = 0;
        fpsWindowFrames = 0;
    }

    uint16_t interval = 1000 / (fxConfig.maxFps ? fxConfig.maxFps : 1);
    if (sinceFrameMs < interval) return;

    if (isStaticEffect(fxcCurrentEffect()) && !frameDirty && sinceRefreshMs < STATIC_REFRESH_MS) {
        return;
    }
    sinceFrameMs = 0;
    sinceRefreshMs = 0;
    renderFrame();
}

// A setting changed, by whichever route: redraw, and keep the advertisement
// in step with the strip's size and name.
static void settingsChanged() {
    frameDirty = true;
    wifi.updateMdns(fxConfig.name, fxConfig.numPixels, fxConfig.matrixWidth, fxConfig.maxFps);
}

// ============================================================================
// USB TERMINAL COMMANDS
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

    if (strcmp(cmd, "help") == 0) cmdHelp();
    else if (strcmp(cmd, "info") == 0 || strcmp(cmd, "status") == 0) cmdInfo();
    else if (strcmp(cmd, "controls") == 0) cmdControls();
    else if (strcmp(cmd, "get") == 0) cmdGet(args);
    else if (strcmp(cmd, "set") == 0) cmdSet(args);
    else if (strcmp(cmd, "wifi") == 0) cmdWifi(args);
    else if (strcmp(cmd, "name") == 0) cmdName(args);
    else if (strcmp(cmd, "save") == 0) saveConfig();
    else if (strcmp(cmd, "reset") == 0) resetConfig();
    else if (strcmp(cmd, "reboot") == 0) {
        dualOut.println("Rebooting...");
        delay(REBOOT_DELAY_MS);
        ESP.restart();
    }
    else if (strcmp(cmd, "auth-key") == 0) cmdAuthKey(args);
    else if (strcmp(cmd, "auth-off") == 0) cmdAuthOff();
    else if (strcmp(cmd, "pair") == 0) cmdPair();
    else {
        dualOut.printf("Unknown command: %s\r\n", cmd);
        dualOut.println("Type 'help' for available commands");
    }
}

void UsbTerminal::cmdHelp() {
    dualOut.println("Available commands:");
    dualOut.println("  wifi <ssid> <password>  - Set WiFi credentials");
    dualOut.println("  name <device_name>      - Set device name (max 15 chars)");
    dualOut.println("  controls                - List every control and its value");
    dualOut.println("  get <control>           - Show one control");
    dualOut.println("  set <control> <value>   - Change one control, e.g. set local_mode 3");
    dualOut.println("  info                    - Show current status");
    dualOut.println("  auth-key <32 hex>       - Set Layer 2 PSK, require claim");
    dualOut.println("  auth-off                - Disable Layer 2 auth (open)");
    dualOut.println("  pair                    - Arm X25519+PIN pairing (PIN shown here)");
    dualOut.println("  save                    - Save settings to flash");
    dualOut.println("  reboot                  - Reboot the device");
    dualOut.println("  reset                   - Reset pattern settings to defaults");
    dualOut.println("  help                    - Show this help");
}

static int findControlByName(const char* name) {
    for (uint8_t i = 0; i < FXC_NUM_CONTROLS; i++) {
        if (strcmp(fxControlDefs[i].name, name) == 0) return i;
    }
    return -1;
}

static void printControl(const FxControlDef& def) {
    if (def.type == FXC_TYPE_ACTION) {
        dualOut.printf("  %-14s (action)  %s\r\n", def.name, def.description);
        return;
    }
    uint16_t value = fxcGetDef(def);
    dualOut.printf("  %-14s %5u  %s", def.name, (unsigned)value, def.description);
    for (uint8_t j = 0; j < def.numOptions; j++) {
        if (def.options[j].value == value) dualOut.printf("  [%s]", def.options[j].label);
    }
    dualOut.println();
}

void UsbTerminal::cmdControls() {
    for (uint8_t i = 0; i < FXC_NUM_CONTROLS; i++) printControl(fxControlDefs[i]);
}

void UsbTerminal::cmdGet(const char* args) {
    int index = findControlByName(args);
    if (index < 0) {
        dualOut.println("Usage: get <control>   ('controls' lists them)");
        return;
    }
    const FxControlDef& def = fxControlDefs[index];
    printControl(def);
    for (uint8_t j = 0; j < def.numOptions; j++) {
        dualOut.printf("      %3u = %s\r\n", def.options[j].value, def.options[j].label);
    }
}

void UsbTerminal::cmdSet(const char* args) {
    char name[24];
    const char* space = strchr(args, ' ');
    size_t nameLen = space ? (size_t)(space - args) : strlen(args);
    if (nameLen == 0 || nameLen > sizeof(name) - 1) {
        dualOut.println("Usage: set <control> <value>");
        return;
    }
    memcpy(name, args, nameLen);
    name[nameLen] = '\0';
    int index = findControlByName(name);
    if (index < 0) {
        dualOut.printf("Unknown control: %s  ('controls' lists them)\r\n", name);
        return;
    }
    const FxControlDef& def = fxControlDefs[index];
    long value = space ? atol(space + 1) : 1;
    uint8_t result = (value < 0 || value > 65535) ? (uint8_t)FXC_ERR_VALUE
                                                  : fxcSet(def.id, (uint16_t)value);
    switch (result) {
        case FXC_OK:
            settingsChanged();
            printControl(def);
            break;
        case FXC_DO_SAVE:
            saveConfig();
            break;
        case FXC_DO_REBOOT:
            dualOut.println("Rebooting...");
            delay(REBOOT_DELAY_MS);
            ESP.restart();
            break;
        case FXC_ERR_READONLY:
            dualOut.println("That control is read-only");
            break;
        default:
            dualOut.printf("Value not accepted (range %d-%d", def.minVal, def.maxVal);
            dualOut.println(def.numOptions ? ", see 'get' for the options)" : ")");
            break;
    }
}

void UsbTerminal::cmdInfo() {
    dualOut.println("=== Device Status ===");
    dualOut.printf("Name: %s\r\n", fxConfig.name);
    dualOut.printf("Device ID: %s\r\n", protocol.deviceId());
    dualOut.printf("Firmware: %s\r\n", FIRMWARE_NAME);
    dualOut.printf("Git: %s  Build: %s\r\n", GIT_COMMIT, BUILD_DATE);
    dualOut.printf("Uptime: %lu sec\r\n", millis() / 1000);
    dualOut.printf("WiFi SSID: %s\r\n", deviceConfig.wifiSsid);
    dualOut.printf("Auth: %s\r\n", deviceConfig.authEnabled ? "Level 2 (siphash)" : "Level 0 (open)");
    if (wifi.isWifiConnected()) {
        dualOut.printf("IP Address: %s\r\n", wifi.getIP().toString().c_str());
        dualOut.printf("Signal: %d dBm\r\n", (int)wifi.getRSSI());
        dualOut.printf("Control Port: %d (TCP)\r\n", wifi.getPort());
    } else {
        dualOut.println("WiFi: not connected");
    }
    dualOut.println("--- LEDs ---");
    dualOut.printf("Pixels: %u", fxConfig.numPixels);
    if (fxHeight() > 1) dualOut.printf(" (%u x %u)", fxWidth(), fxHeight());
    dualOut.println();
    dualOut.printf("Output: %s, WS2812 pin %d, APA102 data %d clock %d\r\n",
                   fxConfig.ledType == FXLED_APA102 ? "APA102" : "WS2812",
                   WS2812_PIN, (int)APA102_DATA_PIN, (int)APA102_CLOCK_PIN);
    dualOut.printf("Frames: %lu sent, %lu failed, %u per second\r\n",
                   (unsigned long)framesDisplayed, (unsigned long)framesFailed, fxRuntime.fps);
    dualOut.printf("Free heap: %lu bytes\r\n", (unsigned long)ESP.getFreeHeap());
    dualOut.println("Type 'controls' for the pattern settings");
}

void UsbTerminal::cmdWifi(const char* args) {
    if (strlen(args) == 0) {
        dualOut.println("Usage: wifi <ssid> <password>");
        dualOut.printf("Current SSID: %s\r\n", deviceConfig.wifiSsid);
        return;
    }
    const char* space = strchr(args, ' ');
    size_t ssidLen = space ? (size_t)(space - args) : strlen(args);
    if (ssidLen > sizeof(deviceConfig.wifiSsid) - 1) ssidLen = sizeof(deviceConfig.wifiSsid) - 1;
    memcpy(deviceConfig.wifiSsid, args, ssidLen);
    deviceConfig.wifiSsid[ssidLen] = '\0';
    strncpy(deviceConfig.wifiPassword, space ? space + 1 : "", sizeof(deviceConfig.wifiPassword) - 1);
    deviceConfig.wifiPassword[sizeof(deviceConfig.wifiPassword) - 1] = '\0';

    dualOut.printf("WiFi credentials set: %s\r\n", deviceConfig.wifiSsid);
    dualOut.println("Use 'save' to persist, then 'reboot' to connect");
}

void UsbTerminal::cmdName(const char* args) {
    if (strlen(args) == 0) {
        dualOut.printf("Current name: %s\r\n", fxConfig.name);
        return;
    }
    fxcSetName((const uint8_t*)args, strlen(args));
    settingsChanged();
    dualOut.printf("Device name set to: %s\r\n", fxConfig.name);
    dualOut.println("Use 'save' to persist");
}

// Set the Layer 2 pre-shared key over USB: physical access is already full
// trust, and the key never crosses the network in the clear.
void UsbTerminal::cmdAuthKey(const char* args) {
    if (strlen(args) != 32) {
        dualOut.println("Usage: auth-key <32 hex chars>  (16-byte PSK)");
        dualOut.println("Generate one with: openssl rand -hex 16");
        return;
    }
    uint8_t key[16];
    for (int i = 0; i < 16; i++) {
        char b[3] = { args[i * 2], args[i * 2 + 1], 0 };
        char* endp = nullptr;
        long v = strtol(b, &endp, 16);
        if (endp == b || *endp != '\0') {
            dualOut.println("auth-key: invalid hex");
            return;
        }
        key[i] = (uint8_t)v;
    }
    memcpy(deviceConfig.authPsk, key, 16);
    deviceConfig.authEnabled = 1;
    dualOut.println("Auth key set. Device now requires a claim (Level 2).");
    dualOut.println("Run 'save' to persist, then 'reboot'. Add the SAME key to");
    dualOut.println("the controller keystore under this device's UUID.");
}

void UsbTerminal::cmdAuthOff() {
    deviceConfig.authEnabled = 0;
    memset(deviceConfig.authPsk, 0, sizeof(deviceConfig.authPsk));
    dualOut.println("Auth disabled (Level 0). Run 'save' then 'reboot'.");
}

// Arm X25519 + PIN pairing. There is no display, so the PIN appears here on
// the console; the controller then pairs over the network. One attempt, and
// the window closes by itself (PAIR_WINDOW_MS).
void UsbTerminal::cmdPair() {
    char pin[9];
    if (!protocol.pairing().arm(pin)) {
        dualOut.println("pair: failed to generate pairing material");
        return;
    }
    dualOut.println("=== Pairing armed (120s, one attempt) ===");
    dualOut.printf("PIN: %s\r\n", pin);
    dualOut.println("In the controller web UI, click 'Pair (ECDH)' on this");
    dualOut.println("sink and enter the PIN. On success the key is saved and");
    dualOut.println("Level 2 auth turns on automatically.");
}

// ============================================================================
// SETUP AND LOOP
// ============================================================================

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(100);

    dualOut.println();
    dualOut.println("LTP Stream (ESP32) starting...");

    loadConfig();

    if (!leds.begin()) {
        dualOut.println("LED output: failed to start the RMT channel");
    }
    renderFrame();

    // The device id comes from the MAC, which needs WiFi initialised.
    WiFi.mode(WIFI_STA);
    protocol.begin(&deviceConfig);
    dualOut.printf("Device ID: %s\r\n", protocol.deviceId());

    if (strlen(deviceConfig.wifiSsid) > 0) {
        wifi.begin(deviceConfig.wifiSsid, deviceConfig.wifiPassword, fxConfig.name);
    } else {
        dualOut.println("WiFi: No credentials configured. Use: wifi <ssid> <password>");
    }

    terminal.begin();
    dualOut.printf("%u pixels, pattern %u\r\n", fxConfig.numPixels, fxConfig.mode);
}

void loop() {
    uint16_t elapsed = elapsedMs();
    fxcTick(elapsed);

    wifi.update();

    // Advertise once WiFi is up.
    static bool networkStarted = false;
    if (wifi.isWifiConnected() && !networkStarted) {
        wifi.startMdns(protocol.deviceId(), fxConfig.name, fxConfig.numPixels,
                       fxConfig.matrixWidth, fxConfig.maxFps,
                       protocol.auth().isEnabled() ? "siphash" : "none");
#if LTP_ENABLE_TELNET
        telnet.begin();
#else
        dualOut.println("Telnet: disabled (LTP_ENABLE_TELNET=0)");
#endif
        networkStarted = true;
    }

    // Control channel
    if (wifi.hasClient()) {
        String line = wifi.readLine();
        if (line.length() > 0) {
            String peerIp = wifi.getActiveClientIp();
            String response = protocol.processMessage(line, peerIp.c_str());
            if (response.length() > 0) wifi.send(response);
            if (protocol.takeSettingsChanged()) settingsChanged();
            if (protocol.takeRebootRequest()) {
                dualOut.println("Rebooting...");
                delay(REBOOT_DELAY_MS);
                ESP.restart();
            }
        }
    }

    updateFrames(elapsed);

    terminal.update();
#if LTP_ENABLE_TELNET
    if (telnet.update()) {
        terminal.processCommand(telnet.getLine());
    }
#endif

    yield();
}
