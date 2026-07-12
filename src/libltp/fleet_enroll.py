"""Fleet enrollment: controller <-> serial-sink-fleet trust establishment.

A serial-sink fleet and a controller each hold a static X25519 *identity*
keypair. Enrollment pins the two identities to each other (trust-on-first-use)
and derives a long-lived **channel key** used later to carry device-key
provisioning confidentially (Phase 5.2). See docs/proposals/fleet-enrollment.md.

Trust model (Phase 5.1 = Pure TOFU): the fleet pins the FIRST controller that
enrolls; the controller pins the fleet public key it saw advertised (and shows
its fingerprint for optional out-of-band verification). `enroll --reset` on the
fleet clears the pin.

Channel-key derivation (static-static X25519; reproducible, interop-pinned):

    Z    = X25519(own_identity_priv, peer_identity_pub)     # 32 bytes
    key  = HKDF-SHA256(ikm=Z, salt=b"ltp-fleet-enroll-v1",
                       info=controller_pub(32) + fleet_pub(32), length=32)
    confirm = HMAC-SHA256(key, b"fleet-enroll-confirm")[0:16]

The confirmation lets each side prove it derived the same key (so a controller
that pinned a wrong/tampered fleet key is detected at enroll time). Roles are
fixed (controller vs fleet), so `info` is role-ordered, not sorted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from libltp.pairing import generate_keypair, x25519_shared

_SALT = b"ltp-fleet-enroll-v1"
_CONFIRM_LABEL = b"fleet-enroll-confirm"
CHANNEL_KEY_LEN = 32
CONFIRM_LEN = 16


def generate_identity() -> tuple[bytes, bytes]:
    """Return a static (private, public) X25519 identity keypair (raw 32-byte)."""
    return generate_keypair()


def fingerprint(public_key: bytes) -> str:
    """Short, human-comparable fingerprint of an identity public key.

    base32(SHA-256(pub))[:16] in four 4-char groups, e.g. AB12-CD34-EF56-GH78.
    Uppercase, no padding — easy to read off a console log and compare in the UI.
    """
    digest = hashlib.sha256(public_key).digest()
    b32 = base64.b32encode(digest).decode("ascii").rstrip("=")[:16]
    return "-".join(b32[i : i + 4] for i in range(0, 16, 4))


def derive_channel_key(
    own_private: bytes,
    peer_public: bytes,
    controller_pub: bytes,
    fleet_pub: bytes,
) -> bytes:
    """Long-lived channel key shared by controller and fleet after enrollment.

    Both sides pass their own private key and the peer's public key; the
    controller/fleet public keys are bound into `info` in fixed role order so
    both compute the identical key.
    """
    shared = x25519_shared(own_private, peer_public)
    info = controller_pub + fleet_pub
    return HKDF(
        algorithm=hashes.SHA256(), length=CHANNEL_KEY_LEN, salt=_SALT, info=info
    ).derive(shared)


def enroll_confirm(channel_key: bytes) -> bytes:
    """Key-confirmation tag proving both sides derived the same channel key."""
    return hmac.new(channel_key, _CONFIRM_LABEL, hashlib.sha256).digest()[:CONFIRM_LEN]


class EnrollError(Exception):
    """Enrollment failed (already enrolled, confirmation mismatch, malformed)."""


class ControllerEnroller:
    """Controller side of enrollment. The controller knows the fleet's public
    key from its advertisement (TOFU) before it starts.

        e = ControllerEnroller(ctrl_priv, ctrl_pub, fleet_pub_advertised)
        req = e.request()                       # -> {"controller_pub": hex}
        key = e.on_response(resp)               # -> 32-byte channel key, or raises
    """

    def __init__(self, identity_priv: bytes, identity_pub: bytes, fleet_pub: bytes):
        self._priv = identity_priv
        self.pub = identity_pub
        self._fleet_pub = fleet_pub
        self._channel_key: bytes | None = None

    def request(self) -> dict[str, str]:
        return {"controller_pub": self.pub.hex()}

    def on_response(self, resp: dict[str, str]) -> bytes:
        fleet_pub = bytes.fromhex(resp["fleet_pub"])
        # TOFU: the fleet key must be the one we saw advertised / are pinning.
        if fleet_pub != self._fleet_pub:
            raise EnrollError("fleet public key does not match the advertised key")
        key = derive_channel_key(self._priv, fleet_pub, self.pub, fleet_pub)
        got = bytes.fromhex(resp.get("confirm", ""))
        if not hmac.compare_digest(got, enroll_confirm(key)):
            raise EnrollError("enrollment confirmation mismatch (tampered key exchange?)")
        self._channel_key = key
        return key

    @property
    def channel_key(self) -> bytes | None:
        return self._channel_key


class FleetEnroller:
    """Fleet side of enrollment. Pins the first controller (TOFU); rejects a
    different controller until reset.

        f = FleetEnroller(fleet_priv, fleet_pub, pinned_controller_pub_or_None)
        resp = f.on_request(req)   # -> ({"fleet_pub", "confirm"}, channel_key,
                                   #     controller_pub) ; raises EnrollError
    """

    def __init__(
        self, identity_priv: bytes, identity_pub: bytes, pinned_controller: bytes | None
    ):
        self._priv = identity_priv
        self.pub = identity_pub
        self._pinned = pinned_controller

    def on_request(self, req: dict[str, str]) -> tuple[dict[str, str], bytes, bytes]:
        controller_pub = bytes.fromhex(req["controller_pub"])
        if len(controller_pub) != 32:
            raise EnrollError("bad controller public key")
        if self._pinned is not None and self._pinned != controller_pub:
            raise EnrollError("fleet already enrolled to a different controller (reset first)")
        key = derive_channel_key(self._priv, controller_pub, controller_pub, self.pub)
        resp = {"fleet_pub": self.pub.hex(), "confirm": enroll_confirm(key).hex()}
        return resp, key, controller_pub
