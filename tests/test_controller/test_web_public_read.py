"""Public (login-free) read access, gated by web.public_read (WebSecuritySettings).

When enabled, unauthenticated callers can see system state (read endpoints and
view pages) but still cannot mutate or touch key management; when disabled, the
prior behavior holds (login required for everything).
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
    hash_password,
)
from ltp_controller.web.app import create_app

ADMIN_PW = "admin-pw"
DEV = "dev-1"


def _make_app(public_read: bool):
    sink = SimpleNamespace(
        id=DEV,
        auth_state="unkeyed",
        online=True,
        properties={"auth": "siphash"},
        name="Test Sink",
        description="a sink",
        host="10.0.1.9",
        port=5000,
        backend_connected=None,
        device=SimpleNamespace(host="10.0.1.9", properties={"desc": "a sink"}),
        capabilities=None,
        controls=[],
        control_values={},
        to_dict=lambda: {"id": DEV, "name": "Test Sink"},
    )
    controller = MagicMock()
    controller.sinks = [sink]
    controller.sources = []
    controller.fleets = []
    controller.get_sink = lambda sid: sink if sid == DEV else None
    controller._connection_pool = None

    keystore = KeyStore()
    auth = AuthManager.from_config(
        {"users": [{"name": "admin", "role": "admin", "password_hash": hash_password(ADMIN_PW)}]}
    )
    settings = WebSecuritySettings(
        auth=auth, secret_key=b"t" * 32, secure_cookies=False, public_read=public_read
    )
    return create_app(
        controller=controller,
        router=MagicMock(routes=[]),
        web_security=settings,
        keystore=keystore,
    )


# --- public_read enabled --------------------------------------------------


@pytest.fixture()
def open_client():
    return _make_app(public_read=True).test_client()


def test_anon_can_read_api(open_client):
    assert open_client.get("/api/sinks").status_code == 200


def test_anon_can_view_page(open_client):
    # No redirect to login; the page renders for an anonymous viewer.
    r = open_client.get("/sinks")
    assert r.status_code == 200
    assert "Log in" in r.get_data(as_text=True)  # nav offers login, not logout


def test_anon_cannot_mutate(open_client):
    # A mutation with no auth still requires a login (401), not open.
    assert open_client.post("/api/sinks/purge").status_code == 401


def test_anon_cannot_read_key_status(open_client):
    # Admin-gated read stays blocked even with public_read on.
    assert open_client.get(f"/api/sinks/{DEV}/key").status_code == 403


def test_anon_cannot_enroll_fleet(open_client):
    assert open_client.post("/api/fleets/deadbeef/enroll").status_code == 401


# --- public_read disabled (prior behavior) --------------------------------


@pytest.fixture()
def closed_client():
    return _make_app(public_read=False).test_client()


def test_closed_api_requires_auth(closed_client):
    assert closed_client.get("/api/sinks").status_code == 401


def test_closed_page_redirects_to_login(closed_client):
    r = closed_client.get("/sinks", headers={"Accept": "text/html"})
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
