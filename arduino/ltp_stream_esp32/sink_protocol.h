/**
 * LTP Stream (ESP32) - Protocol Handler
 *
 * The LTP sink JSON control channel (spec/protocol.md): newline-delimited
 * JSON over TCP. This device answers capability and control messages like
 * any sink, with its control list built from the shared table in
 * ltp_fx_controls.h. It refuses stream setup: it generates its own patterns
 * and has nowhere to put a pixel stream.
 *
 * Device auth (claim) and X25519 + PIN pairing are those of ltp_esp32c3_oled.
 */

#ifndef SINK_PROTOCOL_H
#define SINK_PROTOCOL_H

#include <Arduino.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include "config.h"
#include "build_info.h"
#include "device_auth.h"
#include "device_pairing.h"

// Defined in the .ino; needed here to persist the key after a successful pair.
extern void saveConfig();

// Message types (matching Python libltp.types.MessageType)
#define MSG_CAPABILITY_REQUEST      "capability_request"
#define MSG_CAPABILITY_RESPONSE     "capability_response"
#define MSG_STREAM_SETUP            "stream_setup"
#define MSG_STREAM_SETUP_RESPONSE   "stream_setup_response"
#define MSG_STREAM_CONTROL          "stream_control"
#define MSG_STREAM_CONTROL_RESPONSE "stream_control_response"
#define MSG_CONTROL_GET             "control_get"
#define MSG_CONTROL_GET_RESPONSE    "control_get_response"
#define MSG_CONTROL_SET             "control_set"
#define MSG_CONTROL_SET_RESPONSE    "control_set_response"
#define MSG_ERROR                   "error"

// X25519 + PIN pairing (protocol 0.3)
#define MSG_PAIR_BEGIN              "pair_begin"
#define MSG_PAIR_CONFIRM            "pair_confirm"

// Error codes of spec/protocol.md, Appendix C.
#define LTP_ERR_INVALID_FORMAT      1
#define LTP_ERR_NOT_FOUND           4

#define NO_PIXEL_STREAM_MESSAGE \
    "This device generates its own patterns and does not accept a pixel stream"

class SinkProtocol {
public:
    void begin(DeviceConfig* cfg) {
        config = cfg;
        makeDeviceId(deviceIdStr);
        // Arm the Layer 2 guard from stored config (inert if not enabled).
        authGuard.begin(cfg->authEnabled ? cfg->authPsk : nullptr, deviceIdStr,
                        cfg->authReadOpen != 0);
        pairingObj.setDeviceId(deviceIdStr);
    }

    DeviceAuthGuard& auth() { return authGuard; }
    DevicePairing& pairing() { return pairingObj; }
    const char* deviceId() const { return deviceIdStr; }

    // True once after a control was changed over the network.
    bool takeSettingsChanged() {
        bool changed = settingsChanged;
        settingsChanged = false;
        return changed;
    }

    // True once after the reboot control was set; the caller restarts after
    // the reply has been sent.
    bool takeRebootRequest() {
        bool requested = rebootRequested;
        rebootRequested = false;
        return requested;
    }

    // Process a JSON message line, returns the reply line. peerIp identifies
    // the sender for the auth guard; may be nullptr.
    String processMessage(const String& jsonLine, const char* peerIp = nullptr) {
        JsonDocument doc;
        DeserializationError error = deserializeJson(doc, jsonLine);
        if (error) {
            dualOut.printf("JSON parse error: %s\r\n", error.c_str());
            return buildError(0, LTP_ERR_INVALID_FORMAT, "INVALID_FORMAT", "JSON parse error");
        }

        const char* msgType = doc["type"];
        int seq = doc["seq"] | 0;
        if (!msgType) {
            return buildError(seq, LTP_ERR_INVALID_FORMAT, "INVALID_FORMAT", "Missing message type");
        }

        // Pairing runs before the auth guard: it has to work while the device
        // is still unkeyed, because that is how the first key gets installed.
        if (strcmp(msgType, MSG_PAIR_BEGIN) == 0) {
            return pairingObj.handleBegin(seq, doc);
        }
        if (strcmp(msgType, MSG_PAIR_CONFIRM) == 0) {
            uint8_t newPsk[16];
            bool paired = false;
            String resp = pairingObj.handleConfirm(seq, doc, newPsk, paired);
            if (paired) {
                memcpy(config->authPsk, newPsk, 16);
                config->authEnabled = 1;
                saveConfig();
                authGuard.begin(config->authPsk, deviceIdStr, config->authReadOpen != 0);
                dualOut.println("Paired: Layer 2 key installed, auth enabled");
            }
            memset(newPsk, 0, sizeof(newPsk));
            return resp;
        }

        // Layer 2 guard: consumes handshake messages and gates the commands
        // that change state, once auth is enabled.
        bool privileged =
            strcmp(msgType, MSG_CONTROL_SET) == 0 ||
            strcmp(msgType, MSG_STREAM_SETUP) == 0 ||
            strcmp(msgType, MSG_STREAM_CONTROL) == 0;
        String guardResp;
        if (authGuard.handle(msgType, seq, doc, peerIp, privileged, guardResp)) {
            return guardResp;
        }

        if (strcmp(msgType, MSG_CAPABILITY_REQUEST) == 0) return handleCapabilityRequest(seq);
        if (strcmp(msgType, MSG_CONTROL_GET) == 0) return handleControlGet(seq, doc);
        if (strcmp(msgType, MSG_CONTROL_SET) == 0) return handleControlSet(seq, doc);
        if (strcmp(msgType, MSG_STREAM_SETUP) == 0) {
            return refuseStream(seq, MSG_STREAM_SETUP_RESPONSE);
        }
        if (strcmp(msgType, MSG_STREAM_CONTROL) == 0) {
            return refuseStream(seq, MSG_STREAM_CONTROL_RESPONSE);
        }
        return buildError(seq, LTP_ERR_NOT_FOUND, "NOT_FOUND", "Unknown message type");
    }

private:
    DeviceConfig* config = nullptr;
    DeviceAuthGuard authGuard;
    DevicePairing pairingObj;
    char deviceIdStr[48];
    bool settingsChanged = false;
    bool rebootRequested = false;

