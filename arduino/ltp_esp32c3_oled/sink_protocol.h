/**
 * ESP32-C3 OLED Sink - Protocol Handler
 *
 * Implements the LTP sink interface JSON control channel protocol.
 * Messages are newline-delimited JSON over TCP.
 */

#ifndef SINK_PROTOCOL_H
#define SINK_PROTOCOL_H

#include <Arduino.h>
#include <ArduinoJson.h>
#include "config.h"
#include "build_info.h"

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
#define MSG_CONTROL_CHANGED         "control_changed"
#define MSG_ERROR                   "error"

// Stream actions
#define ACTION_START    "start"
#define ACTION_STOP     "stop"
#define ACTION_PAUSE    "pause"

// Forward declaration
class OledDriver;

// Callbacks for control changes via protocol
typedef void (*LocalModeCallback)(uint8_t mode);
typedef void (*CycleTimeCallback)(uint16_t seconds);
typedef void (*BrightnessCallback)(uint8_t brightness);
typedef void (*ActionCallback)();

class SinkProtocol {
public:
    SinkProtocol()
        : config(nullptr)
        , oled(nullptr)
        , udpPort(0)
        , streamActive(false)
        , activeColorFormat(COLOR_FMT_RGB)
        , lastSeq(0)
        , onLocalModeChange(nullptr)
        , onCycleTimeChange(nullptr)
        , onBrightnessChange(nullptr)
        , onSave(nullptr)
        , onReboot(nullptr)
    {
        streamId[0] = '\0';
    }

    void begin(DeviceConfig* cfg, OledDriver* oledDriver, uint16_t dataPort) {
        config = cfg;
        oled = oledDriver;
        udpPort = dataPort;
    }

    // Set callbacks
    void setLocalModeCallback(LocalModeCallback callback) {
        onLocalModeChange = callback;
    }

    void setCycleTimeCallback(CycleTimeCallback callback) {
        onCycleTimeChange = callback;
    }

    void setBrightnessCallback(BrightnessCallback callback) {
        onBrightnessChange = callback;
    }

    void setSaveCallback(ActionCallback callback) {
        onSave = callback;
    }

    void setRebootCallback(ActionCallback callback) {
        onReboot = callback;
    }

    // Process a JSON message line, returns response
    String processMessage(const String& jsonLine) {
        JsonDocument doc;
        DeserializationError error = deserializeJson(doc, jsonLine);

        if (error) {
            dualOut.printf("JSON parse error: %s\r\n", error.c_str());
            return buildError(0, 1, "INVALID_FORMAT", "JSON parse error");
        }

        const char* msgType = doc["type"];
        int seq = doc["seq"] | 0;
        lastSeq = seq;

        if (!msgType) {
            return buildError(seq, 1, "INVALID_FORMAT", "Missing message type");
        }

        if (strcmp(msgType, MSG_CAPABILITY_REQUEST) == 0) {
            return handleCapabilityRequest(seq);
        } else if (strcmp(msgType, MSG_STREAM_SETUP) == 0) {
            return handleStreamSetup(seq, doc);
        } else if (strcmp(msgType, MSG_STREAM_CONTROL) == 0) {
            return handleStreamControl(seq, doc);
        } else if (strcmp(msgType, MSG_CONTROL_GET) == 0) {
            return handleControlGet(seq, doc);
        } else if (strcmp(msgType, MSG_CONTROL_SET) == 0) {
            return handleControlSet(seq, doc);
        }

        return buildError(seq, 4, "NOT_FOUND", "Unknown message type");
    }

    // Check if stream is active
    bool isStreamActive() const { return streamActive; }

    // Get the negotiated color format for the active stream
    uint8_t getActiveColorFormat() const { return activeColorFormat; }

    // Stop stream (called when client disconnects)
    void stopStream() {
        streamActive = false;
        activeColorFormat = COLOR_FMT_RGB;
        streamId[0] = '\0';
    }

private:
    DeviceConfig* config;
    OledDriver* oled;
    uint16_t udpPort;
    bool streamActive;
    uint8_t activeColorFormat;
    char streamId[32];
    int lastSeq;
    LocalModeCallback onLocalModeChange;
    CycleTimeCallback onCycleTimeChange;
    BrightnessCallback onBrightnessChange;
    ActionCallback onSave;
    ActionCallback onReboot;

