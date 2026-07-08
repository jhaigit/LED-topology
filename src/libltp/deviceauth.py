"""Device claim / anti-hijack layer (proposal Layer 2, protocol 0.2).

Both halves of the scheme live here:

- DeviceAuthGuard — device side. Issues challenges, verifies claim proofs,
  holds the exclusive lease, and checks the per-message MAC on privileged
  commands. Drop-in front of a sink's normal message handler.
- ClaimSession — controller side. Runs the handshake against a guard-
  protected device, derives the session key, signs privileged messages,
  and renews the lease.

Security properties (see docs/proposals/security-access-control.md §2):
the PSK and the derived session key never cross the wire — only SipHash
outputs of them do. The session token is an identifier, not a bearer
credential: without the session key it authorizes nothing. Monotonic
per-message counters prevent replay. A second controller claiming a held
device gets LEASE_HELD; a crashed controller's lease simply expires.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from libltp.protocol import Message, error_message
from libltp.siphash import siphash24_hex
from libltp.types import ErrorCode, MessageType

logger = logging.getLogger(__name__)

# Messages that mutate device state and therefore require a MAC once a
# device enforces auth. INPUT_EVENT / *_RESPONSE / CONTROL_CHANGED flow
# device->controller and are never gated.
PRIVILEGED_TYPES = frozenset(
    {
        MessageType.CONTROL_SET,
        MessageType.STREAM_SETUP,
        MessageType.STREAM_CONTROL,
        MessageType.ROUTE_CREATE,
        MessageType.ROUTE_DELETE,
        MessageType.SUBSCRIBE,
    }
)

# Read-only requests, gated only when read_open=False.
READ_TYPES = frozenset(
    {
        MessageType.CAPABILITY_REQUEST,
        MessageType.CONTROL_GET,
        MessageType.PIXEL_READ,
    }
)

NONCE_TTL_SECONDS = 30.0
DEFAULT_LEASE_SECONDS = 30.0


class DeviceAuthError(Exception):
    """Claim handshake failed."""

    def __init__(self, code: ErrorCode, message: str, retry_after: float | None = None):
        self.code = code
        self.retry_after = retry_after
        super().__init__(message)


def _canonical(payload: dict[str, Any]) -> bytes:
    """Canonical JSON for MAC computation: sorted keys, no whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def compute_proof(psk: bytes, nonce: bytes, device_id: str, controller_id: str) -> str:
    return siphash24_hex(psk, nonce + device_id.encode() + controller_id.encode())


def compute_device_proof(psk: bytes, nonce: bytes) -> str:
    return siphash24_hex(psk, nonce + b"device")


def derive_session_key(psk: bytes, nonce: bytes) -> bytes:
    """16-byte session key from PSK + nonce. Never transmitted; both ends
    derive it independently after a successful claim."""
    return bytes.fromhex(siphash24_hex(psk, nonce + b"sk1")) + bytes.fromhex(
        siphash24_hex(psk, nonce + b"sk2")
    )


def message_mac(session_key: bytes, message: Message, token: str, counter: int) -> str:
    """MAC over the message type, auth envelope (sans mac), and body.

    seq is deliberately NOT covered: the client assigns it after signing
    (request() numbers messages on send), and the monotonic counter n
    already provides replay protection."""
    body = {k: v for k, v in message.data.items() if k != "auth"}
    payload = {
        "type": message.type.value,
        "token": token,
        "n": counter,
        "body": Message._serialize_values(body),
    }
    return siphash24_hex(session_key, _canonical(payload))


def sign_message(session_key: bytes, message: Message, token: str, counter: int) -> Message:
    """Attach the auth envelope {token, n, mac} to a message (in place)."""
    mac = message_mac(session_key, message, token, counter)
    message.data["auth"] = {"token": token, "n": counter, "mac": mac}
    return message


# ---------------------------------------------------------------------------
# Device side
# ---------------------------------------------------------------------------


@dataclass
class _Lease:
    owner_id: str
    token: str
    session_key: bytes
    expiry: float
    last_counter: int = 0
    owner_ip: str | None = None  # control-connection peer; binds the data plane


