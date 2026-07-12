"""Tests for the admin-gated device-key management endpoints (Phase 4a).

Covers role-gating (401/403), the generate/associate/rotate/unpair roundtrip,
CSRF enforcement, and the invariant that a key is never returned on a GET.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fast_hash(monkeypatch):
    monkeypatch.setattr("ltp_controller.security.PBKDF2_ITERATIONS", 1000)


from ltp_controller.keystore import KeyStore
from ltp_controller.security import (
    AuthManager,
    WebSecuritySettings,
    generate_token,
    hash_password,
)
from ltp_controller.web.app import create_app

ADMIN_PW = "admin-pw"
BOB_PW = "bob-pw"
DEV = "dev-1"


@pytest.fixture()
def token_pair():
    return generate_token()


@pytest.fixture()
def keystore(tmp_path):
    ks = KeyStore(path=tmp_path / "keys.yaml")
    ks.load()
    return ks


@pytest.fixture()
def app(token_pair, keystore):
    token, token_hash = token_pair
    sink = SimpleNamespace(
        id=DEV,
        auth_state="unkeyed",
        online=True,
        properties={"auth": "siphash", "desc": "ESP32-C3 OLED Display"},
        # extra attributes so the /sinks page renders (for UI-gating tests)
        name="Test Sink",
        description="ESP32-C3 OLED Display",
        host="10.0.1.9",
        port=5000,
        backend_connected=None,
        device=SimpleNamespace(
            properties={
                "dim": "72x40", "pixels": "2880", "type": "matrix", "color": "rgb",
                "auth": "siphash", "desc": "ESP32-C3 OLED Display",
            }
        ),
        capabilities=None,
        controls=[],
        control_values={},
    )
    controller = MagicMock()
    controller.sinks = [sink]
    controller.sources = []
    controller.get_sink = lambda sid: sink if sid == DEV else None
    # No connection pool / event loop in tests -> reclaim is a no-op.
    controller._connection_pool = None

    router = MagicMock()
    router.routes = []

    auth = AuthManager.from_config(
        {
            "users": [
                {"name": "admin", "role": "admin", "password_hash": hash_password(ADMIN_PW)},
                {
                    "name": "bob",
                    "role": "operator",
                    "password_hash": hash_password(BOB_PW),
                    "scope": {"devices": [DEV], "automation": "none"},
                },
                {"name": "vera", "role": "viewer", "password_hash": hash_password("vera-pw")},
            ],
            "tokens": [
                {
                    "name": "ha",
                    "role": "operator",
                    "hash": token_hash,
                    "scope": {"devices": [DEV], "automation": "run"},
                }
            ],
        }
    )
    settings = WebSecuritySettings(auth=auth, secret_key=b"t" * 32, secure_cookies=False)
    return create_app(
        controller=controller, router=router, web_security=settings, keystore=keystore
    )


@pytest.fixture()
def client(app):
    return app.test_client()


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


def test_status_requires_auth(client):
    assert client.get(f"/api/sinks/{DEV}/key").status_code == 401


def test_status_admin_only(client, token_pair):
    token, _ = token_pair
    # operator (bearer, CSRF-exempt) is forbidden on the key endpoints
    assert client.get(f"/api/sinks/{DEV}/key", headers=_bearer(token)).status_code == 403


def test_set_admin_only(client, token_pair):
    token, _ = token_pair
    r = client.post(f"/api/sinks/{DEV}/key", headers=_bearer(token), json={})
    assert r.status_code == 403


# --- generate / associate / rotate / unpair -------------------------------


def test_generate_and_status_roundtrip(client, keystore):
    csrf = login(client, "admin", ADMIN_PW)

    r = client.post(f"/api/sinks/{DEV}/key", headers={"X-CSRF-Token": csrf}, json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["has_key"] is True and body["rotated"] is False
    hexkey = body["provisioning"]["key"]
    assert len(hexkey) == 32 and bytes.fromhex(hexkey)  # valid 16-byte hex
    # It really landed in the keystore.
    assert keystore.get_key(DEV) == bytes.fromhex(hexkey)

    # GET reports has_key but NEVER the key material.
    g = client.get(f"/api/sinks/{DEV}/key").get_json()
    assert g["has_key"] is True
    assert "key" not in g and "provisioning" not in g


def test_generate_conflict_then_rotate(client, keystore):
    csrf = login(client, "admin", ADMIN_PW)
    h = {"X-CSRF-Token": csrf}
    first = client.post(f"/api/sinks/{DEV}/key", headers=h, json={}).get_json()
    k1 = first["provisioning"]["key"]

    # Second set without rotate is refused.
    dup = client.post(f"/api/sinks/{DEV}/key", headers=h, json={})
    assert dup.status_code == 409

    # With rotate=true it replaces and returns a different key.
    rot = client.post(f"/api/sinks/{DEV}/key", headers=h, json={"rotate": True})
    assert rot.status_code == 200
    body = rot.get_json()
    assert body["rotated"] is True
    k2 = body["provisioning"]["key"]
    assert k2 != k1
    assert keystore.get_key(DEV) == bytes.fromhex(k2)


def test_associate_supplied_key_not_echoed(client, keystore):
    csrf = login(client, "admin", ADMIN_PW)
    supplied = "00112233445566778899aabbccddeeff"
    r = client.post(
        f"/api/sinks/{DEV}/key", headers={"X-CSRF-Token": csrf}, json={"key": supplied}
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["has_key"] is True
    # A caller-supplied key is stored but never echoed back.
    assert "provisioning" not in body and "key" not in body
    assert keystore.get_key(DEV) == bytes.fromhex(supplied)


@pytest.mark.parametrize("bad", ["zz", "00112233", "001122334455667788", "not-hex-here!!"])
def test_supplied_key_rejected(client, bad):
    csrf = login(client, "admin", ADMIN_PW)
    r = client.post(
        f"/api/sinks/{DEV}/key", headers={"X-CSRF-Token": csrf}, json={"key": bad}
    )
    assert r.status_code == 400


def test_delete_unpairs(client, keystore):
    csrf = login(client, "admin", ADMIN_PW)
    keystore.set_key(DEV, b"\x11" * 16)
    r = client.delete(f"/api/sinks/{DEV}/key", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.get_json()["has_key"] is False
    assert keystore.has_key(DEV) is False


def test_unknown_sink_404(client):
    csrf = login(client, "admin", ADMIN_PW)
    assert client.get("/api/sinks/nope/key").status_code == 404
    r = client.post("/api/sinks/nope/key", headers={"X-CSRF-Token": csrf}, json={})
    assert r.status_code == 404


# --- CSRF -----------------------------------------------------------------


def test_csrf_required_for_session_post(client):
    login(client, "admin", ADMIN_PW)  # establishes session cookie
    # No CSRF header on a session-authenticated mutation -> 403.
    assert client.post(f"/api/sinks/{DEV}/key", json={}).status_code == 403


# --- UI gating ------------------------------------------------------------


# The .device-auth CSS class and the pairDevice()/data-auth-actions strings
# also live in the always-rendered <style>/<script> blocks; the only marker
# exclusive to the admin-gated card DOM is the button label.
def test_ui_shows_auth_section_for_admin(client):
    login(client, "admin", ADMIN_PW)
    page = client.get("/sinks").get_data(as_text=True)
    assert ">Set Key</button>" in page


def test_ui_hides_auth_section_for_viewer(client):
    login(client, "vera", "vera-pw")
    page = client.get("/sinks").get_data(as_text=True)
    assert ">Set Key</button>" not in page


# --- X25519+PIN pairing endpoint (Phase 4b) -------------------------------


def test_pair_admin_only(client, token_pair):
    token, _ = token_pair
    r = client.post(f"/api/sinks/{DEV}/pair", headers=_bearer(token), json={"pin": "01234567"})
    assert r.status_code == 403


def test_pair_requires_pin(client):
    csrf = login(client, "admin", ADMIN_PW)
    r = client.post(f"/api/sinks/{DEV}/pair", headers={"X-CSRF-Token": csrf}, json={})
    assert r.status_code == 400


def test_pair_happy_path(app, client):
    async def fake_pair(sink_id, pin):
        assert sink_id == DEV and pin == "01234567"
        return (True, "paired")

    app.config["controller"]._connection_pool = SimpleNamespace(pair_device=fake_pair)
    csrf = login(client, "admin", ADMIN_PW)
    r = client.post(f"/api/sinks/{DEV}/pair", headers={"X-CSRF-Token": csrf}, json={"pin": "01234567"})
    assert r.status_code == 200
    assert r.get_json()["paired"] is True


def test_pair_failure_reported(app, client):
    async def fake_pair(sink_id, pin):
        return (False, "device rejected pairing: wrong PIN")

    app.config["controller"]._connection_pool = SimpleNamespace(pair_device=fake_pair)
    csrf = login(client, "admin", ADMIN_PW)
    r = client.post(f"/api/sinks/{DEV}/pair", headers={"X-CSRF-Token": csrf}, json={"pin": "0"})
    assert r.status_code == 400
    assert r.get_json()["paired"] is False
