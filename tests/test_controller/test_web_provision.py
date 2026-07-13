"""Keystore endpoints push to a trusted owning fleet (Phase 5.2).

Runs a real FleetEnrollServer in a background loop, enrolls it, pins it in the
controller's fleet store with host matching the serial sink, then drives Set
Key / Unpair and asserts the key was pushed over the encrypted channel.
"""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fast_hash(monkeypatch):
    monkeypatch.setattr("ltp_controller.security.PBKDF2_ITERATIONS", 1000)


from libltp.identity import Identity
from ltp_controller.fleet_manager import FleetStore, enroll_fleet
from ltp_controller.keystore import KeyStore
from ltp_controller.security import (
    AuthManager,
    WebSecuritySettings,
    hash_password,
)
from ltp_controller.web.app import create_app
from ltp_serial_sink.enrollment import FleetEnrollServer, FleetTrustStore

ADMIN_PW = "admin-pw"
DEV = "dev-serial-1"


@pytest.fixture()
def fleet_backend(tmp_path):
    """A running fleet server (background loop) + recorded apply calls."""
    holder = {}
    applied = []

    async def apply_fn(device_id, psk):
        applied.append((device_id, psk))
        return True, ("set" if psk else "cleared")

    fleet_id = Identity.load_or_create(path=tmp_path / "fleet-identity")
    trust = FleetTrustStore(path=tmp_path / "fleet-trust.yaml")
    loop = asyncio.new_event_loop()
    server = FleetEnrollServer(fleet_id, trust, port=0, apply_fn=apply_fn)
    ready = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(timeout=5)
    holder.update(loop=loop, server=server, fleet_id=fleet_id, applied=applied)
    yield holder
    fut = asyncio.run_coroutine_threadsafe(server.stop(), loop)
    try:
        fut.result(timeout=5)
    except Exception:
        pass
    loop.call_soon_threadsafe(loop.stop)


@pytest.fixture()
def app(tmp_path, fleet_backend):
    ctrl_id = Identity.load_or_create(path=tmp_path / "controller-identity")
    # Enroll the running fleet so both sides share a channel key.
    pinned = asyncio.run(
        enroll_fleet(
            ctrl_id, "127.0.0.1", fleet_backend["server"].bound_port,
            fleet_backend["fleet_id"].public_key,
        )
    )
    pinned.host = "127.0.0.1"
    pinned.port = fleet_backend["server"].bound_port
    pinned.name = "Test Fleet"
    store = FleetStore(path=tmp_path / "fleets.yaml")
    store.pin(pinned)

    keystore = KeyStore(path=tmp_path / "keys.yaml")
    keystore.load()

    sink = SimpleNamespace(
        id=DEV,
        auth_state="unkeyed",
        online=True,
        properties={"auth": "siphash"},
        name="Bookshelf Strip",
        description="serial device",
        host="127.0.0.1",
        port=40293,
        backend_connected=None,
        device=SimpleNamespace(host="127.0.0.1", properties={"desc": "serial device"}),
        capabilities=None,
        controls=[],
        control_values={},
    )
    controller = MagicMock()
    controller.sinks = [sink]
    controller.sources = []
    controller.get_sink = lambda sid: sink if sid == DEV else None
    controller._connection_pool = None

    auth = AuthManager.from_config(
        {
            "users": [
                {"name": "admin", "role": "admin", "password_hash": hash_password(ADMIN_PW)},
            ],
        }
    )
    settings = WebSecuritySettings(auth=auth, secret_key=b"t" * 32, secure_cookies=False)
    application = create_app(
        controller=controller,
        router=MagicMock(routes=[]),
        web_security=settings,
        keystore=keystore,
        fleet_store=store,
        fleet_identity=ctrl_id,
    )
    application.config["_applied"] = fleet_backend["applied"]
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client):
    resp = client.post("/login", data={"username": "admin", "password": ADMIN_PW})
    assert resp.status_code == 302
    page = client.get("/sources").get_data(as_text=True)
    marker = 'name="csrf-token" content="'
    start = page.index(marker) + len(marker)
    return page[start : page.index('"', start)]


def test_set_key_pushes_to_owning_fleet(client, app):
    csrf = login(client)
    r = client.post(f"/api/sinks/{DEV}/key", headers={"X-CSRF-Token": csrf}, json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["has_key"] is True
    push = body["fleet_push"]
    assert push["ok"] is True and push["fleet"] == "Test Fleet"
    # The fleet actually received the generated key (never shown in cleartext here).
    applied = app.config["_applied"]
    assert len(applied) == 1 and applied[0][0] == DEV and applied[0][1] is not None
    # No manual provisioning hint when the push path is used.
    assert "provisioning" not in body


def test_unpair_pushes_disable(client, app):
    csrf = login(client)
    client.post(f"/api/sinks/{DEV}/key", headers={"X-CSRF-Token": csrf}, json={})
    r = client.delete(f"/api/sinks/{DEV}/key", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    body = r.get_json()
    assert body["has_key"] is False
    assert body["fleet_push"]["ok"] is True
    applied = app.config["_applied"]
    assert applied[-1] == (DEV, None)  # disable pushed
