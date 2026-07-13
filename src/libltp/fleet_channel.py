"""Encrypted controller<->fleet provisioning channel (Phase 5.2).

After enrollment (fleet_enroll.py) both sides hold a shared 32-byte channel
key. This module carries device-PSK provisioning over that channel so the key
never appears in cleartext on the wire. See docs/proposals/fleet-enrollment.md.

Design:
- AEAD is ChaCha20-Poly1305 (IETF, 12-byte nonce). The proposal named
  XChaCha20-Poly1305, but `cryptography` 49 does not expose it; ChaCha20-
  Poly1305 with a per-direction subkey + random nonce is equivalent here given
  the very low message volume and the challenge below.
- Per-direction subkeys (controller->fleet, fleet->controller) are HKDF-derived
  from the channel key, so the two directions never share a (key, nonce) space.
- Freshness/anti-replay: the fleet issues a random single-use `challenge`; the
  controller echoes it inside the *encrypted* provision payload. A replayed or
  stale ciphertext carries the wrong challenge and is rejected. No persisted
  counters, so a controller or fleet restart can't desync the channel.

Wire exchange (all frames are newline-JSON `Message`s on the fleet endpoint):
    C->F  FLEET_PROVISION_BEGIN     {controller_pub}
    F->C  FLEET_PROVISION_CHALLENGE {challenge}
    C->F  FLEET_PROVISION           {nonce, ct}   # seal(c2f, {device_id, psk, challenge})
    F->C  FLEET_PROVISION_RESULT     {nonce, ct}   # seal(f2c, {ok, message})
"""

from __future__ import annotations

import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_INFO = b"ltp-fleet-provision-v1"
_AAD = b"ltp-fleet-provision-v1"
NONCE_LEN = 12
CHALLENGE_LEN = 16
DIR_C2F = b"c2f"  # controller -> fleet
DIR_F2C = b"f2c"  # fleet -> controller


class ChannelError(Exception):
    """Decryption/authentication failed, or a payload was malformed/stale."""


def _subkey(channel_key: bytes, direction: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO + b"|" + direction
    ).derive(channel_key)


def seal(channel_key: bytes, direction: bytes, plaintext: bytes, nonce: bytes) -> bytes:
    """AEAD-encrypt `plaintext` for one direction. `nonce` is 12 bytes."""
    return ChaCha20Poly1305(_subkey(channel_key, direction)).encrypt(
        nonce, plaintext, _AAD
    )


def open_(channel_key: bytes, direction: bytes, nonce: bytes, ct: bytes) -> bytes:
    """AEAD-decrypt a frame. Raises ChannelError on any failure."""
    try:
        return ChaCha20Poly1305(_subkey(channel_key, direction)).decrypt(
            nonce, ct, _AAD
        )
    except Exception as exc:  # cryptography raises InvalidTag etc.
        raise ChannelError(f"channel decrypt failed: {exc}") from exc


def new_nonce() -> bytes:
    return os.urandom(NONCE_LEN)


def new_challenge() -> bytes:
    return os.urandom(CHALLENGE_LEN)


# --- provision payload (controller -> fleet) -------------------------------


def build_provision(device_id: str, psk_hex: str | None, challenge: bytes) -> bytes:
    """Serialize the provision request plaintext. `psk_hex` None == disable auth."""
    return json.dumps(
        {"device_id": device_id, "psk": psk_hex, "challenge": challenge.hex()}
    ).encode("utf-8")


def parse_provision(plaintext: bytes) -> dict:
    """Parse + shape-check a decrypted provision request."""
    try:
        obj = json.loads(plaintext.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ChannelError(f"malformed provision payload: {exc}") from exc
    if not isinstance(obj, dict) or "device_id" not in obj or "challenge" not in obj:
        raise ChannelError("provision payload missing fields")
    psk = obj.get("psk")
    if psk is not None:
        try:
            if len(bytes.fromhex(psk)) != 16:
                raise ValueError("psk must be 16 bytes")
        except (ValueError, TypeError) as exc:
            raise ChannelError(f"bad psk: {exc}") from exc
    return obj


# --- provision result (fleet -> controller) --------------------------------


def build_result(ok: bool, message: str) -> bytes:
    return json.dumps({"ok": ok, "message": message}).encode("utf-8")


def parse_result(plaintext: bytes) -> dict:
    try:
        obj = json.loads(plaintext.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ChannelError(f"malformed provision result: {exc}") from exc
    if not isinstance(obj, dict) or "ok" not in obj:
        raise ChannelError("provision result missing fields")
    return obj
