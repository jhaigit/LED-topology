/**
 * ESP32-C3 OLED Sink - Layer 2 device auth guard (proposal §2).
 *
 * Device-side twin of src/libltp/deviceauth.py DeviceAuthGuard:
 *   - issues single-use challenge nonces
 *   - verifies claim proofs (SipHash of nonce||device_id||controller_id)
 *   - holds one exclusive lease at a time (owner + session key + token)
 *   - verifies the per-message MAC on privileged commands, with a
 *     monotonic counter for replay protection
 *
 * The PSK and derived session key never leave the device. A guard with no
 * PSK is inert (Level 0) — every message passes, exactly today's behavior.
 *
 * Canonical MAC preimage matches Python's _canonical():
 *     type \x1f token \x1f n \x1f canon(body)
 * where canon() sorts object keys, renders integral numbers as integers,
 * booleans as true/false, arrays in order. See buildCanon().
 */

#ifndef LTP_DEVICE_AUTH_H
#define LTP_DEVICE_AUTH_H

#include <Arduino.h>
#include <ArduinoJson.h>
#include "siphash.h"

#define AUTH_SEP            '\x1f'
#define AUTH_NONCE_TTL_MS   30000UL
#define AUTH_DEFAULT_LEASE  30      // seconds
#define AUTH_MAX_CONTROLLERS 4      // outstanding challenges tracked

// Error codes (match libltp.types.ErrorCode)
#define ERR_UNAUTHORIZED    8
#define ERR_LEASE_HELD      9

class DeviceAuthGuard {
public:
    DeviceAuthGuard()
        : enabled(false), readOpen(true), leaseSeconds(AUTH_DEFAULT_LEASE),
          claimed_(false), leaseExpiryMs(0), lastCounter(0), nonceCount(0) {
        deviceId[0] = '\0';
        ownerId[0] = '\0';
        ownerIp[0] = '\0';
        token[0] = '\0';
    }

    // psk16 = 16-byte key, or nullptr to leave the guard inert (Level 0).
    void begin(const uint8_t* psk16, const char* devId, bool reads_open) {
        if (psk16) {
            memcpy(psk, psk16, 16);
            enabled = true;
        } else {
            enabled = false;
        }
        strncpy(deviceId, devId, sizeof(deviceId) - 1);
        deviceId[sizeof(deviceId) - 1] = '\0';
        readOpen = reads_open;
    }

    bool isEnabled() const { return enabled; }
    bool isClaimed() { return claimed_ && (int32_t)(leaseExpiryMs - millis()) > 0; }
    const char* getOwnerIp() { return isClaimed() ? ownerIp : ""; }
    const char* getOwnerId() { return isClaimed() ? ownerId : ""; }

    // Fill capability_response.device.auth
    void fillAuthInfo(JsonObject auth) {
        if (!enabled) {
            auth["mode"] = "none";
            auth["required"] = false;
            auth["claimed"] = false;
        } else {
            auth["mode"] = "siphash";
            auth["required"] = true;
            auth["claimed"] = isClaimed();
        }
    }

    // Handle a message before the normal protocol handler.
    //   returns true  -> `outResponse` holds the reply; do NOT dispatch further
    //   returns false -> message is authorized (or auth disabled); dispatch it
    // `isPrivileged` marks control_set/stream_setup/stream_control/etc.
    bool handle(const char* msgType, int seq, JsonDocument& doc,
                const char* peerIp, bool isPrivileged, String& outResponse) {
        if (!enabled) return false;

        if (strcmp(msgType, "auth_challenge_request") == 0) {
            outResponse = issueChallenge(seq, doc);
            return true;
        }
        if (strcmp(msgType, "claim") == 0) {
            outResponse = handleClaim(seq, doc, peerIp);
            return true;
        }
        if (strcmp(msgType, "claim_renew") == 0) {
            if (!verifyAuth(doc)) { outResponse = err(seq, ERR_UNAUTHORIZED, "unauthorized"); return true; }
            leaseExpiryMs = millis() + (uint32_t)leaseSeconds * 1000UL;
            outResponse = simpleResp(seq, "claim_renew_response");
            return true;
        }
        if (strcmp(msgType, "release") == 0) {
            if (!verifyAuth(doc)) { outResponse = err(seq, ERR_UNAUTHORIZED, "unauthorized"); return true; }
            claimed_ = false;
            outResponse = simpleResp(seq, "release_response");
            return true;
        }

        if (isPrivileged) {
            if (!verifyAuth(doc)) {
                outResponse = err(seq, ERR_UNAUTHORIZED, "unauthorized");
                return true;
            }
            return false;  // authorized — dispatch
        }

        // Read-only message when reads are gated
        if (!readOpen) {
            if (!verifyAuth(doc)) {
                outResponse = err(seq, ERR_UNAUTHORIZED, "reads are gated");
                return true;
            }
        }
        return false;
    }

private:
    bool enabled;
    bool readOpen;
    uint8_t psk[16];
    char deviceId[40];
    int leaseSeconds;

