/**
 * ESP32-C3 OLED Sink - X25519 + PIN device pairing (proposal Phase 4b).
 *
 * Device-side twin of src/libltp/pairing.py (ReferenceDevice role). This
 * establishes the Layer 2 PSK on both controller and device *without the key
 * ever crossing the wire*: an ephemeral X25519 ECDH gives a shared secret, and
 * a short PIN shown on the OLED and typed into the controller binds the
 * exchange against a man-in-the-middle via mutual key confirmation. After
 * pairing, the derived PSK feeds the existing claim / SipHash-MAC path
 * (device_auth.h) unchanged.
 *
 * Derivation — MUST stay byte-identical to src/libltp/pairing.py:
 *
 *   Z    = X25519(own_priv, peer_pub)                    // 32-byte shared secret
 *   info = "ltp-pair-v1" + 0x00 + controller_pub(32) + device_pub(32) + pin(8)
 *   okm  = HKDF-SHA256(ikm=Z, salt=salt16, info=info, len=48)
 *   psk  = okm[0:16]     // the Layer 2 PSK (persisted to NVS authPsk)
 *   kc   = okm[16:48]    // key-confirmation key (ephemeral, never stored)
 *   confirm_controller = HMAC-SHA256(kc, "controller-confirm")[0:16]
 *   confirm_device     = HMAC-SHA256(kc, "device-confirm")[0:16]
 *
 * info total = 11 + 1 + 32 + 32 + 8 = 84 bytes.
 *
 * X25519 note: mbedTLS represents Curve25519 scalars/coordinates in RFC 7748
 * little-endian byte order, matching Python `cryptography`'s raw X25519 bytes.
 * We clamp the private scalar (RFC 7748) and mask the peer u-coordinate's high
 * bit, exactly as `cryptography` does, so the results match the pinned vectors.
 *
 * PINNED INTEROP VECTORS (this code must reproduce these):
 *   controller_priv = 0x00..0x1f, device_priv = 0x20..0x3f
 *   salt = 00112233445566778899aabbccddeeff, pin = "01234567"
 *   controller_pub      = 8f40c5adb68f25624ae5b214ea767a6ec94d829d3d7b5e1ad1ba6f3e2138285f
 *   device_pub          = 358072d6365880d1aeea329adf9121383851ed21a28e3b75e965d0d2cd166254
 *   shared              = 9663aa1da97e848a914a436d04163dfbb89178f107f1b5b77ed3854203382854
 *   PSK                 = 83962a754f05995f35965aa40075e7f2
 *   confirm_controller  = 38d491461f147c3b134cc5aecd9c6786
 *   confirm_device      = 27ffdbbd22b54175887878e8d0ccc1f1
 *
 * The PSK and key-confirmation key (kc) are never logged.
 */

#ifndef LTP_DEVICE_PAIRING_H
#define LTP_DEVICE_PAIRING_H

#include <Arduino.h>
#include <ArduinoJson.h>

#include "mbedtls/ecp.h"
#include "mbedtls/bignum.h"
#include "mbedtls/hkdf.h"
#include "mbedtls/md.h"

// TTL of an armed pairing window (one-shot).
#define PAIR_WINDOW_MS      120000UL

// Error codes (match src/libltp.types / controller expectations).
#define ERR_NOT_PAIRING     10
#define ERR_PAIRING_FAILED  11

#define PAIR_PIN_DIGITS     8
#define PAIR_SALT_LEN       16
#define PAIR_PSK_LEN        16
#define PAIR_CONFIRM_LEN    16

class DevicePairing {
public:
    DevicePairing()
        : armed_(false), havePending_(false), windowExpiryMs_(0) {
        pinStr_[0] = '\0';
        deviceId_[0] = '\0';
    }

    // The device UUID reported in pair_complete. Set once from the sink.
    void setDeviceId(const char* id) {
        strncpy(deviceId_, id, sizeof(deviceId_) - 1);
        deviceId_[sizeof(deviceId_) - 1] = '\0';
    }

    // Generate fresh ephemeral X25519 material, a 16-byte salt and an 8-digit
    // PIN, then open the pairing window. Copies the PIN to pinOut (>= 9 bytes).
    bool arm(char* pinOut) {
        randomBytes(privKey_, 32);
        if (!x25519(privKey_, nullptr, pubKey_)) {
            armed_ = false;
            return false;
        }
        randomBytes(salt_, PAIR_SALT_LEN);

        // Zero-padded 8-digit decimal PIN (matches pairing.generate_pin).
        uint32_t r = esp_random() % 100000000UL;   // 0 .. 99,999,999
        snprintf(pinStr_, sizeof(pinStr_), "%08lu", (unsigned long)r);
        strcpy(pinOut, pinStr_);

        havePending_ = false;
        armed_ = true;
        windowExpiryMs_ = millis() + PAIR_WINDOW_MS;
        return true;
    }

