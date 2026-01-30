/**
 * ESP32 Ring Controller - Sink Protocol Handler
 *
 * Implements the LTP sink interface JSON control channel protocol.
 * Messages are newline-delimited JSON over TCP.
 */

#ifndef SINK_PROTOCOL_H
#define SINK_PROTOCOL_H

#include <Arduino.h>
#include <ArduinoJson.h>
#include "config.h"

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
#define MSG_INPUT_EVENT             "input_event"
#define MSG_ERROR                   "error"

// Stream actions
#define ACTION_START    "start"
#define ACTION_STOP     "stop"
#define ACTION_PAUSE    "pause"

// Forward declaration (RingDriver defined in ring_driver.h)
class RingDriver;

// Control value holder
struct ControlValue {
    const char* id;
    float value;
    float min;
    float max;
    bool readonly;
};

// Callback for when local mode changes via protocol
typedef void (*LocalModeCallback)(uint8_t mode);
typedef void (*CycleTimeCallback)(uint16_t seconds);

class SinkProtocol {
public:
    SinkProtocol()
        : config(nullptr)
        , leds(nullptr)
        , udpPort(0)
        , streamActive(false)
        , lastSeq(0)
        , onLocalModeChange(nullptr)
        , onCycleTimeChange(nullptr)
    {
        streamId[0] = '\0';
    }

    void begin(DeviceConfig* cfg, RingDriver* ledDriver, uint16_t dataPort) {
        config = cfg;
        leds = ledDriver;
        udpPort = dataPort;
    }

    // Set callback for local mode changes
    void setLocalModeCallback(LocalModeCallback callback) {
        onLocalModeChange = callback;
    }

    // Set callback for cycle time changes
    void setCycleTimeCallback(CycleTimeCallback callback) {
        onCycleTimeChange = callback;
    }

    // Process a JSON message line, returns response (caller must free)
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

    // Build an input event message for touch
    String buildInputEvent(uint8_t touchIdx, const char* inputName, bool value);

    // Check if stream is active
    bool isStreamActive() const { return streamActive; }

    // Stop stream (called when client disconnects)
    void stopStream() {
        streamActive = false;
        streamId[0] = '\0';
    }

private:
    DeviceConfig* config;
    RingDriver* leds;
    uint16_t udpPort;
    bool streamActive;
    char streamId[32];
    int lastSeq;
    LocalModeCallback onLocalModeChange;
    CycleTimeCallback onCycleTimeChange;

    String handleCapabilityRequest(int seq);
    String handleStreamSetup(int seq, JsonDocument& doc);
    String handleStreamControl(int seq, JsonDocument& doc);
    String handleControlGet(int seq, JsonDocument& doc);
    String handleControlSet(int seq, JsonDocument& doc);

    String buildError(int seq, int code, const char* error, const char* message);

    // Get control value by ID
    float getControlValue(const char* id);
    bool setControlValue(const char* id, float value);
};

// Implementation

