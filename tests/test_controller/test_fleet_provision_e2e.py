"""End-to-end fleet provisioning over a real socket (Phase 5.2): enroll, then
push a PSK through the encrypted channel to a fake apply function, exercising
set/disable, the anti-replay challenge, and unauthorized-controller rejection."""

from __future__ import annotations

import pytest

from libltp.fleet_channel import DIR_C2F, build_provision, new_nonce, seal
from libltp.identity import Identity
from libltp.protocol import Message
from libltp.types import MessageType
from ltp_controller.fleet_manager import ProvisionError, enroll_fleet, provision_device
from ltp_serial_sink.enrollment import FleetEnrollServer, FleetTrustStore


def _identity(tmp_path, name):
    return Identity.load_or_create(path=tmp_path / name)


async def _enrolled_server(tmp_path):
    """A running, already-enrolled fleet server with a recording apply_fn."""
    fleet_id = _identity(tmp_path, "fleet-identity")
    trust = FleetTrustStore(path=tmp_path / "fleet-trust.yaml")
    applied: list[tuple[str, str | None]] = []

    async def apply_fn(device_id, psk):
        applied.append((device_id, psk))
        return True, ("set" if psk else "cleared")

    server = FleetEnrollServer(fleet_id, trust, port=0, apply_fn=apply_fn)
    await server.start()
    ctrl_id = _identity(tmp_path, "ctrl-identity")
    pinned = await enroll_fleet(ctrl_id, "127.0.0.1", server.bound_port, fleet_id.public_key)
    return fleet_id, ctrl_id, server, pinned, applied


async def test_provision_set_and_disable(tmp_path):
    fleet_id, ctrl_id, server, pinned, applied = await _enrolled_server(tmp_path)
    try:
        psk = "00112233445566778899aabbccddeeff"
        msg = await provision_device(ctrl_id, pinned, "dev-1", psk)
        assert msg == "set"
        assert applied[-1] == ("dev-1", psk)

        msg = await provision_device(ctrl_id, pinned, "dev-1", None)
        assert msg == "cleared"
        assert applied[-1] == ("dev-1", None)
    finally:
        await server.stop()


async def test_provision_requires_correct_channel_key(tmp_path):
    fleet_id, ctrl_id, server, pinned, applied = await _enrolled_server(tmp_path)
    # Corrupt the controller's channel key -> AEAD open fails on the fleet.
    bad = type(pinned)(pinned.fleet_pub, b"\x00" * 32, host=pinned.host, port=pinned.port)
    try:
        with pytest.raises(ProvisionError):
            await provision_device(ctrl_id, bad, "dev-1", "00" * 16)
        assert applied == []  # nothing applied
    finally:
        await server.stop()


async def test_provision_rejects_unenrolled_controller(tmp_path):
    fleet_id, ctrl_id, server, pinned, applied = await _enrolled_server(tmp_path)
    other = _identity(tmp_path, "other-ctrl")
    # A different controller identity, even with a guessed key, is refused at BEGIN.
    impostor = type(pinned)(
        pinned.fleet_pub, pinned.channel_key, host=pinned.host, port=pinned.port
    )
    try:
        with pytest.raises(ProvisionError, match="not the enrolled controller"):
            await provision_device(other, impostor, "dev-1", "00" * 16)
        assert applied == []
    finally:
        await server.stop()


async def test_provision_rejects_replayed_challenge(tmp_path):
    """A captured provision frame can't be replayed: the challenge is single-use
    and per-connection, so a second connection's challenge won't match."""
    fleet_id, ctrl_id, server, pinned, applied = await _enrolled_server(tmp_path)
    import asyncio

    try:
        # Manually drive one exchange, capturing the sealed frame.
        reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
        writer.write(
            Message(
                MessageType.FLEET_PROVISION_BEGIN, 1, controller_pub=ctrl_id.public_key.hex()
            ).to_bytes()
        )
        await writer.drain()
        chal = Message.from_bytes(await reader.readline())
        challenge = bytes.fromhex(chal.data["challenge"])
        nonce = new_nonce()
        ct = seal(
            pinned.channel_key, DIR_C2F, build_provision("dev-1", "00" * 16, challenge), nonce
        )
        captured = Message(MessageType.FLEET_PROVISION, 1, nonce=nonce.hex(), ct=ct.hex())
        writer.write(captured.to_bytes())
        await writer.drain()
        await reader.readline()  # result
        writer.close()

        # Replay the captured frame on a fresh connection: new challenge -> reject.
        r2, w2 = await asyncio.open_connection("127.0.0.1", server.bound_port)
        w2.write(
            Message(
                MessageType.FLEET_PROVISION_BEGIN, 1, controller_pub=ctrl_id.public_key.hex()
            ).to_bytes()
        )
        await w2.drain()
        await r2.readline()  # new (different) challenge, ignored
        w2.write(captured.to_bytes())
        await w2.drain()
        reply = Message.from_bytes(await r2.readline())
        w2.close()
        assert reply.type == MessageType.ERROR
        # dev-1 applied once (first exchange), not by the replay.
        assert applied == [("dev-1", "00" * 16)]
    finally:
        await server.stop()
