"""Session lifecycle as seen by the browser: idle (sliding) expiry, the
/api/session status endpoint the page monitor polls, keep-alive, and the
JSON login used by the in-page modal to replay a request that got a 401."""

import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fast_hash(monkeypatch):
    monkeypatch.setattr("ltp_controller.security.PBKDF2_ITERATIONS", 1000)


from ltp_controller.security import AuthManager, WebSecuritySettings, hash_password
from ltp_controller.web import auth as auth_mod
from ltp_controller.web.app import create_app

PW = "admin-pw"
TTL = 1000


def _controller():
    controller = MagicMock()
    controller.sources = []
    controller.sinks = []
    controller.purge_offline_sinks.return_value = 0
    return controller


@pytest.fixture()
def app():
    auth = AuthManager.from_config(
        {
            "session_ttl_seconds": TTL,
            "users": [
                {"name": "admin", "role": "admin", "password_hash": hash_password(PW)},
                {"name": "vera", "role": "viewer", "password_hash": hash_password("v")},
            ],
        }
    )
    settings = WebSecuritySettings(
        auth=auth, secret_key=b"t" * 32, secure_cookies=False, public_read=True
    )
    return create_app(controller=_controller(), router=MagicMock(routes=[]), web_security=settings)


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, user="admin", pw=PW):
    r = client.post("/login", data={"username": user, "password": pw})
    assert r.status_code == 302


def _stamp(client):
    with client.session_transaction() as s:
        return s.get("t")


def _set_stamp(client, value):
    with client.session_transaction() as s:
        s["t"] = value


def _csrf(client):
    with client.session_transaction() as s:
        return s["csrf"]


class TestSessionStatus:
    def test_anonymous(self, client):
        data = client.get("/api/session").get_json()
        assert data == {"auth_enabled": True, "authenticated": False}

    def test_logged_in(self, client):
        _login(client)
        data = client.get("/api/session").get_json()
        assert data["authenticated"] is True
        assert data["name"] == "admin" and data["role"] == "admin"
        assert 0 < data["expires_in"] <= TTL
        assert data["warn_below"] == auth_mod.SESSION_WARN_BELOW

    def test_expired_reports_unauthenticated_not_401(self, client):
        _login(client)
        _set_stamp(client, time.time() - TTL - 1)
        r = client.get("/api/session")
        assert r.status_code == 200
        assert r.get_json()["authenticated"] is False

    def test_status_poll_does_not_extend_session(self, client):
        _login(client)
        old = time.time() - 500
        _set_stamp(client, old)
        client.get("/api/session")
        assert _stamp(client) == old

    def test_auth_disabled(self):
        app = create_app(controller=_controller(), router=MagicMock(routes=[]))
        data = app.test_client().get("/api/session").get_json()
        assert data == {"auth_enabled": False, "authenticated": False}


class TestSlidingExpiry:
    def test_page_navigation_refreshes_stamp(self, client):
        _login(client)
        old = time.time() - 500
        _set_stamp(client, old)
        assert client.get("/sources").status_code == 200
        assert _stamp(client) > old

    def test_mutation_refreshes_stamp(self, client):
        _login(client)
        old = time.time() - 500
        _set_stamp(client, old)
        r = client.post("/api/sinks/purge", headers={"X-CSRF-Token": _csrf(client)})
        assert r.status_code == 200
        assert _stamp(client) > old

    def test_api_polling_does_not_refresh(self, client):
        # Background GET /api/... polls must not keep an abandoned tab alive.
        _login(client)
        old = time.time() - 500
        _set_stamp(client, old)
        assert client.get("/api/sinks").status_code == 200
        assert _stamp(client) == old

    def test_refresh_is_throttled(self, client):
        _login(client)
        recent = time.time() - (auth_mod.SESSION_REFRESH_INTERVAL / 2)
        _set_stamp(client, recent)
        client.get("/sources")
        assert _stamp(client) == recent

    def test_expired_session_is_still_expired(self, client):
        _login(client)
        _set_stamp(client, time.time() - TTL - 1)
        r = client.post("/api/sinks/purge", headers={"X-CSRF-Token": _csrf(client)})
        assert r.status_code == 401

    def test_keepalive_endpoint(self, client):
        _login(client, "vera", "v")  # any signed-in role may extend its own session
        old = time.time() - 500
        _set_stamp(client, old)
        r = client.post("/api/session/refresh", headers={"X-CSRF-Token": _csrf(client)})
        assert r.status_code == 200
        assert r.get_json()["authenticated"] is True
        assert _stamp(client) > old

    def test_keepalive_needs_csrf(self, client):
        _login(client)
        assert client.post("/api/session/refresh").status_code == 403

    def test_keepalive_anonymous_401(self, client):
        assert client.post("/api/session/refresh").status_code == 401


class TestJsonLogin:
    JSON = {"Accept": "application/json"}

    def test_success_returns_csrf_and_principal(self, client):
        r = client.post("/login", data={"username": "admin", "password": PW}, headers=self.JSON)
        assert r.status_code == 200
        data = r.get_json()
        assert data["status"] == "ok"
        assert data["principal"] == {"name": "admin", "role": "admin"}
        assert data["csrf"] == _csrf(client)
        assert data["session"]["authenticated"] is True
        # The token it handed out is the one the gate now expects.
        r = client.post("/api/sinks/purge", headers={"X-CSRF-Token": data["csrf"]})
        assert r.status_code == 200

    def test_failure_is_json(self, client):
        r = client.post("/login", data={"username": "admin", "password": "x"}, headers=self.JSON)
        assert r.status_code == 401
        assert r.get_json() == {"error": "Invalid username or password."}

    def test_html_form_unchanged(self, client):
        r = client.post("/login", data={"username": "admin", "password": "x"})
        assert r.status_code == 401
        assert "Invalid username or password." in r.get_data(as_text=True)


class TestPageMarkup:
    def test_modal_present_for_logged_in_user(self, client):
        # An expired login mid-interaction must be able to re-authenticate in
        # place, so the modal is rendered even when the page thinks it's logged in.
        _login(client)
        page = client.get("/sources").get_data(as_text=True)
        assert 'id="loginModal"' in page
        assert 'name="ltp-principal" content="admin"' in page
        assert 'id="session-banner"' in page
        assert 'id="nav-auth"' in page

    def test_anonymous_markup(self, client):
        page = client.get("/sources").get_data(as_text=True)
        assert 'name="ltp-principal" content=""' in page
        assert 'id="loginModal"' in page

    def test_no_session_chrome_without_auth(self):
        app = create_app(controller=_controller(), router=MagicMock(routes=[]))
        page = app.test_client().get("/sources").get_data(as_text=True)
        assert 'id="loginModal"' not in page
        assert 'id="session-banner"' not in page
