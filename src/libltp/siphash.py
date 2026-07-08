"""SipHash-2-4 keyed MAC (pure Python).

Chosen for the device-auth layer (proposal §2.1) because the same algorithm
fits an ATmega328P in ~400 bytes of flash and ~64 bytes of transient RAM —
one MAC primitive across the whole fleet, from CPython to 2 KB AVRs. Used
for the claim handshake proof and per-message control MACs; never on the
per-frame data path.
"""

from __future__ import annotations

import struct

_MASK = 0xFFFFFFFFFFFFFFFF


def _rotl(x: int, b: int) -> int:
    return ((x << b) | (x >> (64 - b))) & _MASK


def siphash24(key: bytes, data: bytes) -> int:
    """SipHash-2-4 of data under a 16-byte key; returns a 64-bit int."""
    if len(key) != 16:
        raise ValueError("SipHash key must be exactly 16 bytes")

    k0, k1 = struct.unpack("<QQ", key)
    v0 = k0 ^ 0x736F6D6570736575
    v1 = k1 ^ 0x646F72616E646F6D
    v2 = k0 ^ 0x6C7967656E657261
    v3 = k1 ^ 0x7465646279746573

    def sipround() -> None:
        nonlocal v0, v1, v2, v3
        v0 = (v0 + v1) & _MASK
        v1 = _rotl(v1, 13)
        v1 ^= v0
        v0 = _rotl(v0, 32)
        v2 = (v2 + v3) & _MASK
        v3 = _rotl(v3, 16)
        v3 ^= v2
        v0 = (v0 + v3) & _MASK
        v3 = _rotl(v3, 21)
        v3 ^= v0
        v2 = (v2 + v1) & _MASK
        v1 = _rotl(v1, 17)
        v1 ^= v2
        v2 = _rotl(v2, 32)

    b = len(data) & 0xFF
    tail = len(data) & ~7
    for offset in range(0, tail, 8):
        (m,) = struct.unpack_from("<Q", data, offset)
        v3 ^= m
        sipround()
        sipround()
        v0 ^= m

    last = (b << 56) & _MASK
    rem = data[tail:]
    for i, byte in enumerate(rem):
        last |= byte << (8 * i)

    v3 ^= last
    sipround()
    sipround()
    v0 ^= last

    v2 ^= 0xFF
    sipround()
    sipround()
    sipround()
    sipround()

    return int((v0 ^ v1 ^ v2 ^ v3) & _MASK)


def siphash24_bytes(key: bytes, data: bytes) -> bytes:
    """SipHash-2-4 tag as 8 bytes little-endian (the wire/reference form)."""
    return struct.pack("<Q", siphash24(key, data))


def siphash24_hex(key: bytes, data: bytes) -> str:
    """SipHash-2-4 tag as 16 lowercase hex chars (little-endian byte order)."""
    return siphash24_bytes(key, data).hex()
