/**
 * NOTE: vendored copy of arduino/libraries/LtpSipHash/siphash.h — the Arduino
 * sketch build cannot resolve a ../ include, so this must stay byte-identical
 * to the canonical library header. Keep them in sync.
 *
 * SipHash-2-4 keyed MAC (C/C++, portable).
 *
 * The one MAC primitive shared across the LTP fleet — matches
 * src/libltp/siphash.py byte-for-byte. Used for the Layer 2 device-auth
 * claim handshake and per-message control MACs. Never on the pixel data
 * path. ~400 bytes of flash, ~64 bytes transient RAM; fits an ATmega328P.
 *
 * Header-only so it can be dropped into any sketch. Endianness-safe
 * (builds the 64-bit words byte-by-byte).
 */

#ifndef LTP_SIPHASH_H
#define LTP_SIPHASH_H

#include <stdint.h>
#include <stddef.h>

static inline uint64_t ltp_siphash_rotl(uint64_t x, int b) {
    return (x << b) | (x >> (64 - b));
}

// SipHash-2-4 of `inlen` bytes under a 16-byte key. Returns the 64-bit tag.
static inline uint64_t ltp_siphash24(const uint8_t key[16],
                                     const uint8_t* in, size_t inlen) {
    uint64_t k0 = 0, k1 = 0;
    for (int i = 0; i < 8; i++) k0 |= (uint64_t)key[i] << (8 * i);
    for (int i = 0; i < 8; i++) k1 |= (uint64_t)key[8 + i] << (8 * i);

    uint64_t v0 = k0 ^ 0x736f6d6570736575ULL;
    uint64_t v1 = k1 ^ 0x646f72616e646f6dULL;
    uint64_t v2 = k0 ^ 0x6c7967656e657261ULL;
    uint64_t v3 = k1 ^ 0x7465646279746573ULL;

#define LTP_SIPROUND()                     \
    do {                                   \
        v0 += v1; v1 = ltp_siphash_rotl(v1, 13); v1 ^= v0; v0 = ltp_siphash_rotl(v0, 32); \
        v2 += v3; v3 = ltp_siphash_rotl(v3, 16); v3 ^= v2;                                \
        v0 += v3; v3 = ltp_siphash_rotl(v3, 21); v3 ^= v0;                                \
        v2 += v1; v1 = ltp_siphash_rotl(v1, 17); v1 ^= v2; v2 = ltp_siphash_rotl(v2, 32); \
    } while (0)

    const size_t left = inlen & 7;
    const uint8_t* end = in + (inlen - left);
    uint64_t b = (uint64_t)inlen << 56;

    for (; in != end; in += 8) {
        uint64_t m = 0;
        for (int i = 0; i < 8; i++) m |= (uint64_t)in[i] << (8 * i);
        v3 ^= m;
        LTP_SIPROUND();
        LTP_SIPROUND();
        v0 ^= m;
    }

    for (size_t i = 0; i < left; i++) {
        b |= (uint64_t)in[i] << (8 * i);
    }

    v3 ^= b;
    LTP_SIPROUND();
    LTP_SIPROUND();
    v0 ^= b;

    v2 ^= 0xff;
    LTP_SIPROUND();
    LTP_SIPROUND();
    LTP_SIPROUND();
    LTP_SIPROUND();

#undef LTP_SIPROUND

    return v0 ^ v1 ^ v2 ^ v3;
}

// Write the tag as 16 lowercase hex chars + NUL (little-endian byte order,
// matching siphash24_hex in Python). `out` must hold at least 17 bytes.
static inline void ltp_siphash24_hex(const uint8_t key[16],
                                     const uint8_t* in, size_t inlen,
                                     char* out) {
    uint64_t tag = ltp_siphash24(key, in, inlen);
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 8; i++) {
        uint8_t byte = (uint8_t)(tag >> (8 * i));
        out[i * 2] = hex[byte >> 4];
        out[i * 2 + 1] = hex[byte & 0xf];
    }
    out[16] = '\0';
}

#endif // LTP_SIPHASH_H