@dataclass
class DeviceAuthGuard:
    """Device-side enforcement. Sits in front of the normal message handler:

        response = guard.handle_message(message, peer_ip)
        if response is not None:
            return response          # guard handled (handshake) or rejected
        ... normal handler ...       # message is authorized (auth stripped)

    A guard with no PSK set is inert (Level 0): every message passes. This
    is the "Level-2 device with no key behaves as Level 0 until paired"
    compatibility rule.
    """

    psk: bytes | None = None
    device_id: str = ""
    lease_seconds: float = DEFAULT_LEASE_SECONDS
    read_open: bool = True  # read-only requests allowed without a claim
    lease: _Lease | None = field(default=None, init=False)
    # controller_id -> (nonce, issued_at); single-use, short TTL
    _nonces: dict[str, tuple[bytes, float]] = field(default_factory=dict, init=False)

    @property
    def enabled(self) -> bool:
        return self.psk is not None

    @property
    def claimed(self) -> bool:
        return self.lease is not None and time.monotonic() < self.lease.expiry

    @property
    def owner_id(self) -> str | None:
        return self.lease.owner_id if self.claimed and self.lease else None

    @property
    def owner_ip(self) -> str | None:
        return self.lease.owner_ip if self.claimed and self.lease else None

    def auth_info(self) -> dict[str, Any]:
        """The auth object for capability_response.device."""
        if not self.enabled:
            return {"mode": "none", "required": False, "claimed": False}
        return {"mode": "siphash", "required": True, "claimed": self.claimed}

    # -- handshake ---------------------------------------------------------

    def _issue_challenge(self, message: Message) -> Message:
        controller_id = str(message.data.get("controller_id", ""))
        nonce = secrets.token_bytes(16)
        now = time.monotonic()
        self._nonces = {cid: v for cid, v in self._nonces.items() if now - v[1] < NONCE_TTL_SECONDS}
        self._nonces[controller_id] = (nonce, now)
        return Message(
            MessageType.AUTH_CHALLENGE,
            message.seq,
            nonce=nonce.hex(),
            device_id=self.device_id,
        )

    def _handle_claim(self, message: Message, peer_ip: str | None) -> Message:
        assert self.psk is not None
        controller_id = str(message.data.get("controller_id", ""))
        proof = str(message.data.get("proof", ""))
        entry = self._nonces.pop(controller_id, None)
        if entry is None or time.monotonic() - entry[1] >= NONCE_TTL_SECONDS:
            return error_message(
                message.seq, ErrorCode.UNAUTHORIZED, "no valid challenge outstanding"
            )
        nonce = entry[0]

        expected = compute_proof(self.psk, nonce, self.device_id, controller_id)
        if not secrets.compare_digest(proof, expected):
            logger.warning(f"Claim with bad proof from '{controller_id}'")
            return error_message(message.seq, ErrorCode.UNAUTHORIZED, "invalid proof")

        # Exclusive lease: reject while held by a different, live owner.
        if self.claimed and self.lease is not None and self.lease.owner_id != controller_id:
            remaining = max(0.0, self.lease.expiry - time.monotonic())
            logger.warning(
                f"Claim from '{controller_id}' rejected: leased to "
                f"'{self.lease.owner_id}' for {remaining:.0f}s more"
            )
            msg = error_message(message.seq, ErrorCode.LEASE_HELD, "device is claimed")
            msg.data["retry_after"] = round(remaining, 1)
            return msg

        lease_req = float(message.data.get("lease_seconds", self.lease_seconds))
        lease_secs = min(max(lease_req, 5.0), 300.0)
        self.lease = _Lease(
            owner_id=controller_id,
            token=secrets.token_hex(16),
            session_key=derive_session_key(self.psk, nonce),
            expiry=time.monotonic() + lease_secs,
            owner_ip=peer_ip,
        )
        logger.info(f"Device claimed by '{controller_id}' (lease {lease_secs:.0f}s)")
        return Message(
            MessageType.CLAIM_RESPONSE,
            message.seq,
            token=self.lease.token,
            lease_seconds=lease_secs,
            device_proof=compute_device_proof(self.psk, nonce),
        )

    # -- per-message verification ------------------------------------------

    def _verify_auth(self, message: Message) -> Message | None:
        """None if the message's auth envelope is valid; error Message if not."""
        lease = self.lease
        if lease is None or not self.claimed:
            self.lease = None
            return error_message(message.seq, ErrorCode.UNAUTHORIZED, "device not claimed")
        auth = message.data.get("auth")
        if not isinstance(auth, dict):
            return error_message(message.seq, ErrorCode.UNAUTHORIZED, "auth required")
        token = str(auth.get("token", ""))
        counter = auth.get("n")
        mac = str(auth.get("mac", ""))
        if not secrets.compare_digest(token, lease.token):
            return error_message(message.seq, ErrorCode.UNAUTHORIZED, "bad token")
        if not isinstance(counter, int) or counter <= lease.last_counter:
            return error_message(message.seq, ErrorCode.UNAUTHORIZED, "counter replay")
        expected = message_mac(lease.session_key, message, token, counter)
        if not secrets.compare_digest(mac, expected):
            return error_message(message.seq, ErrorCode.UNAUTHORIZED, "bad mac")
        lease.last_counter = counter
        return None

    # -- entry point ---------------------------------------------------------

    def handle_message(self, message: Message, peer_ip: str | None = None) -> Message | None:
        """Returns a response if the guard consumed/rejected the message,
        None if it should proceed to the normal handler."""
        if not self.enabled:
            return None

        if message.type == MessageType.AUTH_CHALLENGE_REQUEST:
            return self._issue_challenge(message)
        if message.type == MessageType.CLAIM:
            return self._handle_claim(message, peer_ip)
        if message.type == MessageType.CLAIM_RENEW:
            err = self._verify_auth(message)
            if err is not None:
                return err
            assert self.lease is not None
            self.lease.expiry = time.monotonic() + self.lease_seconds
            return Message(
                MessageType.CLAIM_RENEW_RESPONSE,
                message.seq,
                lease_seconds=self.lease_seconds,
            )
        if message.type == MessageType.RELEASE:
            err = self._verify_auth(message)
            if err is not None:
                return err
            owner = self.lease.owner_id if self.lease else "?"
            self.lease = None
            logger.info(f"Device released by '{owner}'")
            return Message(MessageType.RELEASE_RESPONSE, message.seq)

        if message.type in PRIVILEGED_TYPES:
            err = self._verify_auth(message)
            if err is not None:
                logger.warning(
                    f"Rejected {message.type.value} from {peer_ip or '?'}: "
                    f"{err.data.get('message')}"
                )
                return err
            message.data.pop("auth", None)
            return None

        if message.type in READ_TYPES and not self.read_open:
            if self._verify_auth(message) is not None:
                return error_message(message.seq, ErrorCode.UNAUTHORIZED, "reads are gated")
            message.data.pop("auth", None)
            return None

        return None