    bool claimed_;
    char ownerId[40];
    char ownerIp[40];
    char token[33];
    uint8_t sessionKey[16];
    uint32_t leaseExpiryMs;
    uint32_t lastCounter;

    // Outstanding challenge nonces, keyed by controller id.
    struct NonceEntry { char controllerId[40]; uint8_t nonce[16]; uint32_t issuedMs; };
    NonceEntry nonces[AUTH_MAX_CONTROLLERS];
    int nonceCount;

    static void hexToBytes(const char* hex, uint8_t* out, int nbytes) {
        for (int i = 0; i < nbytes; i++) {
            unsigned v = 0;
            char b[3] = { hex[i * 2], hex[i * 2 + 1], 0 };
            sscanf(b, "%2x", &v);
            out[i] = (uint8_t)v;
        }
    }
    static void bytesToHex(const uint8_t* in, int nbytes, char* out) {
        static const char h[] = "0123456789abcdef";
        for (int i = 0; i < nbytes; i++) {
            out[i * 2] = h[in[i] >> 4];
            out[i * 2 + 1] = h[in[i] & 0xf];
        }
        out[nbytes * 2] = '\0';
    }

    void randomBytes(uint8_t* buf, int n) {
        for (int i = 0; i < n; i++) buf[i] = (uint8_t)esp_random();
    }

    // proof = SipHash(psk, nonce || device_id || controller_id)
    void computeProof(const uint8_t* nonce, const char* controllerId, char* outHex) {
        uint8_t buf[16 + 40 + 40];
        int n = 0;
        memcpy(buf, nonce, 16); n += 16;
        int dl = strlen(deviceId); memcpy(buf + n, deviceId, dl); n += dl;
        int cl = strlen(controllerId); memcpy(buf + n, controllerId, cl); n += cl;
        ltp_siphash24_hex(psk, buf, n, outHex);
    }

    void computeDeviceProof(const uint8_t* nonce, char* outHex) {
        uint8_t buf[16 + 6];
        memcpy(buf, nonce, 16);
        memcpy(buf + 16, "device", 6);
        ltp_siphash24_hex(psk, buf, 22, outHex);
    }

    void deriveSessionKey(const uint8_t* nonce, uint8_t* out16) {
        uint8_t buf[16 + 3];
        char hex1[17], hex2[17];
        memcpy(buf, nonce, 16); memcpy(buf + 16, "sk1", 3);
        ltp_siphash24_hex(psk, buf, 19, hex1);
        memcpy(buf + 16, "sk2", 3);
        ltp_siphash24_hex(psk, buf, 19, hex2);
        hexToBytes(hex1, out16, 8);
        hexToBytes(hex2, out16 + 8, 8);
    }