    String handleCapabilityRequest(int seq) {
        JsonDocument doc;

        doc["type"] = MSG_CAPABILITY_RESPONSE;
        if (seq > 0) doc["seq"] = seq;

        JsonObject device = doc["device"].to<JsonObject>();

        // Device ID - generate from MAC
        uint8_t mac[6];
        WiFi.macAddress(mac);
        char deviceId[48];
        snprintf(deviceId, sizeof(deviceId),
                 "%02x%02x%02x%02x-%02x%02x-4000-8000-%02x%02x%02x%02x%02x%02x",
                 mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
                 mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
        device["id"] = deviceId;
        device["name"] = config->deviceName;
        device["description"] = "ESP32-C3 72x40 OLED Display";
        device["type"] = "matrix";
        device["pixels"] = OLED_TOTAL_PIXELS;

        JsonArray dims = device["dimensions"].to<JsonArray>();
        dims.add(OLED_WIDTH);
        dims.add(OLED_HEIGHT);

        // Topology
        JsonObject topology = device["topology"].to<JsonObject>();
        topology["topology"] = "matrix";
        JsonArray topoDims = topology["dimensions"].to<JsonArray>();
        topoDims.add(OLED_WIDTH);
        topoDims.add(OLED_HEIGHT);

        JsonArray colorFormats = device["color_formats"].to<JsonArray>();
        colorFormats.add("rgb");
        colorFormats.add("grayscale");
        colorFormats.add("mono_packed");

        device["preferred_format"] = "mono_packed";
        device["max_refresh_hz"] = 15;
        device["protocol_version"] = "0.1";

        // Build info
        device["firmware_name"] = FIRMWARE_NAME;
        device["git_commit"] = GIT_COMMIT;
        device["build_date"] = BUILD_DATE;

        // Controls
        JsonArray controls = device["controls"].to<JsonArray>();

        JsonObject brightness = controls.add<JsonObject>();
        brightness["id"] = "brightness";
        brightness["name"] = "Brightness";
        brightness["type"] = "number";
        brightness["description"] = "OLED contrast 0-255";
        brightness["value"] = config->brightness;
        brightness["min"] = 0;
        brightness["max"] = 255;

        JsonObject idleTimeout = controls.add<JsonObject>();
        idleTimeout["id"] = "idle_timeout";
        idleTimeout["name"] = "Idle Timeout";
        idleTimeout["type"] = "number";
        idleTimeout["description"] = "secs, 0=off";
        idleTimeout["value"] = config->idleTimeout;
        idleTimeout["min"] = 0;
        idleTimeout["max"] = 32767;

        JsonObject localMode = controls.add<JsonObject>();
        localMode["id"] = "local_mode";
        localMode["name"] = "Local Mode";
        localMode["type"] = "number";
        localMode["description"] = "0=off, 1=info, 2=clock, 3=pattern, 255=cycle";
        localMode["value"] = config->localMode;
        localMode["min"] = 0;
        localMode["max"] = 255;

        JsonObject cycleTime = controls.add<JsonObject>();
        cycleTime["id"] = "cycle_time";
        cycleTime["name"] = "Cycle Time";
        cycleTime["type"] = "number";
        cycleTime["description"] = "mode cycle, secs";
        cycleTime["value"] = config->cycleTime;
        cycleTime["min"] = 1;
        cycleTime["max"] = 3600;
        cycleTime["step"] = 1;

        // Action controls
        JsonObject save = controls.add<JsonObject>();
        save["id"] = "save";
        save["name"] = "Save";
        save["type"] = "action";
        save["description"] = "save to flash";

        JsonObject reboot = controls.add<JsonObject>();
        reboot["id"] = "reboot";
        reboot["name"] = "Reboot";
        reboot["type"] = "action";
        reboot["description"] = "restart";

        // No inputs (no touch sensors)

        String response;
        serializeJson(doc, response);
        response += "\n";
        return response;
    }

    String handleStreamSetup(int seq, JsonDocument& doc) {
        JsonDocument resp;
        resp["type"] = MSG_STREAM_SETUP_RESPONSE;
        if (seq > 0) resp["seq"] = seq;
        resp["status"] = "ok";
        resp["udp_port"] = udpPort;

        // Extract negotiated color format from stream_setup request
        const char* colorStr = doc["format"]["color"];
        if (colorStr && strcmp(colorStr, "mono_packed") == 0) {
            activeColorFormat = COLOR_FMT_MONO_PACKED;
            dualOut.println("Stream format: mono_packed");
        } else if (colorStr && strcmp(colorStr, "grayscale") == 0) {
            activeColorFormat = COLOR_FMT_GRAYSCALE;
            dualOut.println("Stream format: grayscale");
        } else {
            activeColorFormat = COLOR_FMT_RGB;
            dualOut.println("Stream format: rgb");
        }

        // Generate stream ID
        snprintf(streamId, sizeof(streamId), "stream-%04x", (unsigned int)(millis() & 0xFFFF));
        resp["stream_id"] = streamId;

        streamActive = true;

        String response;
        serializeJson(resp, response);
        response += "\n";
        return response;
    }

    String handleStreamControl(int seq, JsonDocument& doc) {
        const char* action = doc["action"];
        const char* reqStreamId = doc["stream_id"];

        JsonDocument resp;
        resp["type"] = MSG_STREAM_CONTROL_RESPONSE;
        if (seq > 0) resp["seq"] = seq;
        resp["status"] = "ok";
        resp["stream_id"] = reqStreamId ? reqStreamId : streamId;

        if (action) {
            if (strcmp(action, ACTION_START) == 0) {
                streamActive = true;
                dualOut.println("Stream started");
            } else if (strcmp(action, ACTION_STOP) == 0) {
                streamActive = false;
                dualOut.println("Stream stopped");
            } else if (strcmp(action, ACTION_PAUSE) == 0) {
                streamActive = false;
                dualOut.println("Stream paused");
            }
        }

        String response;
        serializeJson(resp, response);
        response += "\n";
        return response;
    }

    String handleControlGet(int seq, JsonDocument& doc) {
        JsonDocument resp;
        resp["type"] = MSG_CONTROL_GET_RESPONSE;
        if (seq > 0) resp["seq"] = seq;
        resp["status"] = "ok";

        JsonObject values = resp["values"].to<JsonObject>();

        // Check if specific IDs requested
        JsonArray ids = doc["ids"];
        if (ids.size() > 0) {
            for (JsonVariant id : ids) {
                const char* idStr = id.as<const char*>();
                values[idStr] = getControlValue(idStr);
            }
        } else {
            // Return all controls
            values["brightness"] = config->brightness;
            values["idle_timeout"] = config->idleTimeout;
            values["local_mode"] = config->localMode;
            values["cycle_time"] = config->cycleTime;
        }

        String response;
        serializeJson(resp, response);
        response += "\n";
        return response;
    }

    String handleControlSet(int seq, JsonDocument& doc) {
        JsonDocument resp;
        resp["type"] = MSG_CONTROL_SET_RESPONSE;
        if (seq > 0) resp["seq"] = seq;

        JsonObject applied = resp["applied"].to<JsonObject>();
        JsonObject errors = resp["errors"].to<JsonObject>();

        JsonObject values = doc["values"];
        bool hasErrors = false;

        for (JsonPair kv : values) {
            const char* id = kv.key().c_str();
            float value = kv.value().as<float>();

            if (setControlValue(id, value)) {
                applied[id] = value;
            } else {
                errors[id] = "Invalid control ID or value";
                hasErrors = true;
            }
        }

        resp["status"] = hasErrors ? "partial" : "ok";

        String response;
        serializeJson(resp, response);
        response += "\n";
        return response;
    }

    String buildError(int seq, int code, const char* error, const char* message) {
        JsonDocument doc;
        doc["type"] = MSG_ERROR;
        if (seq > 0) doc["seq"] = seq;
        doc["code"] = code;
        doc["error"] = error;
        doc["message"] = message;

        String response;
        serializeJson(doc, response);
        response += "\n";
        return response;
    }

    float getControlValue(const char* id) {
        if (strcmp(id, "brightness") == 0) return config->brightness;
        if (strcmp(id, "idle_timeout") == 0) return config->idleTimeout;
        if (strcmp(id, "local_mode") == 0) return config->localMode;
        if (strcmp(id, "cycle_time") == 0) return config->cycleTime;
        return 0;
    }

    bool setControlValue(const char* id, float value) {
        if (strcmp(id, "brightness") == 0) {
            config->brightness = constrain((int)value, 0, 255);
            dualOut.printf("Protocol: brightness=%d\r\n", config->brightness);
            if (onBrightnessChange) {
                onBrightnessChange(config->brightness);
            }
            return true;
        }
        if (strcmp(id, "idle_timeout") == 0) {
            config->idleTimeout = constrain((int)value, 0, 32767);
            dualOut.printf("Protocol: idle_timeout=%d\r\n", config->idleTimeout);
            return true;
        }
        if (strcmp(id, "local_mode") == 0) {
            int mode = (int)value;
            if (mode < LOCAL_MODE_COUNT || mode == LOCAL_MODE_CYCLE) {
                config->localMode = mode;
                dualOut.printf("Protocol: local_mode=%d\r\n", mode);
                if (onLocalModeChange) {
                    onLocalModeChange(mode);
                }
                return true;
            }
            return false;
        }
        if (strcmp(id, "cycle_time") == 0) {
            config->cycleTime = constrain((int)value, 1, 3600);
            dualOut.printf("Protocol: cycle_time=%d\r\n", config->cycleTime);
            if (onCycleTimeChange) {
                onCycleTimeChange(config->cycleTime);
            }
            return true;
        }
        if (strcmp(id, "save") == 0) {
            dualOut.println("Protocol: save");
            if (onSave) {
                onSave();
            }
            return true;
        }
        if (strcmp(id, "reboot") == 0) {
            dualOut.println("Protocol: reboot");
            if (onReboot) {
                onReboot();
            }
            return true;
        }
        return false;
    }
};

#endif // SINK_PROTOCOL_H