# ---------------------------------------------------------------------------
# Controller side
# ---------------------------------------------------------------------------


class ClaimSession:
    """Controller-side claim session over a ControlClient.

    Usage:
        session = ClaimSession(client, psk, controller_id)
        await session.claim()
        await client.request(session.sign(control_set(0, {...})))
        ...
        await session.release()
    """

    def __init__(self, client: Any, psk: bytes, controller_id: str):
        self.client = client
        self.psk = psk
        self.controller_id = controller_id
        self.token: str | None = None
        self.session_key: bytes | None = None
        self.lease_seconds: float = DEFAULT_LEASE_SECONDS
        self.device_id: str = ""
        self._counter = 0
        self._renew_task: Any = None

    @property
    def is_claimed(self) -> bool:
        return self.token is not None

    def _next_counter(self) -> int:
        self._counter += 1
        return self._counter

    @staticmethod
    def _raise_on_error(response: Message, what: str) -> None:
        if response.type == MessageType.ERROR:
            code = ErrorCode(response.data.get("code", ErrorCode.INTERNAL))
            raise DeviceAuthError(
                code,
                f"{what}: {response.data.get('message', code.name)}",
                retry_after=response.data.get("retry_after"),
            )

    async def claim(self, lease_seconds: float = DEFAULT_LEASE_SECONDS) -> None:
        """Run challenge-response and take the lease. Raises DeviceAuthError
        with LEASE_HELD/UNAUTHORIZED on rejection."""
        challenge = await self.client.request(
            Message(MessageType.AUTH_CHALLENGE_REQUEST, controller_id=self.controller_id)
        )
        self._raise_on_error(challenge, "challenge")
        nonce = bytes.fromhex(str(challenge.data.get("nonce", "")))
        self.device_id = str(challenge.data.get("device_id", ""))

        response = await self.client.request(
            Message(
                MessageType.CLAIM,
                controller_id=self.controller_id,
                proof=compute_proof(self.psk, nonce, self.device_id, self.controller_id),
                lease_seconds=lease_seconds,
            )
        )
        self._raise_on_error(response, "claim")

        # Mutual auth: verify we spoke to the real key holder, not an mDNS
        # impostor that let the handshake through unverified.
        device_proof = str(response.data.get("device_proof", ""))
        if not secrets.compare_digest(device_proof, compute_device_proof(self.psk, nonce)):
            raise DeviceAuthError(ErrorCode.UNAUTHORIZED, "device proof invalid (impostor?)")

        self.token = str(response.data.get("token", ""))
        self.session_key = derive_session_key(self.psk, nonce)
        self.lease_seconds = float(response.data.get("lease_seconds", lease_seconds))
        self._counter = 0
        logger.info(f"Claimed device '{self.device_id}' (lease {self.lease_seconds:.0f}s)")

    def sign(self, message: Message) -> Message:
        """Attach the auth envelope to a privileged message."""
        if self.token is None or self.session_key is None:
            raise DeviceAuthError(ErrorCode.UNAUTHORIZED, "not claimed")
        return sign_message(self.session_key, message, self.token, self._next_counter())

    async def renew(self) -> None:
        response = await self.client.request(self.sign(Message(MessageType.CLAIM_RENEW)))
        self._raise_on_error(response, "renew")

    async def release(self) -> None:
        if not self.is_claimed:
            return
        try:
            response = await self.client.request(self.sign(Message(MessageType.RELEASE)))
            self._raise_on_error(response, "release")
        finally:
            self.token = None
            self.session_key = None
