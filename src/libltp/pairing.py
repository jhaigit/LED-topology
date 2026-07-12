"""X25519 + PIN device pairing (protocol 0.3, Phase 4b).

Establishes the Layer 2 PSK on both controller and device *without the key
ever crossing the wire*: an ephemeral X25519 ECDH gives a shared secret, and
a short PIN shown on the device (its OLED) and typed into the controller
binds the exchange against a man-in-the-middle via mutual key confirmation.
After pairing, the derived PSK feeds the existing claim / SipHash-MAC path
unchanged.

Derivation — MUST stay byte-identical to the ESP32 mbedTLS implementation
(see arduino/ltp_esp32c3_oled/device_pairing.h):

    Z    = X25519(own_priv, peer_pub)                    # 32-byte shared secret
    okm  = HKDF-SHA256(ikm=Z, salt=salt16,
                       info=b"ltp-pair-v1\\x00"
                            + controller_pub(32) + device_pub(32)
                            + pin_ascii,
                       length=48)
    psk  = okm[0:16]     # the Layer 2 pre-shared key (persisted both sides)
    kc   = okm[16:48]    # key-confirmation key (ephemeral, never persisted)
    confirm_controller = HMAC-SHA256(kc, b"controller-confirm")[0:16]
    confirm_device     = HMAC-SHA256(kc, b"device-confirm")[0:16]

Mixing controller_pub + device_pub into `info` binds the full transcript
(defeats unknown-key-share); mixing the PIN binds the human presence factor.

Security envelope (MVP, per proposal §Key Management):
  - A passive eavesdropper sees only public points -> cannot derive Z or the PSK.
  - An online MITM must guess the PIN; a wrong PIN fails key confirmation and
    the device's armed window is one-shot, so it gets a single guess.
  - Residual risk: an active MITM who relays a full exchange can brute-force
    the PIN OFFLINE against the captured confirmation (the PIN is low entropy).
    An 8-digit PIN (10^8) raises the cost; SPAKE2/CPace removes this class of
    attack entirely and is the documented hardening path. For a home LAN this
    MVP defends fully against eavesdroppers and online attackers.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

_INFO_PREFIX = b"ltp-pair-v1\x00"
PIN_DIGITS = 8
SALT_LEN = 16
PSK_LEN = 16
CONFIRM_LEN = 16


def generate_keypair() -> tuple[bytes, bytes]:
    """Return (private_bytes, public_bytes), both raw 32-byte X25519 values."""
    priv = X25519PrivateKey.generate()
    priv_b = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_b = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv_b, pub_b


def x25519_shared(private_bytes: bytes, peer_public_bytes: bytes) -> bytes:
    """Raw 32-byte X25519 shared secret."""
    priv = X25519PrivateKey.from_private_bytes(private_bytes)
    return priv.exchange(X25519PublicKey.from_public_bytes(peer_public_bytes))


def generate_pin(digits: int = PIN_DIGITS) -> str:
    """A zero-padded decimal PIN (device-generated, shown on the OLED)."""
    upper = 10**digits
    return str(secrets.randbelow(upper)).zfill(digits)


def derive(
    shared: bytes,
    salt: bytes,
    controller_pub: bytes,
    device_pub: bytes,
    pin: str,
) -> tuple[bytes, bytes, bytes]:
    """Derive (psk, confirm_controller, confirm_device) from the ECDH output.

    Both sides call this with the same transcript; they agree iff the PIN
    matches and no key substitution happened.
    """
    info = _INFO_PREFIX + controller_pub + device_pub + pin.encode("ascii")
    okm = HKDF(algorithm=hashes.SHA256(), length=48, salt=salt, info=info).derive(shared)
    psk, kc = okm[:PSK_LEN], okm[PSK_LEN:]
    confirm_c = hmac.new(kc, b"controller-confirm", hashlib.sha256).digest()[:CONFIRM_LEN]
    confirm_d = hmac.new(kc, b"device-confirm", hashlib.sha256).digest()[:CONFIRM_LEN]
    return psk, confirm_c, confirm_d


class ControllerPairing:
    """Controller side of the handshake. Drives the four messages and yields
    the derived PSK only after the device's confirmation verifies.

    Usage:
        p = ControllerPairing()
        begin = p.begin()                       # -> {"controller_pub": hex}
        # send begin, receive {"device_pub", "salt"} from the device
        confirm = p.on_begin_response(resp, pin) # -> {"confirm": hex}
        # send confirm, receive {"confirm": hex} from the device
        psk = p.on_complete(complete)            # -> 16-byte PSK, or raises
    """

    def __init__(self) -> None:
        self._priv, self.pub = generate_keypair()
        self._psk: bytes | None = None
        self._confirm_device: bytes | None = None

    def begin(self) -> dict[str, str]:
        return {"controller_pub": self.pub.hex()}

    def on_begin_response(self, resp: dict[str, str], pin: str) -> dict[str, str]:
        device_pub = bytes.fromhex(resp["device_pub"])
        salt = bytes.fromhex(resp["salt"])
        shared = x25519_shared(self._priv, device_pub)
        psk, confirm_c, confirm_d = derive(shared, salt, self.pub, device_pub, pin)
        self._psk = psk
        self._confirm_device = confirm_d
        return {"confirm": confirm_c.hex()}

    def on_complete(self, complete: dict[str, str]) -> bytes:
        if self._psk is None or self._confirm_device is None:
            raise PairingError("pairing not started")
        got = bytes.fromhex(complete.get("confirm", ""))
        if not hmac.compare_digest(got, self._confirm_device):
            raise PairingError("device confirmation mismatch")
        return self._psk


class PairingError(Exception):
    """Raised when a pairing step fails (bad PIN, tampered transcript)."""


class ReferenceDevice:
    """Reference device role — mirrors what the ESP32 firmware does, used for
    tests and for generating the cross-language interop vectors. Not used in
    production (real devices run the C implementation)."""

    def __init__(self, pin: str | None = None, salt: bytes | None = None) -> None:
        self._priv, self.pub = generate_keypair()
        self.pin = pin or generate_pin()
        self.salt = salt or secrets.token_bytes(SALT_LEN)
        self._psk: bytes | None = None
        self._confirm_controller: bytes | None = None
        self._confirm_device: bytes | None = None

    def on_begin(self, begin: dict[str, str]) -> dict[str, str]:
        controller_pub = bytes.fromhex(begin["controller_pub"])
        shared = x25519_shared(self._priv, controller_pub)
        psk, confirm_c, confirm_d = derive(
            shared, self.salt, controller_pub, self.pub, self.pin
        )
        self._psk, self._confirm_controller, self._confirm_device = (
            psk,
            confirm_c,
            confirm_d,
        )
        return {"device_pub": self.pub.hex(), "salt": self.salt.hex()}

    def on_confirm(self, confirm: dict[str, str]) -> dict[str, str]:
        if self._confirm_controller is None:
            raise PairingError("pairing not started")
        got = bytes.fromhex(confirm.get("confirm", ""))
        if not hmac.compare_digest(got, self._confirm_controller):
            raise PairingError("controller confirmation mismatch (wrong PIN?)")
        assert self._confirm_device is not None
        return {"confirm": self._confirm_device.hex()}

    @property
    def psk(self) -> bytes | None:
        return self._psk