    String issueChallenge(int seq, JsonDocument& doc) {
        const char* controllerId = doc["controller_id"] | "";
        uint8_t nonce[16];
        randomBytes(nonce, 16);

        // Evict expired, then insert (replacing same controller id).
        uint32_t now = millis();
        int slot = -1;
        for (int i = 0; i < nonceCount; i++) {
            if (strcmp(nonces[i].controllerId, controllerId) == 0) { slot = i; break; }
        }
        if (slot < 0) {
            if (nonceCount < AUTH_MAX_CONTROLLERS) slot = nonceCount++;
            else slot = 0;  // overwrite oldest slot (simple ring)
        }
        strncpy(nonces[slot].controllerId, controllerId, 39);
        nonces[slot].controllerId[39] = '\0';
        memcpy(nonces[slot].nonce, nonce, 16);
        nonces[slot].issuedMs = now;

        char nonceHex[33];
        bytesToHex(nonce, 16, nonceHex);

        JsonDocument resp;
        resp["type"] = "auth_challenge";
        if (seq > 0) resp["seq"] = seq;
        resp["nonce"] = nonceHex;
        resp["device_id"] = deviceId;
        String s; serializeJson(resp, s); s += "\n"; return s;
    }

    String handleClaim(int seq, JsonDocument& doc, const char* peerIp) {
        const char* controllerId = doc["controller_id"] | "";
        const char* proof = doc["proof"] | "";

        // Find and consume the outstanding nonce for this controller.
        int slot = -1;
        uint32_t now = millis();
        for (int i = 0; i < nonceCount; i++) {
            if (strcmp(nonces[i].controllerId, controllerId) == 0) { slot = i; break; }
        }
        if (slot < 0 || (now - nonces[slot].issuedMs) >= AUTH_NONCE_TTL_MS) {
            return err(seq, ERR_UNAUTHORIZED, "no valid challenge");
        }
        uint8_t nonce[16];
        memcpy(nonce, nonces[slot].nonce, 16);
        // consume (remove from list)
        for (int i = slot; i < nonceCount - 1; i++) nonces[i] = nonces[i + 1];
        nonceCount--;

        char expected[17];
        computeProof(nonce, controllerId, expected);
        if (strncmp(proof, expected, 16) != 0) {
            return err(seq, ERR_UNAUTHORIZED, "invalid proof");
        }

        // Exclusive lease: refuse a different live owner.
        if (isClaimed() && strcmp(ownerId, controllerId) != 0) {
            JsonDocument resp;
            resp["type"] = "error";
            if (seq > 0) resp["seq"] = seq;
            resp["code"] = ERR_LEASE_HELD;
            resp["error"] = "LEASE_HELD";
            resp["message"] = "device is claimed";
            int remaining = (int)((leaseExpiryMs - millis()) / 1000);
            resp["retry_after"] = remaining < 0 ? 0 : remaining;
            String s; serializeJson(resp, s); s += "\n"; return s;
        }

        int leaseReq = doc["lease_seconds"] | AUTH_DEFAULT_LEASE;
        if (leaseReq < 5) leaseReq = 5;
        if (leaseReq > 300) leaseReq = 300;
        leaseSeconds = leaseReq;

        strncpy(ownerId, controllerId, 39); ownerId[39] = '\0';
        strncpy(ownerIp, peerIp ? peerIp : "", 39); ownerIp[39] = '\0';
        deriveSessionKey(nonce, sessionKey);
        uint8_t tokBytes[16];
        randomBytes(tokBytes, 16);
        bytesToHex(tokBytes, 16, token);
        leaseExpiryMs = millis() + (uint32_t)leaseSeconds * 1000UL;
        lastCounter = 0;
        claimed_ = true;

        char deviceProof[17];
        computeDeviceProof(nonce, deviceProof);

        JsonDocument resp;
        resp["type"] = "claim_response";
        if (seq > 0) resp["seq"] = seq;
        resp["token"] = token;
        resp["lease_seconds"] = leaseSeconds;
        resp["device_proof"] = deviceProof;
        String s; serializeJson(resp, s); s += "\n"; return s;
    }

