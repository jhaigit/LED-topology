"""Fleet-side enrollment: identity, trust store, and the inbound control endpoint.

A serial-sink fleet gains a static X25519 identity and a tiny newline-JSON
control endpoint that a controller enrolls against (Phase 5.1). Enrollment pins
the controller (trust-on-first-use) and derives a long-lived channel key that
Phase 5.2 will use to push per-device PSKs. See docs/proposals/fleet-enrollment.md.

Phase 5.2 adds device-PSK provisioning over the enrollment-derived channel: the
controller pushes keys the fleet applies to its running devices, persisted in a
writable store (so hand-maintained serial-fleet.yaml stays untouched).

Persistence:
  ~/.config/ltp/fleet-identity        the static private key (0600, via Identity)
  ~/.config/ltp/fleet-trust.yaml       pinned controller pub + channel key (0600)
  ~/.config/ltp/fleet-provisioned.yaml controller-pushed per-device PSKs (0600)
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from libltp.fleet_channel import (
    DIR_C2F,
    DIR_F2C,
    ChannelError,
    build_result,
    new_challenge,
    new_nonce,
    open_,
    parse_provision,
    seal,
)
from libltp.fleet_enroll import EnrollError, FleetEnroller
from libltp.identity import Identity, config_dir
from libltp.protocol import Message
from libltp.types import ErrorCode, MessageType

logger = logging.getLogger(__name__)

# apply(device_id, psk_hex_or_None) -> (ok, human message)
ProvisionApply = Callable[[str, "str | None"], Awaitable["tuple[bool, str]"]]


class FleetTrustStore:
    """Persists the one controller this fleet is enrolled to (TOFU) and the
    derived channel key. `reset()` clears the pin so a new controller can enroll.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "fleet-trust.yaml")
        self.controller_pub: bytes | None = None
        self.channel_key: bytes | None = None

    def load(self) -> None:
        if not self.path.exists():
            return
        data = yaml.safe_load(self.path.read_text()) or {}
        cp = data.get("controller_pub")
        ck = data.get("channel_key")
        try:
            self.controller_pub = bytes.fromhex(cp) if cp else None
            self.channel_key = bytes.fromhex(ck) if ck else None
        except (ValueError, TypeError):
            logger.error(f"Fleet trust store {self.path} corrupt; ignoring")
            self.controller_pub = self.channel_key = None
        if self.controller_pub:
            logger.info(f"Fleet enrolled to controller {self.controller_pub.hex()[:16]}…")

    def save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if self.controller_pub:
            data["controller_pub"] = self.controller_pub.hex()
        if self.channel_key:
            data["channel_key"] = self.channel_key.hex()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, yaml.safe_dump(data).encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(self.path, 0o600)

    def pin(self, controller_pub: bytes, channel_key: bytes) -> None:
        self.controller_pub = controller_pub
        self.channel_key = channel_key
        self.save()

    def reset(self) -> None:
        self.controller_pub = None
        self.channel_key = None
        if self.path.exists():
            self.path.unlink()
        logger.info("Fleet trust reset — ready to re-enroll")

    @property
    def is_enrolled(self) -> bool:
        return self.controller_pub is not None


class FleetProvisionStore:
    """Controller-pushed per-device PSKs (Phase 5.2), keyed by device UUID.

    Kept separate from the hand-maintained serial-fleet.yaml so pushed keys
    survive restarts without the fleet ever rewriting the operator's config.
    Stored 0600 (the PSK is secret)."""

    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "fleet-provisioned.yaml")
        self._keys: dict[str, str] = {}  # device_id -> psk hex

    def load(self) -> None:
        if not self.path.exists():
            return
        data = yaml.safe_load(self.path.read_text()) or {}
        for did, hexkey in (data.get("devices") or {}).items():
            try:
                if len(bytes.fromhex(hexkey)) == 16:
                    self._keys[str(did)] = str(hexkey)
            except (ValueError, TypeError):
                logger.error(f"Provision store: bad key for {did}")
        logger.info(f"Provision store: loaded {len(self._keys)} pushed key(s)")

    def save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        data = {"devices": dict(self._keys)}
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, yaml.safe_dump(data).encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(self.path, 0o600)

    def get(self, device_id: str) -> str | None:
        return self._keys.get(device_id)

    def set(self, device_id: str, psk_hex: str) -> None:
        self._keys[device_id] = psk_hex
        self.save()

    def remove(self, device_id: str) -> bool:
        if device_id in self._keys:
            del self._keys[device_id]
            self.save()
            return True
        return False


