"""Tests for the admin-gated fleet enrollment endpoints (Phase 5.1).

Covers role-gating, the discovered+trusted merged view, the channel-key-never-
exposed invariant, revoke, and a real-socket enroll happy path (a live
FleetEnrollServer running in a background event loop).
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
from ltp_controller.fleet_manager import FleetStore, PinnedFleet
from ltp_controller.security import (
    AuthManager,
    WebSecuritySettings,
    generate_token,
    hash_password,
)
from ltp_controller.web.app import create_app
from ltp_serial_sink.enrollment import FleetEnrollServer, FleetTrustStore

ADMIN_PW = "admin-pw"


@pytest.fixture()
def token_pair():
    return generate_token()


@pytest.fixture()
def fleet_store(tmp_path):
    return FleetStore(path=tmp_path / "fleets.yaml")


@pytest.fixture()
def controller_identity(tmp_path):
    return Identity.load_or_create(path=tmp_path / "controller-identity")


@pytest.fixture()
def discovered(tmp_path):
    """A running fleet server + a matching discovered-device stub for it."""
    holder = {}

    def _make():
        fleet_id = Identity.load_or_create(path=tmp_path / "fleet-identity")
        trust = FleetTrustStore(path=tmp_path / "fleet-trust.yaml")
        loop = asyncio.new_event_loop()
        server = FleetEnrollServer(fleet_id, trust, port=0)
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(server.start())
            ready.set()
            loop.run_forever()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        ready.wait(timeout=5)
        holder["loop"] = loop
        holder["server"] = server
        holder["trust"] = trust
        stub = SimpleNamespace(
            properties={
                "fleet_pub": fleet_id.public_key.hex(),
                "fpr": fleet_id.fingerprint,
                "enrolled": "0",
            },
            display_name="Test Fleet",
            host="127.0.0.1",
            port=server.bound_port,
        )
        return fleet_id, stub

    yield _make
    if "loop" in holder:
        loop = holder["loop"]
        fut = asyncio.run_coroutine_threadsafe(holder["server"].stop(), loop)
        try:
            fut.result(timeout=5)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)


def _build_app(controller, fleet_store, controller_identity, token_hash):
    auth = AuthManager.from_config(
        {
            "users": [
                {"name": "admin", "role": "admin", "password_hash": hash_password(ADMIN_PW)},
                {"name": "vera", "role": "viewer", "password_hash": hash_password("vera-pw")},
            ],
            "tokens": [{"name": "ha", "role": "operator", "hash": token_hash}],
        }
    )
    settings = WebSecuritySettings(auth=auth, secret_key=b"t" * 32, secure_cookies=False)
    return create_app(
        controller=controller,
        router=MagicMock(routes=[]),
        web_security=settings,
        fleet_store=fleet_store,
        fleet_identity=controller_identity,
    )


@pytest.fixture()
def make_app(fleet_store, controller_identity, token_pair):
    _, token_hash = token_pair

    def _make(fleets=()):
        controller = MagicMock()
        controller.sinks = []
        controller.sources = []
        controller.fleets = list(fleets)
        return _build_app(controller, fleet_store, controller_identity, token_hash)

    return _make


def login(client, username, password):
    resp = client.post("/login", data={"username": username, "password": password})
    assert resp.status_code == 302
    page = client.get("/sources").get_data(as_text=True)
    marker = 'name="csrf-token" content="'
    start = page.index(marker) + len(marker)
    return page[start : page.index('"', start)]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# --- role gating ----------------------------------------------------------


def test_list_requires_auth(make_app):
    client = make_app().test_client()
    assert client.get("/api/fleets").status_code == 401


def test_enroll_admin_only(make_app, token_pair):
    token, _ = token_pair
    client = make_app().test_client()
    # operator token (CSRF-exempt bearer) is forbidden from enrolling
    r = client.post("/api/fleets/deadbeef/enroll", headers=_bearer(token))
    assert r.status_code == 403


def test_viewer_can_list(make_app):
    client = make_app().test_client()
    login(client, "vera", "vera-pw")
    assert client.get("/api/fleets").status_code == 200


# --- merged view + secrecy ------------------------------------------------


def test_view_merges_and_hides_channel_key(make_app, fleet_store, discovered):
    fleet_id, stub = discovered()
    # Pre-pin the fleet with a secret channel key.
    fleet_store.pin(
        PinnedFleet(fleet_id.public_key, b"\x11" * 32, name="Test Fleet")
    )
    client = make_app([stub]).test_client()
    login(client, "admin", ADMIN_PW)
    body = client.get("/api/fleets").get_json()
    assert body["controller_fingerprint"]
    assert len(body["fleets"]) == 1
    row = body["fleets"][0]
    assert row["trusted"] is True and row["online"] is True
    assert row["fingerprint"] == fleet_id.fingerprint
    # The channel key must never appear anywhere in the response.
    assert "11" * 32 not in client.get("/api/fleets").get_data(as_text=True)


# --- enroll happy path (real socket) --------------------------------------


def test_enroll_roundtrip(make_app, fleet_store, controller_identity, discovered):
    fleet_id, stub = discovered()
    client = make_app([stub]).test_client()
    csrf = login(client, "admin", ADMIN_PW)
    r = client.post(
        f"/api/fleets/{fleet_id.public_key.hex()}/enroll",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["enrolled"] is True
    assert body["fingerprint"] == fleet_id.fingerprint
    # Controller pinned the fleet with a real channel key.
    pinned = fleet_store.get(fleet_id.public_key.hex())
    assert pinned is not None and len(pinned.channel_key) == 32


def test_enroll_unknown_fleet_404(make_app):
    # No discovered fleets -> nothing to enroll.
    client = make_app([]).test_client()
    csrf = login(client, "admin", ADMIN_PW)
    r = client.post(
        "/api/fleets/" + "aa" * 32 + "/enroll", headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 404


# --- revoke ---------------------------------------------------------------


def test_revoke(make_app, fleet_store, discovered):
    fleet_id, stub = discovered()
    fleet_store.pin(PinnedFleet(fleet_id.public_key, b"\x22" * 32, name="Test Fleet"))
    client = make_app([stub]).test_client()
    csrf = login(client, "admin", ADMIN_PW)
    r = client.delete(
        f"/api/fleets/{fleet_id.public_key.hex()}", headers={"X-CSRF-Token": csrf}
    )
    assert r.status_code == 200 and r.get_json()["revoked"] is True
    assert fleet_store.get(fleet_id.public_key.hex()) is None


def test_revoke_unknown_404(make_app):
    client = make_app([]).test_client()
    csrf = login(client, "admin", ADMIN_PW)
    r = client.delete("/api/fleets/" + "bb" * 32, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404


# --- UI gating ------------------------------------------------------------


def test_fleets_page_renders(make_app):
    client = make_app([]).test_client()
    login(client, "vera", "vera-pw")
    page = client.get("/fleets").get_data(as_text=True)
    assert "Fleets" in page and "loadFleets()" in page
