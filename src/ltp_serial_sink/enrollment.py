"""Fleet-side enrollment: identity, trust store, and the inbound control endpoint.

A serial-sink fleet gains a static X25519 identity and a tiny newline-JSON
control endpoint that a controller enrolls against (Phase 5.1). Enrollment pins
the controller (trust-on-first-use) and derives a long-lived channel key that
Phase 5.2 will use to push per-device PSKs. See docs/proposals/fleet-enrollment.md.

Persistence:
  ~/.config/ltp/fleet-identity     the static private key (0600, via Identity)
  ~/.config/ltp/fleet-trust.yaml   pinned controller pub + channel key (0600)
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from libltp.fleet_enroll import EnrollError, FleetEnroller
from libltp.identity import Identity, config_dir
from libltp.protocol import Message
from libltp.types import ErrorCode, MessageType

logger = logging.getLogger(__name__)


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


class FleetEnrollServer:
    """Small asyncio TCP server speaking newline-JSON `Message` frames. Accepts a
    single FLEET_ENROLL_REQUEST per connection and answers FLEET_ENROLL_RESPONSE.
    """

    def __init__(self, identity: Identity, trust: FleetTrustStore, port: int = 0):
        self.identity = identity
        self.trust = trust
        self.port = port
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
            if msg.type != MessageType.FLEET_ENROLL_REQUEST:
                await self._send_error(writer, msg.seq, ErrorCode.INVALID_FORMAT,
                                       "expected fleet_enroll_request")
                return
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
        except EnrollError as exc:
            logger.warning(f"Enrollment from {peer} rejected: {exc}")
            await self._send_error(writer, None, ErrorCode.ENROLL_REJECTED, str(exc))
        except (TimeoutError, asyncio.TimeoutError):
            logger.debug(f"Enroll connection from {peer} timed out")
        except Exception as exc:  # noqa: BLE001 - endpoint must not crash the fleet
            # Report rather than silently closing, so the controller shows a
            # real reason (e.g. a read-only config dir failing trust.pin) instead
            # of an opaque "fleet closed the connection".
            logger.error(f"Enroll handler error from {peer}: {exc!r}")
            await self._send_error(
                writer, None, ErrorCode.INTERNAL, f"enroll failed: {exc}"
            )
        finally:
            writer.close()

    async def _send_error(
        self, writer: asyncio.StreamWriter, seq: int | None, code: ErrorCode, message: str
    ) -> None:
        err = Message(MessageType.ERROR, seq, code=int(code), message=message)
        try:
            writer.write(err.to_bytes())
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