    bool isArmed() {
        return armed_ && (int32_t)(windowExpiryMs_ - millis()) > 0;
    }

    const char* pin() const { return pinStr_; }

    // pair_begin: compute the shared secret and derive the PSK + confirmations.
    // The PSK is held pending (not persisted) until pair_confirm succeeds.
    String handleBegin(int seq, JsonDocument& in) {
        if (!isArmed()) {
            return buildErr(seq, ERR_NOT_PAIRING, "NOT_PAIRING", "not in pairing mode");
        }
        const char* cpubHex = in["controller_pub"] | "";
        if (strlen(cpubHex) != 64 || !isHex(cpubHex, 64)) {
            return buildErr(seq, ERR_PAIRING_FAILED, "PAIRING_FAILED", "pairing failed");
        }
        uint8_t ctrlPub[32];
        hexToBytes(cpubHex, ctrlPub, 32);

        uint8_t shared[32];
        if (!x25519(privKey_, ctrlPub, shared)) {
            return buildErr(seq, ERR_PAIRING_FAILED, "PAIRING_FAILED", "pairing failed");
        }

        derive(shared, salt_, ctrlPub, pubKey_, pinStr_,
               pendingPsk_, pendingConfirmC_, pendingConfirmD_);
        memset(shared, 0, sizeof(shared));
        havePending_ = true;

        char devPubHex[65];
        bytesToHex(pubKey_, 32, devPubHex);
        char saltHex[33];
        bytesToHex(salt_, PAIR_SALT_LEN, saltHex);

        JsonDocument resp;
        resp["type"] = "pair_begin_response";
        if (seq > 0) resp["seq"] = seq;
        resp["device_pub"] = devPubHex;
        resp["salt"] = saltHex;
        String s;
        serializeJson(resp, s);
        s += "\n";
        return s;
    }

    // pair_confirm: verify the controller's confirmation (constant time). On
    // success, hand back the PSK, mark paired, and close the window. On any
    // failure, close the window (one-shot). Either way returns the reply JSON.
    String handleConfirm(int seq, JsonDocument& in, uint8_t pskOut[16], bool& paired) {
        paired = false;
        if (!havePending_ || !isArmed()) {
            closeWindow();
            return buildErr(seq, ERR_PAIRING_FAILED, "PAIRING_FAILED", "pairing failed");
        }

        const char* cHex = in["confirm"] | "";
        if (strlen(cHex) != 32 || !isHex(cHex, 32)) {
            closeWindow();
            return buildErr(seq, ERR_PAIRING_FAILED, "PAIRING_FAILED", "pairing failed");
        }
        uint8_t got[16];
        hexToBytes(cHex, got, 16);

        if (!constTimeEq(got, pendingConfirmC_, PAIR_CONFIRM_LEN)) {
            closeWindow();
            return buildErr(seq, ERR_PAIRING_FAILED, "PAIRING_FAILED", "pairing failed");
        }

        // Success: export the PSK and the device confirmation.
        memcpy(pskOut, pendingPsk_, PAIR_PSK_LEN);
        char confHex[33];
        bytesToHex(pendingConfirmD_, PAIR_CONFIRM_LEN, confHex);
        paired = true;
        closeWindow();

        JsonDocument resp;
        resp["type"] = "pair_complete";
        if (seq > 0) resp["seq"] = seq;
        resp["confirm"] = confHex;
        resp["device_id"] = deviceId_;
        String s;
        serializeJson(resp, s);
        s += "\n";
        return s;
    }

private:
    bool armed_;
    bool havePending_;
    uint32_t windowExpiryMs_;

    uint8_t privKey_[32];   // ephemeral device X25519 private scalar
    uint8_t pubKey_[32];    // ephemeral device X25519 public key
    uint8_t salt_[PAIR_SALT_LEN];
    char pinStr_[PAIR_PIN_DIGITS + 1];
    char deviceId_[48];

    // Derived material held between pair_begin and pair_confirm.
    uint8_t pendingPsk_[PAIR_PSK_LEN];
    uint8_t pendingConfirmC_[PAIR_CONFIRM_LEN];
    uint8_t pendingConfirmD_[PAIR_CONFIRM_LEN];

    // Close the window and wipe pending secrets (PSK/kc never persist here).
    void closeWindow() {
        armed_ = false;
        havePending_ = false;
        memset(pendingPsk_, 0, sizeof(pendingPsk_));
        memset(pendingConfirmC_, 0, sizeof(pendingConfirmC_));
        memset(pendingConfirmD_, 0, sizeof(pendingConfirmD_));
    }

    static void randomBytes(uint8_t* buf, int n) {
        for (int i = 0; i < n; i++) buf[i] = (uint8_t)esp_random();
    }

    // mbedTLS RNG callback (coordinate blinding for the scalar multiply).
    static int rngCb(void* /*ctx*/, unsigned char* out, size_t len) {
        for (size_t i = 0; i < len; i++) out[i] = (uint8_t)esp_random();
        return 0;
    }

