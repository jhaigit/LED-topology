"""Provisioning-channel crypto tests, with a pinned interop vector.

The pinned vector locks the subkey derivation + AEAD byte layout so any future
reimplementation can be cross-checked exactly (as test_pairing/test_fleet_enroll
do for their layers)."""

from __future__ import annotations

import pytest

from libltp.fleet_channel import (
    DIR_C2F,
    DIR_F2C,
    ChannelError,
    _subkey,
    build_provision,
    build_result,
    open_,
    parse_provision,
    parse_result,
    seal,
)

CK = bytes(range(32))
NONCE = bytes(range(12))
PT = b'{"device_id":"dev-1","psk":null,"challenge":"00112233445566778899aabbccddeeff"}'
EXP_SUBKEY_C2F = "5f2a3226b8be3d9a76ad2efcdc8072b9f31640a69ec18e3b453015e6760af866"
EXP_SUBKEY_F2C = "32ba0419dbb89a8879800f033f954cab6875cf85eec0d5ea41c067cd72b03dc8"
EXP_CT = (
    "08294e4abfb58b232feaf9487757c74d611b172c71342072ab197175c2c5458d53d8"
    "9066666447f9391cc3d09ec1a8d41f5f907ec8001e85f05540f14dbf7f2e5d4270fd"
    "d3cd0a0a443822d57ee35df0585abb35a3285e99313ce18fe4083e"
)


def test_pinned_vector():
    assert _subkey(CK, DIR_C2F).hex() == EXP_SUBKEY_C2F
    assert _subkey(CK, DIR_F2C).hex() == EXP_SUBKEY_F2C
    ct = seal(CK, DIR_C2F, PT, NONCE)
    assert ct.hex() == EXP_CT
    assert open_(CK, DIR_C2F, NONCE, ct) == PT


def test_directions_are_independent():
    ct = seal(CK, DIR_C2F, PT, NONCE)
    # A frame sealed c2f must not open as f2c (distinct subkeys).
    with pytest.raises(ChannelError):
        open_(CK, DIR_F2C, NONCE, ct)


def test_tamper_detected():
    ct = bytearray(seal(CK, DIR_C2F, PT, NONCE))
    ct[-1] ^= 0x01
    with pytest.raises(ChannelError):
        open_(CK, DIR_C2F, NONCE, bytes(ct))


def test_wrong_key_rejected():
    ct = seal(CK, DIR_C2F, PT, NONCE)
    with pytest.raises(ChannelError):
        open_(bytes([1]) + bytes(31), DIR_C2F, NONCE, ct)


def test_provision_payload_roundtrip():
    ch = bytes(range(16))
    pt = build_provision("dev-9", "00112233445566778899aabbccddeeff", ch)
    obj = parse_provision(pt)
    assert obj["device_id"] == "dev-9"
    assert obj["psk"] == "00112233445566778899aabbccddeeff"
    assert obj["challenge"] == ch.hex()


def test_provision_disable_payload():
    obj = parse_provision(build_provision("d", None, bytes(16)))
    assert obj["psk"] is None


def test_provision_rejects_bad_psk():
    with pytest.raises(ChannelError, match="psk"):
        parse_provision(build_provision("d", "abcd", bytes(16)))  # 2 bytes, not 16


def test_provision_rejects_missing_fields():
    import json

    with pytest.raises(ChannelError):
        parse_provision(json.dumps({"device_id": "d"}).encode())


def test_result_roundtrip():
    obj = parse_result(build_result(True, "applied"))
    assert obj["ok"] is True and obj["message"] == "applied"