class FleetEnrollServer:
    """Small asyncio TCP server speaking newline-JSON `Message` frames. Handles
    enrollment (FLEET_ENROLL_REQUEST) and, once enrolled, encrypted device-PSK
    provisioning (FLEET_PROVISION_BEGIN, Phase 5.2).
    """

    def __init__(
        self,
        identity: Identity,
        trust: FleetTrustStore,
        port: int = 0,
        advertiser: Any = None,
        apply_fn: ProvisionApply | None = None,
    ):
        self.identity = identity
        self.trust = trust
        self.port = port
        # Optional mDNS advertiser whose `enrolled` TXT flag is refreshed when a
        # controller pins us, so discovery reflects the live trust state.
        self.advertiser = advertiser
        # Applies a pushed PSK to the running fleet; None disables provisioning.
        self.apply_fn = apply_fn
        self._server: asyncio.Server | None = None

    @property
    def bound_port(self) -> int:
        if self._server and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self.port

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, host="0.0.0.0", port=self.port
        )
        self.port = self.bound_port
        logger.info(
            f"Fleet enroll endpoint on :{self.port} "
            f"(identity {self.identity.fingerprint}, "
            f"{'enrolled' if self.trust.is_enrolled else 'un-enrolled'})"
        )

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not line:
                return
            msg = Message.from_bytes(line)
            if msg.type == MessageType.FLEET_ENROLL_REQUEST:
                await self._handle_enroll(msg, writer, peer)
            elif msg.type == MessageType.FLEET_PROVISION_BEGIN:
                await self._handle_provision(msg, reader, writer, peer)
            else:
                await self._send_error(
                    writer, msg.seq, ErrorCode.INVALID_FORMAT, "unexpected message"
                )
        except (TimeoutError, asyncio.TimeoutError):
            logger.debug(f"Connection from {peer} timed out")
        except Exception as exc:  # noqa: BLE001 - endpoint must not crash the fleet
            logger.error(f"Fleet endpoint error from {peer}: {exc!r}")
            await self._send_error(writer, None, ErrorCode.INTERNAL, str(exc))
        finally:
            writer.close()

    async def _handle_enroll(self, msg: Message, writer: Any, peer: Any) -> None:
        try:
            enroller = FleetEnroller(
                self.identity.private_key,
                self.identity.public_key,
                self.trust.controller_pub,
            )
            resp, channel_key, controller_pub = enroller.on_request(
                {"controller_pub": msg.data.get("controller_pub", "")}
            )
            # TOFU pin (idempotent if the same controller re-enrolls).
            self.trust.pin(controller_pub, channel_key)
            reply = Message(
                MessageType.FLEET_ENROLL_RESPONSE,
                msg.seq,
                fleet_pub=resp["fleet_pub"],
                confirm=resp["confirm"],
                fingerprint=self.identity.fingerprint,
            )
            writer.write(reply.to_bytes())
            await writer.drain()
            logger.info(f"Enrolled controller {controller_pub.hex()[:16]}… from {peer}")
            # Refresh the advert so discovery/UI show enrolled=1 without a restart.
            if self.advertiser is not None:
                try:
                    await self.advertiser.update_properties(enrolled="1")
                except Exception as exc:  # noqa: BLE001 - advert refresh is best-effort
                    logger.warning(f"Advert refresh after enroll failed: {exc}")
        except EnrollError as exc:
            logger.warning(f"Enrollment from {peer} rejected: {exc}")
            await self._send_error(writer, None, ErrorCode.ENROLL_REJECTED, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Enroll handler error from {peer}: {exc!r}")
            await self._send_error(
                writer, None, ErrorCode.INTERNAL, f"enroll failed: {exc}"
            )

    async def _handle_provision(
        self, begin: Message, reader: Any, writer: Any, peer: Any
    ) -> None:
        """Encrypted device-PSK push over the channel key. Challenge-response
        binds the request to this session (anti-replay); the payload is AEAD-
        sealed so the PSK never appears in cleartext."""
        key = self.trust.channel_key
        if not self.trust.is_enrolled or key is None:
            await self._send_error(writer, None, ErrorCode.UNAUTHORIZED, "not enrolled")
            return
        if self.apply_fn is None:
            await self._send_error(
                writer, None, ErrorCode.INTERNAL, "provisioning not available"
            )
            return
        # Only the pinned controller may provision.
        try:
            controller_pub = bytes.fromhex(begin.data.get("controller_pub", ""))
        except ValueError:
            await self._send_error(writer, None, ErrorCode.INVALID_FORMAT, "bad key")
            return
        if controller_pub != self.trust.controller_pub:
            await self._send_error(
                writer, None, ErrorCode.UNAUTHORIZED, "not the enrolled controller"
            )
            return

        challenge = new_challenge()
        writer.write(
            Message(
                MessageType.FLEET_PROVISION_CHALLENGE, begin.seq, challenge=challenge.hex()
            ).to_bytes()
        )
        await writer.drain()

        line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        if not line:
            return
        pmsg = Message.from_bytes(line)
        if pmsg.type != MessageType.FLEET_PROVISION:
            await self._send_error(
                writer, pmsg.seq, ErrorCode.INVALID_FORMAT, "expected fleet_provision"
            )
            return
        try:
            nonce = bytes.fromhex(pmsg.data.get("nonce", ""))
            ct = bytes.fromhex(pmsg.data.get("ct", ""))
            payload = parse_provision(open_(key, DIR_C2F, nonce, ct))
            if bytes.fromhex(payload["challenge"]) != challenge:
                raise ChannelError("stale or mismatched challenge")
        except (ChannelError, ValueError) as exc:
            logger.warning(f"Provision from {peer} rejected: {exc}")
            await self._send_error(writer, None, ErrorCode.UNAUTHORIZED, str(exc))
            return

        ok, message = await self.apply_fn(payload["device_id"], payload.get("psk"))
        rnonce = new_nonce()
        rct = seal(key, DIR_F2C, build_result(ok, message), rnonce)
        writer.write(
            Message(
                MessageType.FLEET_PROVISION_RESULT, begin.seq, nonce=rnonce.hex(), ct=rct.hex()
            ).to_bytes()
        )
        await writer.drain()
        logger.info(
            f"Provisioned {payload['device_id']} from {peer}: ok={ok} ({message})"
        )

    async def _send_error(
        self, writer: asyncio.StreamWriter, seq: int | None, code: ErrorCode, message: str
    ) -> None:
        err = Message(MessageType.ERROR, seq, code=int(code), message=message)
        try:
            writer.write(err.to_bytes())
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