    static bool isHex(const char* s, int n) {
        for (int i = 0; i < n; i++) {
            char c = s[i];
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')))
                return false;
        }
        return true;
    }

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

    static bool constTimeEq(const uint8_t* a, const uint8_t* b, int n) {
        uint8_t d = 0;
        for (int i = 0; i < n; i++) d |= (uint8_t)(a[i] ^ b[i]);
        return d == 0;
    }

    // X25519 scalar multiply. peerPub == nullptr uses the base point (public
    // key from a private scalar). Byte order is RFC 7748 little-endian to match
    // Python `cryptography` (and the pinned vectors). Returns true on success.
    static bool x25519(const uint8_t priv[32], const uint8_t* peerPub, uint8_t out[32]) {
        mbedtls_ecp_group grp;
        mbedtls_mpi d;
        mbedtls_ecp_point Q, P;
        mbedtls_ecp_group_init(&grp);
        mbedtls_mpi_init(&d);
        mbedtls_ecp_point_init(&Q);
        mbedtls_ecp_point_init(&P);

        uint8_t clamped[32];
        memcpy(clamped, priv, 32);
        clamped[0]  &= 248;     // RFC 7748 clamp
        clamped[31] &= 127;
        clamped[31] |= 64;

        bool ok = false;
        if (mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_CURVE25519) == 0 &&
            mbedtls_mpi_read_binary_le(&d, clamped, 32) == 0) {

            const mbedtls_ecp_point* base = &grp.G;
            bool baseOk = true;
            if (peerPub) {
                uint8_t u[32];
                memcpy(u, peerPub, 32);
                u[31] &= 0x7f;  // mask u-coordinate high bit (RFC 7748)
                baseOk = (mbedtls_mpi_read_binary_le(&P.MBEDTLS_PRIVATE(X), u, 32) == 0 &&
                          mbedtls_mpi_lset(&P.MBEDTLS_PRIVATE(Z), 1) == 0);
                base = &P;
            }

            if (baseOk &&
                mbedtls_ecp_mul(&grp, &Q, &d, base, rngCb, nullptr) == 0 &&
                mbedtls_mpi_write_binary_le(&Q.MBEDTLS_PRIVATE(X), out, 32) == 0) {
                ok = true;
            }
        }

        mbedtls_mpi_free(&d);
        mbedtls_ecp_point_free(&Q);
        mbedtls_ecp_point_free(&P);
        mbedtls_ecp_group_free(&grp);
        memset(clamped, 0, sizeof(clamped));
        return ok;
    }

    // HKDF-SHA256 + HMAC key confirmation. Byte-identical to pairing.derive.
    static void derive(const uint8_t shared[32], const uint8_t salt[16],
                       const uint8_t ctrlPub[32], const uint8_t devPub[32],
                       const char* pin,
                       uint8_t pskOut[16], uint8_t confirmC[16], uint8_t confirmD[16]) {
        uint8_t info[84];
        int n = 0;
        memcpy(info + n, "ltp-pair-v1", 11); n += 11;   // 11 bytes, no NUL
        info[n++] = 0x00;
        memcpy(info + n, ctrlPub, 32); n += 32;
        memcpy(info + n, devPub, 32); n += 32;
        memcpy(info + n, pin, PAIR_PIN_DIGITS); n += PAIR_PIN_DIGITS;  // 84

        const mbedtls_md_info_t* md = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
        uint8_t okm[48];
        mbedtls_hkdf(md, salt, PAIR_SALT_LEN, shared, 32, info, (size_t)n, okm, 48);

        memcpy(pskOut, okm, PAIR_PSK_LEN);

        uint8_t kc[32];
        memcpy(kc, okm + PAIR_PSK_LEN, 32);   // okm[16:48]

        uint8_t mac[32];
        mbedtls_md_hmac(md, kc, 32, (const uint8_t*)"controller-confirm", 18, mac);
        memcpy(confirmC, mac, PAIR_CONFIRM_LEN);
        mbedtls_md_hmac(md, kc, 32, (const uint8_t*)"device-confirm", 14, mac);
        memcpy(confirmD, mac, PAIR_CONFIRM_LEN);

        memset(okm, 0, sizeof(okm));
        memset(kc, 0, sizeof(kc));
        memset(mac, 0, sizeof(mac));
        memset(info, 0, sizeof(info));
    }

    String buildErr(int seq, int code, const char* error, const char* message) {
        JsonDocument resp;
        resp["type"] = "error";
        if (seq > 0) resp["seq"] = seq;
        resp["code"] = code;
        resp["error"] = error;
        resp["message"] = message;
        String s;
        serializeJson(resp, s);
        s += "\n";
        return s;
    }
};

#endif // LTP_DEVICE_PAIRING_H