    // Recursively append canon(value) for the MAC preimage. Mirrors
    // _canon_value in deviceauth.py.
    static void canonValue(JsonVariantConst v, String& out) {
        if (v.is<bool>()) {
            out += v.as<bool>() ? "true" : "false";
        } else if (v.is<JsonObjectConst>()) {
            JsonObjectConst obj = v.as<JsonObjectConst>();
            // Collect and sort keys (small objects; simple insertion sort).
            const char* keys[16]; int nk = 0;
            for (JsonPairConst kv : obj) {
                if (nk < 16) keys[nk++] = kv.key().c_str();
            }
            for (int i = 1; i < nk; i++) {
                const char* k = keys[i]; int j = i - 1;
                while (j >= 0 && strcmp(keys[j], k) > 0) { keys[j + 1] = keys[j]; j--; }
                keys[j + 1] = k;
            }
            out += '{';
            for (int i = 0; i < nk; i++) {
                if (i) out += ',';
                out += keys[i]; out += '=';
                canonValue(obj[keys[i]], out);
            }
            out += '}';
        } else if (v.is<JsonArrayConst>()) {
            out += '[';
            bool first = true;
            for (JsonVariantConst e : v.as<JsonArrayConst>()) {
                if (!first) out += ','; first = false;
                canonValue(e, out);
            }
            out += ']';
        } else if (v.is<const char*>()) {
            out += v.as<const char*>();
        } else if (v.isNull()) {
            out += "null";
        } else if (v.is<int>() || v.is<long>()) {
            out += String((long)v.as<long>());
        } else if (v.is<float>() || v.is<double>()) {
            double d = v.as<double>();
            if (d == (double)(long)d) out += String((long)d);
            else out += String(d, 6);
        } else {
            out += v.as<String>();
        }
    }

    // Build type \x1f token \x1f n \x1f canon(body). body = all fields
    // except "seq" and "auth".
    void buildCanon(const char* msgType, JsonDocument& doc,
                    const char* tok, long n, String& out) {
        out = msgType;
        out += AUTH_SEP; out += tok;
        out += AUTH_SEP; out += String(n);
        out += AUTH_SEP;
        // canon(body) — object of non-seq, non-auth keys, keys sorted.
        JsonObjectConst root = doc.as<JsonObjectConst>();
        const char* keys[24]; int nk = 0;
        for (JsonPairConst kv : root) {
            const char* k = kv.key().c_str();
            if (strcmp(k, "seq") == 0 || strcmp(k, "auth") == 0 || strcmp(k, "type") == 0)
                continue;
            if (nk < 24) keys[nk++] = k;
        }
        for (int i = 1; i < nk; i++) {
            const char* k = keys[i]; int j = i - 1;
            while (j >= 0 && strcmp(keys[j], k) > 0) { keys[j + 1] = keys[j]; j--; }
            keys[j + 1] = k;
        }
        out += '{';
        for (int i = 0; i < nk; i++) {
            if (i) out += ',';
            out += keys[i]; out += '=';
            canonValue(root[keys[i]], out);
        }
        out += '}';
    }

    bool verifyAuth(JsonDocument& doc) {
        if (!isClaimed()) { claimed_ = false; return false; }
        JsonObjectConst auth = doc["auth"];
        if (auth.isNull()) return false;
        const char* atoken = auth["token"] | "";
        long n = auth["n"] | -1;
        const char* mac = auth["mac"] | "";
        if (strcmp(atoken, token) != 0) return false;
        if (n <= (long)lastCounter) return false;
        const char* msgType = doc["type"] | "";
        String canon;
        buildCanon(msgType, doc, token, n, canon);
        char expected[17];
        ltp_siphash24_hex(sessionKey, (const uint8_t*)canon.c_str(), canon.length(), expected);
        if (strncmp(mac, expected, 16) != 0) return false;
        lastCounter = (uint32_t)n;
        return true;
    }

    String simpleResp(int seq, const char* type) {
        JsonDocument resp;
        resp["type"] = type;
        if (seq > 0) resp["seq"] = seq;
        if (strcmp(type, "claim_renew_response") == 0) resp["lease_seconds"] = leaseSeconds;
        String s; serializeJson(resp, s); s += "\n"; return s;
    }

    String err(int seq, int code, const char* message) {
        JsonDocument resp;
        resp["type"] = "error";
        if (seq > 0) resp["seq"] = seq;
        resp["code"] = code;
        resp["error"] = code == ERR_LEASE_HELD ? "LEASE_HELD" : "UNAUTHORIZED";
        resp["message"] = message;
        String s; serializeJson(resp, s); s += "\n"; return s;
    }
};

#endif // LTP_DEVICE_AUTH_H
