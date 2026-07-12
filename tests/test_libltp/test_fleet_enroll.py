"""Fleet enrollment crypto + handshake tests, with a pinned interop vector.

The pinned vector locks the channel-key / confirmation byte layout so any
future reimplementation (e.g. a Go/Rust fleet, or a firmware port) can be
cross-checked against it exactly as test_pairing.py does for device pairing.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from libltp.fleet_enroll import (
    ControllerEnroller,
    EnrollError,
    FleetEnroller,
    derive_channel_key,
    enroll_confirm,
    fingerprint,
    generate_identity,
)


def _kp(seed: bytes) -> tuple[bytes, bytes]:
    p = X25519PrivateKey.from_private_bytes(seed)
    return (
        p.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()),
        p.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
    )


# --- Pinned interop vector (fixed seeds) -----------------------------------
CTRL_SEED = bytes(range(1, 33))
FLEET_SEED = bytes(range(33, 65))
EXP_CPUB = "07a37cbc142093c8b755dc1b10e86cb426374ad16aa853ed0bdfc0b2b86d1c7c"
EXP_FPUB = "5869aff450549732cbaaed5e5df9b30a6da31cb0e5742bad5ad4a1a768f1a67b"
EXP_KEY = "0c93e586e04bc4416d2f0baf8094b3888f641d9457f070027fd4a376c79c36db"
EXP_CONFIRM = "56f4d5de4f57f60a237ead8229db2ac3"
EXP_FPR_C = "VKUP-75YD-WUFS-FF7U"
EXP_FPR_F = "IRLR-GR4U-KWIY-EITH"


def test_pinned_interop_vector():
    cpriv, cpub = _kp(CTRL_SEED)
    fpriv, fpub = _kp(FLEET_SEED)
    assert cpub.hex() == EXP_CPUB
    assert fpub.hex() == EXP_FPUB
    key_c = derive_channel_key(cpriv, fpub, cpub, fpub)
    key_f = derive_channel_key(fpriv, cpub, cpub, fpub)
    assert key_c == key_f
    assert key_c.hex() == EXP_KEY
    assert enroll_confirm(key_c).hex() == EXP_CONFIRM
    assert fingerprint(cpub) == EXP_FPR_C
    assert fingerprint(fpub) == EXP_FPR_F


def test_both_sides_agree_random_identities():
    cpriv, cpub = generate_identity()
    fpriv, fpub = generate_identity()
    assert derive_channel_key(cpriv, fpub, cpub, fpub) == derive_channel_key(
        fpriv, cpub, cpub, fpub
    )


def test_fingerprint_shape():
    _, pub = generate_identity()
    fpr = fingerprint(pub)
    assert len(fpr) == 19  # 16 chars + 3 dashes
    assert fpr.count("-") == 3
    assert all(len(g) == 4 for g in fpr.split("-"))


def test_full_handshake_first_enrollment():
    cpriv, cpub = generate_identity()
    fpriv, fpub = generate_identity()
    ctrl = ControllerEnroller(cpriv, cpub, fpub)
    fleet = FleetEnroller(fpriv, fpub, pinned_controller=None)

    req = ctrl.request()
    resp, fleet_key, seen_controller = fleet.on_request(req)
    ctrl_key = ctrl.on_response(resp)

    assert ctrl_key == fleet_key
    assert seen_controller == cpub
    assert ctrl.channel_key == ctrl_key


def test_reenroll_same_controller_ok():
    cpriv, cpub = generate_identity()
    fpriv, fpub = generate_identity()
    fleet = FleetEnroller(fpriv, fpub, pinned_controller=cpub)
    ctrl = ControllerEnroller(cpriv, cpub, fpub)
    resp, _, _ = fleet.on_request(ctrl.request())
    assert ctrl.on_response(resp)  # succeeds — same pinned controller


def test_fleet_rejects_second_controller():
    _, other_pub = generate_identity()
    cpriv, cpub = generate_identity()
    fpriv, fpub = generate_identity()
    fleet = FleetEnroller(fpriv, fpub, pinned_controller=other_pub)
    ctrl = ControllerEnroller(cpriv, cpub, fpub)
    with pytest.raises(EnrollError, match="already enrolled"):
        fleet.on_request(ctrl.request())


def test_controller_rejects_wrong_fleet_key():
    cpriv, cpub = generate_identity()
    fpriv, fpub = generate_identity()
    _, decoy_pub = generate_identity()
    # Controller pinned a decoy fleet key but the real fleet answers.
    ctrl = ControllerEnroller(cpriv, cpub, decoy_pub)
    fleet = FleetEnroller(fpriv, fpub, pinned_controller=None)
    resp, _, _ = fleet.on_request(ctrl.request())
    with pytest.raises(EnrollError, match="does not match"):
        ctrl.on_response(resp)


def test_controller_detects_tampered_confirm():
    cpriv, cpub = generate_identity()
    fpriv, fpub = generate_identity()
    ctrl = ControllerEnroller(cpriv, cpub, fpub)
    fleet = FleetEnroller(fpriv, fpub, pinned_controller=None)
    resp, _, _ = fleet.on_request(ctrl.request())
    resp["confirm"] = "00" * 16
    with pytest.raises(EnrollError, match="confirmation mismatch"):
        ctrl.on_response(resp)


def test_fleet_rejects_malformed_controller_pub():
    fpriv, fpub = generate_identity()
    fleet = FleetEnroller(fpriv, fpub, pinned_controller=None)
    with pytest.raises(EnrollError, match="bad controller public key"):
        fleet.on_request({"controller_pub": "abcd"})
