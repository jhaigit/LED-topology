"""Tests for X25519 + PIN device pairing (protocol 0.3, Phase 4b)."""

import pytest

from libltp.pairing import (
    ControllerPairing,
    PairingError,
    ReferenceDevice,
    derive,
    generate_pin,
    x25519_shared,
)


def _run(controller: ControllerPairing, device: ReferenceDevice, pin: str) -> bytes:
    """Drive the four-message handshake, return the controller's PSK."""
    begin = controller.begin()
    resp = device.on_begin(begin)
    confirm = controller.on_begin_response(resp, pin)
    complete = device.on_confirm(confirm)
    return controller.on_complete(complete)


def test_happy_path_both_sides_agree():
    controller = ControllerPairing()
    device = ReferenceDevice()
    psk = _run(controller, device, device.pin)
    assert len(psk) == 16
    assert psk == device.psk  # both sides derived the same key


def test_wrong_pin_fails_at_device():
    controller = ControllerPairing()
    device = ReferenceDevice(pin="12345678")
    begin = controller.begin()
    resp = device.on_begin(begin)
    confirm = controller.on_begin_response(resp, "87654321")  # operator mistyped
    with pytest.raises(PairingError):
        device.on_confirm(confirm)


def test_mitm_substituted_device_key_fails():
    controller = ControllerPairing()
    device = ReferenceDevice()
    attacker = ReferenceDevice(pin=device.pin, salt=device.salt)

    begin = controller.begin()
    # Attacker relays its own public key to the controller.
    forged = attacker.on_begin(begin)
    confirm = controller.on_begin_response(forged, device.pin)
    # The real device never sees a matching confirmation; and the attacker,
    # lacking the controller's private key, cannot complete without the PIN
    # brute force. Feeding the controller's confirm to the real device fails.
    real_resp = device.on_begin(begin)  # noqa: F841 - device computed its own state
    with pytest.raises(PairingError):
        device.on_confirm(confirm)


def test_pin_format():
    for _ in range(20):
        pin = generate_pin()
        assert len(pin) == 8 and pin.isdigit()


def test_shared_secret_is_symmetric():
    from libltp.pairing import generate_keypair

    a_priv, a_pub = generate_keypair()
    b_priv, b_pub = generate_keypair()
    assert x25519_shared(a_priv, b_pub) == x25519_shared(b_priv, a_pub)


def test_fixed_interop_vector():
    """Pins the derivation to fixed inputs so the ESP32 mbedTLS build can be
    verified against the exact same PSK/confirmation. If this changes, the C
    implementation and arduino/.../device_pairing.h MUST change in lockstep."""
    # Fixed raw X25519 private scalars (clamped internally by X25519).
    c_priv = bytes(range(32))
    d_priv = bytes(range(32, 64))
    c_pub = _pub_of(c_priv)
    d_pub = _pub_of(d_priv)
    salt = bytes.fromhex("00112233445566778899aabbccddeeff")
    pin = "01234567"

    shared_c = x25519_shared(c_priv, d_pub)
    shared_d = x25519_shared(d_priv, c_pub)
    assert shared_c == shared_d

    psk, confirm_c, confirm_d = derive(shared_c, salt, c_pub, d_pub, pin)

    # Expected values — these are the cross-language interop vectors.
    assert psk.hex() == EXP_PSK
    assert confirm_c.hex() == EXP_CONFIRM_C
    assert confirm_d.hex() == EXP_CONFIRM_D


def _pub_of(priv: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return (
        X25519PrivateKey.from_private_bytes(priv)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )


# Cross-language interop vectors (Python == ESP32 mbedTLS). Inputs:
#   c_priv = bytes(0..31), d_priv = bytes(32..63),
#   salt   = 00112233445566778899aabbccddeeff, pin = "01234567"
#   c_pub  = 8f40c5adb68f25624ae5b214ea767a6ec94d829d3d7b5e1ad1ba6f3e2138285f
#   d_pub  = 358072d6365880d1aeea329adf9121383851ed21a28e3b75e965d0d2cd166254
#   shared = 9663aa1da97e848a914a436d04163dfbb89178f107f1b5b77ed3854203382854
EXP_PSK = "83962a754f05995f35965aa40075e7f2"
EXP_CONFIRM_C = "38d491461f147c3b134cc5aecd9c6786"
EXP_CONFIRM_D = "27ffdbbd22b54175887878e8d0ccc1f1"
