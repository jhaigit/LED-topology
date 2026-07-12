"""End-to-end fleet enrollment over a real socket: FleetEnrollServer (fleet
side) enrolled by enroll_fleet (controller side). Proves the wire protocol and
that both sides derive the same channel key and pin each other."""

from __future__ import annotations

import pytest

from libltp.fleet_enroll import EnrollError
from libltp.identity import Identity
from ltp_controller.fleet_manager import FleetStore, enroll_fleet
from ltp_serial_sink.enrollment import FleetEnrollServer, FleetTrustStore


def _identity(tmp_path, name):
    return Identity.load_or_create(path=tmp_path / name)


async def _serve(tmp_path):
    fleet_id = _identity(tmp_path, "fleet-identity")
    trust = FleetTrustStore(path=tmp_path / "fleet-trust.yaml")
    server = FleetEnrollServer(fleet_id, trust, port=0)
    await server.start()
    return fleet_id, trust, server


async def test_enroll_roundtrip(tmp_path):
    fleet_id, trust, server = await _serve(tmp_path)
    ctrl_id = _identity(tmp_path, "ctrl-identity")
    try:
        pinned = await enroll_fleet(
            ctrl_id, "127.0.0.1", server.bound_port, fleet_id.public_key
        )
        # Both sides derived the same channel key.
        assert pinned.channel_key == trust.channel_key
        assert pinned.fleet_pub == fleet_id.public_key
        # Fleet pinned this controller (TOFU).
        assert trust.controller_pub == ctrl_id.public_key
        assert trust.is_enrolled
    finally:
        await server.stop()


async def test_controller_store_persists_pin(tmp_path):
    fleet_id, trust, server = await _serve(tmp_path)
    ctrl_id = _identity(tmp_path, "ctrl-identity")
    store = FleetStore(path=tmp_path / "fleets.yaml")
    try:
        pinned = await enroll_fleet(
            ctrl_id, "127.0.0.1", server.bound_port, fleet_id.public_key
        )
        store.pin(pinned)
    finally:
        await server.stop()
    reloaded = FleetStore(path=tmp_path / "fleets.yaml")
    reloaded.load()
    got = reloaded.get(fleet_id.public_key.hex())
    assert got is not None
    assert got.channel_key == pinned.channel_key


async def test_reject_wrong_advertised_key(tmp_path):
    fleet_id, trust, server = await _serve(tmp_path)
    ctrl_id = _identity(tmp_path, "ctrl-identity")
    decoy = _identity(tmp_path, "decoy-identity")
    try:
        with pytest.raises(EnrollError, match="does not match"):
            # Controller was told (advertised) the decoy key, real fleet answers.
            await enroll_fleet(
                ctrl_id, "127.0.0.1", server.bound_port, decoy.public_key
            )
    finally:
        await server.stop()


async def test_fleet_rejects_second_controller(tmp_path):
    fleet_id, trust, server = await _serve(tmp_path)
    first = _identity(tmp_path, "ctrl-a")
    second = _identity(tmp_path, "ctrl-b")
    try:
        await enroll_fleet(first, "127.0.0.1", server.bound_port, fleet_id.public_key)
        with pytest.raises(EnrollError, match="rejected"):
            await enroll_fleet(
                second, "127.0.0.1", server.bound_port, fleet_id.public_key
            )
        # After reset, the second controller can enroll.
        trust.reset()
        pinned = await enroll_fleet(
            second, "127.0.0.1", server.bound_port, fleet_id.public_key
        )
        assert trust.controller_pub == second.public_key
        assert pinned.channel_key == trust.channel_key
    finally:
        await server.stop()