    // The device UUID, built from the WiFi MAC so it is stable per board.
    static void makeDeviceId(char* out) {
        uint8_t mac[6];
        WiFi.macAddress(mac);
        snprintf(out, 48,
                 "%02x%02x%02x%02x-%02x%02x-4000-8000-%02x%02x%02x%02x%02x%02x",
                 mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
                 mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    }

    static String finish(JsonDocument& doc) {
        String response;
        serializeJson(doc, response);
        response += "\n";
        return response;
    }

    // "local_mode" -> "Local Mode", for the control's display name.
    static String displayName(const char* id) {
        String name;
        bool startOfWord = true;
        for (const char* p = id; *p; p++) {
            if (*p == '_') {
                name += ' ';
                startOfWord = true;
            } else {
                name += startOfWord ? (char)toupper((unsigned char)*p) : *p;
                startOfWord = false;
            }
        }
        return name;
    }

    static int findControl(const char* id) {
        for (uint8_t i = 0; i < FXC_NUM_CONTROLS; i++) {
            if (strcmp(fxControlDefs[i].name, id) == 0) return i;
        }
        return -1;
    }

    static void putValue(JsonObject target, const char* key, const FxControlDef& def) {
        uint16_t value = fxcGetDef(def);
        if (def.type == FXC_TYPE_BOOL) target[key] = value != 0;
        else target[key] = value;
    }

    String handleCapabilityRequest(int seq) {
        JsonDocument doc;
        doc["type"] = MSG_CAPABILITY_RESPONSE;
        if (seq > 0) doc["seq"] = seq;

        JsonObject device = doc["device"].to<JsonObject>();
        device["id"] = deviceIdStr;
        device["name"] = fxConfig.name;
        device["description"] = DEVICE_DESCRIPTION;

        bool matrix = fxHeight() > 1;
        device["type"] = matrix ? "matrix" : "string";
        device["pixels"] = fxConfig.numPixels;
        JsonArray dims = device["dimensions"].to<JsonArray>();
        JsonObject topology = device["topology"].to<JsonObject>();
        topology["topology"] = matrix ? "matrix" : "linear";
        JsonArray topoDims = topology["dimensions"].to<JsonArray>();
        dims.add(fxWidth());
        topoDims.add(fxWidth());
        if (matrix) {
            dims.add(fxHeight());
            topoDims.add(fxHeight());
        }

        JsonArray colorFormats = device["color_formats"].to<JsonArray>();
        colorFormats.add("rgb");
        device["preferred_format"] = "rgb";
        device["max_refresh_hz"] = fxConfig.maxFps;
        device["protocol_version"] = PROTOCOL_VERSION;
        // Not a field older controllers know: it says why stream_setup fails.
        device["pixel_input"] = false;

        JsonObject authObj = device["auth"].to<JsonObject>();
        authGuard.fillAuthInfo(authObj);

        device["firmware_name"] = FIRMWARE_NAME;
        device["git_commit"] = GIT_COMMIT;
        device["build_date"] = BUILD_DATE;

        JsonArray controls = device["controls"].to<JsonArray>();
        for (uint8_t i = 0; i < FXC_NUM_CONTROLS; i++) {
            const FxControlDef& def = fxControlDefs[i];
            JsonObject control = controls.add<JsonObject>();
            control["id"] = def.name;
            control["name"] = displayName(def.name);
            control["description"] = def.description;
            if (def.flags & FXC_FLAG_READONLY) control["readonly"] = true;
            switch (def.type) {
                case FXC_TYPE_ACTION:
                    control["type"] = "action";
                    break;
                case FXC_TYPE_BOOL:
                    control["type"] = "boolean";
                    putValue(control, "value", def);
                    break;
                case FXC_TYPE_ENUM: {
                    control["type"] = "enum";
                    putValue(control, "value", def);
                    JsonArray options = control["options"].to<JsonArray>();
                    for (uint8_t j = 0; j < def.numOptions; j++) {
                        JsonObject option = options.add<JsonObject>();
                        option["value"] = String(def.options[j].value);
                        option["label"] = def.options[j].label;
                    }
                    break;
                }
                default:
                    control["type"] = "number";
                    putValue(control, "value", def);
                    control["min"] = def.minVal;
                    control["max"] = def.maxVal;
                    control["step"] = 1;
                    break;
            }
        }
        return finish(doc);
    }

    String handleControlGet(int seq, JsonDocument& doc) {
        JsonDocument resp;
        resp["type"] = MSG_CONTROL_GET_RESPONSE;
        if (seq > 0) resp["seq"] = seq;
        resp["status"] = "ok";
        JsonObject values = resp["values"].to<JsonObject>();

        JsonArray ids = doc["ids"];
        if (ids.size() > 0) {
            for (JsonVariant id : ids) {
                const char* idStr = id.as<const char*>();
                int index = idStr ? findControl(idStr) : -1;
                if (index >= 0) putValue(values, fxControlDefs[index].name, fxControlDefs[index]);
            }
        } else {
            for (uint8_t i = 0; i < FXC_NUM_CONTROLS; i++) {
                const FxControlDef& def = fxControlDefs[i];
                if (def.type != FXC_TYPE_ACTION) putValue(values, def.name, def);
            }
        }
        return finish(resp);
    }

    String handleControlSet(int seq, JsonDocument& doc) {
        JsonDocument resp;
        resp["type"] = MSG_CONTROL_SET_RESPONSE;
        if (seq > 0) resp["seq"] = seq;
        JsonObject applied = resp["applied"].to<JsonObject>();
        JsonObject errors = resp["errors"].to<JsonObject>();

        bool hasErrors = false;
        JsonObject values = doc["values"];
        for (JsonPair kv : values) {
            const char* id = kv.key().c_str();
            int index = findControl(id);
            float number = kv.value().as<float>();
            uint8_t result = FXC_ERR_UNKNOWN;
            if (index >= 0 && number >= 0 && number <= 65535) {
                result = fxcSet(fxControlDefs[index].id, (uint16_t)number);
            } else if (index >= 0) {
                result = FXC_ERR_VALUE;
            }
            switch (result) {
                case FXC_DO_SAVE:
                    saveConfig();
                    applied[id] = true;
                    break;
                case FXC_DO_REBOOT:
                    rebootRequested = true;
                    applied[id] = true;
                    break;
                case FXC_OK:
                    putValue(applied, fxControlDefs[index].name, fxControlDefs[index]);
                    settingsChanged = true;
                    dualOut.printf("Protocol: %s=%u\r\n", id, (unsigned)fxcGetDef(fxControlDefs[index]));
                    break;
                case FXC_ERR_READONLY:
                    errors[id] = "Control is read-only";
                    hasErrors = true;
                    break;
                case FXC_ERR_VALUE:
                    errors[id] = "Value out of range";
                    hasErrors = true;
                    break;
                default:
                    errors[id] = "Unknown control";
                    hasErrors = true;
                    break;
            }
        }
        resp["status"] = hasErrors ? "partial" : "ok";
        return finish(resp);
    }

    // stream_setup and stream_control: answered in their own reply type with
    // a status the controller reports as a failed route, not a dead sink.
    String refuseStream(int seq, const char* replyType) {
        JsonDocument resp;
        resp["type"] = replyType;
        if (seq > 0) resp["seq"] = seq;
        resp["status"] = "error";
        resp["error"] = "NOT_SUPPORTED";
        resp["message"] = NO_PIXEL_STREAM_MESSAGE;
        return finish(resp);
    }

    String buildError(int seq, int code, const char* error, const char* message) {
        JsonDocument doc;
        doc["type"] = MSG_ERROR;
        if (seq > 0) doc["seq"] = seq;
        doc["code"] = code;
        doc["error"] = error;
        doc["message"] = message;
        return finish(doc);
    }
};

#endif // SINK_PROTOCOL_H