String SinkProtocol::handleCapabilityRequest(int seq) {
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
    device["description"] = "ESP32 LED Ring with touch sensors";
    device["type"] = "custom";  // Ring topology
    device["pixels"] = RING_NUM_PIXELS;

    JsonArray dims = device["dimensions"].to<JsonArray>();
    dims.add(RING_NUM_PIXELS);

    // Topology - ring is custom type
    JsonObject topology = device["topology"].to<JsonObject>();
    topology["topology"] = "linear";
    JsonArray topoDims = topology["dimensions"].to<JsonArray>();
    topoDims.add(RING_NUM_PIXELS);

    JsonArray colorFormats = device["color_formats"].to<JsonArray>();
    colorFormats.add("rgb");

    device["max_refresh_hz"] = 60;
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
    brightness["value"] = config->brightness;
    brightness["min"] = 0;
    brightness["max"] = 255;

    JsonObject gamma = controls.add<JsonObject>();
    gamma["id"] = "gamma";
    gamma["name"] = "Gamma";
    gamma["type"] = "number";
    gamma["value"] = config->gamma / 10.0;
    gamma["min"] = 1.0;
    gamma["max"] = 3.0;
    gamma["step"] = 0.1;

    JsonObject localMode = controls.add<JsonObject>();
    localMode["id"] = "local_mode";
    localMode["name"] = "Local Mode";
    localMode["type"] = "number";
    localMode["value"] = config->localMode;
    localMode["min"] = 0;
    localMode["max"] = 255;

    // Note: ws2812_offset is USB-terminal only, not exposed via sink protocol

    // Inputs (touch sensors)
    JsonArray inputs = device["inputs"].to<JsonArray>();
    for (int i = 0; i < TOUCH_NUM_SENSORS; i++) {
        JsonObject input = inputs.add<JsonObject>();
        input["id"] = i;
        char name[16];
        snprintf(name, sizeof(name), "Touch%d", i);
        input["name"] = name;
        input["type"] = "touch";
    }

    String response;
    serializeJson(doc, response);
    response += "\n";
    return response;
}

String SinkProtocol::handleStreamSetup(int seq, JsonDocument& doc) {
    JsonDocument resp;
    resp["type"] = MSG_STREAM_SETUP_RESPONSE;
    if (seq > 0) resp["seq"] = seq;
    resp["status"] = "ok";
    resp["udp_port"] = udpPort;

    // Generate stream ID
    snprintf(streamId, sizeof(streamId), "stream-%04x", (unsigned int)(millis() & 0xFFFF));
    resp["stream_id"] = streamId;

    streamActive = true;

    String response;
    serializeJson(resp, response);
    response += "\n";
    return response;
}

String SinkProtocol::handleStreamControl(int seq, JsonDocument& doc) {
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

String SinkProtocol::handleControlGet(int seq, JsonDocument& doc) {
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
        // Return all controls (ws2812_offset is USB-only)
        values["brightness"] = config->brightness;
        values["gamma"] = config->gamma / 10.0;
        values["local_mode"] = config->localMode;
        values["cycle_time"] = config->cycleTime;
    }

    String response;
    serializeJson(resp, response);
    response += "\n";
    return response;
}

String SinkProtocol::handleControlSet(int seq, JsonDocument& doc) {
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

String SinkProtocol::buildError(int seq, int code, const char* error, const char* message) {
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

String SinkProtocol::buildInputEvent(uint8_t touchIdx, const char* inputName, bool value) {
    JsonDocument doc;
    doc["type"] = MSG_INPUT_EVENT;
    doc["input_id"] = touchIdx;
    doc["input_name"] = inputName;
    doc["input_type"] = "touch";
    doc["value"] = value;
    doc["timestamp"] = millis();

    String response;
    serializeJson(doc, response);
    response += "\n";
    return response;
}

float SinkProtocol::getControlValue(const char* id) {
    if (strcmp(id, "brightness") == 0) return config->brightness;
    if (strcmp(id, "gamma") == 0) return config->gamma / 10.0;
    if (strcmp(id, "local_mode") == 0) return config->localMode;
    if (strcmp(id, "cycle_time") == 0) return config->cycleTime;
    // ws2812_offset is USB-terminal only
    return 0;
}

bool SinkProtocol::setControlValue(const char* id, float value) {
    if (strcmp(id, "brightness") == 0) {
        config->brightness = constrain((int)value, 0, 255);
        if (leds) leds->setBrightness(config->brightness);
        dualOut.printf("Protocol: brightness=%d\r\n", config->brightness);
        return true;
    }
    if (strcmp(id, "gamma") == 0) {
        config->gamma = constrain((int)(value * 10), 10, 30);
        dualOut.printf("Protocol: gamma=%.1f\r\n", config->gamma / 10.0);
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
    // ws2812_offset is USB-terminal only
    return false;
}

#endif // SINK_PROTOCOL_H
