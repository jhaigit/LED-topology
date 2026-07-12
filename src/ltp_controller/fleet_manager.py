"""Controller-side fleet trust: discovery, enrollment client, and pinned store.

The controller discovers serial-sink fleets advertising `_ltp-fleet._tcp`,
lets an admin enroll one (static-static X25519, TOFU — Phase 5.1), and pins the
result. The derived channel key is stored for Phase 5.2 device-key provisioning.
See docs/proposals/fleet-enrollment.md and libltp/fleet_enroll.py.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from libltp.fleet_channel import (
    DIR_C2F,
    DIR_F2C,
    ChannelError,
    build_provision,
    new_nonce,
    open_,
    parse_result,
    seal,
)
from libltp.fleet_enroll import ControllerEnroller, EnrollError, fingerprint
from libltp.identity import Identity, config_dir
from libltp.protocol import Message
from libltp.types import MessageType

logger = logging.getLogger(__name__)


class ProvisionError(Exception):
    """A device-PSK push to a fleet failed (channel error, or fleet rejected)."""


class PinnedFleet:
    """A fleet the controller has enrolled with and trusts."""

    def __init__(
        self,
        fleet_pub: bytes,
        channel_key: bytes,
        name: str = "",
        host: str = "",
        port: int = 0,
    ):
        self.fleet_pub = fleet_pub
        self.channel_key = channel_key
        self.name = name
        self.host = host
        self.port = port

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.fleet_pub)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_key": self.channel_key.hex(),
            "name": self.name,
            "host": self.host,
            "port": self.port,
        }


class FleetStore:
    """Persists pinned fleets (keyed by fleet public key) at
    ~/.config/ltp/fleets.yaml (0600). The channel key is a secret."""

    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "fleets.yaml")
        self._fleets: dict[str, PinnedFleet] = {}  # keyed by fleet_pub hex

    def load(self) -> None:
        if not self.path.exists():
            return
        data = yaml.safe_load(self.path.read_text()) or {}
        for pub_hex, entry in (data.get("fleets") or {}).items():
            try:
                self._fleets[pub_hex] = PinnedFleet(
                    fleet_pub=bytes.fromhex(pub_hex),
                    channel_key=bytes.fromhex(entry["channel_key"]),
                    name=entry.get("name", ""),
                    host=entry.get("host", ""),
                    port=int(entry.get("port", 0)),
                )
            except (ValueError, KeyError, TypeError):
                logger.error(f"FleetStore: invalid entry for {pub_hex[:16]}…")
        logger.info(f"FleetStore: loaded {len(self._fleets)} pinned fleet(s)")

    def save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        data = {"fleets": {h: f.to_dict() for h, f in self._fleets.items()}}
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, yaml.safe_dump(data).encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(self.path, 0o600)

    def pin(self, fleet: PinnedFleet) -> None:
        self._fleets[fleet.fleet_pub.hex()] = fleet
        self.save()

    def revoke(self, fleet_pub_hex: str) -> bool:
        if fleet_pub_hex in self._fleets:
            del self._fleets[fleet_pub_hex]
            self.save()
            return True
        return False

    def get(self, fleet_pub_hex: str) -> PinnedFleet | None:
        return self._fleets.get(fleet_pub_hex)

    def all(self) -> list[PinnedFleet]:
        return list(self._fleets.values())

    def is_trusted(self, fleet_pub_hex: str) -> bool:
        return fleet_pub_hex in self._fleets


async def enroll_fleet(
    identity: Identity,
    host: str,
    port: int,
    expected_fleet_pub: bytes,
    timeout: float = 5.0,
) -> PinnedFleet:
    """Connect to a fleet's enroll endpoint and run the handshake.

    `expected_fleet_pub` is the key seen in the fleet advertisement — the TOFU
    anchor. Raises EnrollError on any mismatch/rejection.
    """
    enroller = ControllerEnroller(
        identity.private_key, identity.public_key, expected_fleet_pub
    )
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except (OSError, TimeoutError, asyncio.TimeoutError) as exc:
        raise EnrollError(f"cannot reach fleet at {host}:{port} ({exc})") from exc
    try:
        req = Message(MessageType.FLEET_ENROLL_REQUEST, 1, **enroller.request())
        writer.write(req.to_bytes())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not line:
            raise EnrollError("fleet closed the connection")
        msg = Message.from_bytes(line)
        if msg.type == MessageType.ERROR:
            raise EnrollError(f"fleet rejected enrollment: {msg.data.get('message', '')}")
        if msg.type != MessageType.FLEET_ENROLL_RESPONSE:
            raise EnrollError(f"unexpected reply {msg.type.value}")
        channel_key = enroller.on_response(
            {"fleet_pub": msg.data.get("fleet_pub", ""),
             "confirm": msg.data.get("confirm", "")}
        )
    finally:
        writer.close()
    return PinnedFleet(expected_fleet_pub, channel_key, host=host, port=port)


async def provision_device(
    identity: Identity,
    fleet: PinnedFleet,
    device_id: str,
    psk_hex: str | None,
    timeout: float = 5.0,
) -> str:
    """Push a device PSK (or None to disable auth) to a trusted fleet over the
    encrypted channel (Phase 5.2). Returns the fleet's status message; raises
    ProvisionError on any failure. The PSK never crosses the wire in cleartext.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(fleet.host, fleet.port), timeout=timeout
        )
    except (OSError, TimeoutError, asyncio.TimeoutError) as exc:
        raise ProvisionError(f"cannot reach fleet at {fleet.host}:{fleet.port} ({exc})") from exc
    try:
        begin = Message(
            MessageType.FLEET_PROVISION_BEGIN, 1, controller_pub=identity.public_key.hex()
        )
        writer.write(begin.to_bytes())
        await writer.drain()

        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not line:
            raise ProvisionError("fleet closed the connection")
        msg = Message.from_bytes(line)
        if msg.type == MessageType.ERROR:
            raise ProvisionError(f"fleet rejected: {msg.data.get('message', '')}")
        if msg.type != MessageType.FLEET_PROVISION_CHALLENGE:
            raise ProvisionError(f"unexpected reply {msg.type.value}")
        challenge = bytes.fromhex(msg.data.get("challenge", ""))

        nonce = new_nonce()
        ct = seal(
            fleet.channel_key, DIR_C2F, build_provision(device_id, psk_hex, challenge), nonce
        )
        prov = Message(MessageType.FLEET_PROVISION, 1, nonce=nonce.hex(), ct=ct.hex())
        writer.write(prov.to_bytes())
        await writer.drain()

        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not line:
            raise ProvisionError("fleet closed the connection after provision")
        rmsg = Message.from_bytes(line)
        if rmsg.type == MessageType.ERROR:
            raise ProvisionError(f"fleet rejected: {rmsg.data.get('message', '')}")
        if rmsg.type != MessageType.FLEET_PROVISION_RESULT:
            raise ProvisionError(f"unexpected reply {rmsg.type.value}")
        try:
            result = parse_result(
                open_(
                    fleet.channel_key,
                    DIR_F2C,
                    bytes.fromhex(rmsg.data.get("nonce", "")),
                    bytes.fromhex(rmsg.data.get("ct", "")),
                )
            )
        except (ChannelError, ValueError) as exc:
            raise ProvisionError(f"bad provision result: {exc}") from exc
    finally:
        writer.close()
    if not result.get("ok"):
        raise ProvisionError(result.get("message", "fleet reported failure"))
    return str(result.get("message", "provisioned"))
