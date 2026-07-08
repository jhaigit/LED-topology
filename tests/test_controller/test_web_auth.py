"""Tests for the web auth gate: 401/403 matrix, CSRF, cookies, roles/scopes."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _fast_hash(monkeypatch):
    """PBKDF2 at production strength takes ~0.5s per hash; tests don't need it."""
    monkeypatch.setattr("ltp_controller.security.PBKDF2_ITERATIONS", 1000)


from ltp_controller.security import (
    AuthManager,
    WebSecuritySettings,
    generate_token,
    hash_password,
)
from ltp_controller.web.app import create_app

ADMIN_PW = "admin-pw"
BOB_PW = "bob-pw"

# Bob: operator scoped to one sink, no automation.
# Vera: viewer. Tok: operator token scoped like Bob but with automation: run.
HTML = {"Accept": "text/html"}


@pytest.fixture()
def token_pair():
    return generate_token()


@pytest.fixture()
def app(token_pair):
    token, token_hash = token_pair
    controller = MagicMock()
    controller.sources = []
    controller.sinks = []
    controller.purge_offline_sinks.return_value = 0
    controller.purge_offline_sources.return_value = 0
    group = SimpleNamespace(members=[SimpleNamespace(sink_id="strip-1")])
    controller.sink_group_manager.get = lambda ident: group if ident == "stage" else None

    router = MagicMock()
    router.routes = []
    router.get_route = lambda rid: (
        SimpleNamespace(sink_id="hall-strip") if rid == "r-hall" else None
    )

    auth = AuthManager.from_config(
        {
            "users": [
                {"name": "admin", "role": "admin", "password_hash": hash_password(ADMIN_PW)},
                {
                    "name": "bob",
                    "role": "operator",
                    "password_hash": hash_password(BOB_PW),
                    "scope": {"devices": ["hall-strip"], "automation": "none"},
                },
                {"name": "vera", "role": "viewer", "password_hash": hash_password("vera-pw")},
            ],
            "tokens": [
                {
                    "name": "ha",
                    "role": "operator",
                    "hash": token_hash,
                    "scope": {"devices": ["hall-strip"], "automation": "run"},
                }
            ],
        }
    )
    settings = WebSecuritySettings(auth=auth, secret_key=b"t" * 32, secure_cookies=False)
    return create_app(controller=controller, router=router, web_security=settings)


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, username, password):
    resp = client.post("/login", data={"username": username, "password": password})
    assert resp.status_code == 302
    # Fish the CSRF token out of a rendered page.
    page = client.get("/sources").get_data(as_text=True)
    marker = 'name="csrf-token" content="'
    start = page.index(marker) + len(marker)
    return page[start : page.index('"', start)]


def mutate(client, method, path, csrf=None, json=None, **kwargs):
    headers = kwargs.pop("headers", {})
    if csrf:
        headers["X-CSRF-Token"] = csrf
    return getattr(client, method)(path, headers=headers, json=json, **kwargs)


class TestAuthentication:
    def test_anonymous_api_gets_401(self, client):
        assert client.get("/api/sinks").status_code == 401

    def test_anonymous_browser_redirected_to_login(self, client):
        resp = client.get("/sinks", headers=HTML)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_healthz_public(self, client):
        assert client.get("/healthz").status_code == 200

    def test_static_public(self, client):
        assert client.get("/static/css/style.css").status_code == 200

    def test_bad_login_rejected(self, client):
        resp = client.post("/login", data={"username": "admin", "password": "nope"})
        assert resp.status_code == 401

    def test_login_rate_limited(self, app):
        client = app.test_client()
        for _ in range(10):
            client.post("/login", data={"username": "admin", "password": "nope"})
        resp = client.post("/login", data={"username": "admin", "password": ADMIN_PW})
        assert resp.status_code == 429

    def test_login_grants_access(self, client):
        login(client, "admin", ADMIN_PW)
        assert client.get("/api/sinks").status_code == 200

    def test_logout_revokes(self, client):
        csrf = login(client, "admin", ADMIN_PW)
        client.post("/logout", data={"_csrf": csrf})
        assert client.get("/api/sinks").status_code == 401

    def test_bearer_token(self, client, token_pair):
        token, _ = token_pair
        resp = client.get("/api/sinks", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert client.get("/api/sinks", headers={"Authorization": "Bearer bad"}).status_code == 401

    def test_open_redirect_rejected(self, client):
        resp = client.post(
            "/login?next=//evil.example",
            data={"username": "admin", "password": ADMIN_PW},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] in ("/", "http://localhost/")


class TestCSRF:
    def test_session_mutation_needs_token(self, client):
        login(client, "admin", ADMIN_PW)
        assert client.post("/api/sinks/purge").status_code == 403

    def test_session_mutation_with_token(self, client):
        csrf = login(client, "admin", ADMIN_PW)
        assert mutate(client, "post", "/api/sinks/purge", csrf).status_code == 200

    def test_bearer_exempt_from_csrf(self, client, token_pair):
        token, _ = token_pair
        resp = client.put(
            "/api/sinks/hall-strip/controls",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        assert resp.status_code != 403  # may 4xx/5xx on the mock, never CSRF-403

    def test_wrong_token_rejected(self, client):
        login(client, "admin", ADMIN_PW)
        assert mutate(client, "post", "/api/sinks/purge", "wrong").status_code == 403


class TestAuthorization:
    def test_viewer_reads_but_cannot_mutate(self, client):
        csrf = login(client, "vera", "vera-pw")
        assert client.get("/api/sinks").status_code == 200
        assert client.get("/api/routes").status_code == 200
        assert mutate(client, "post", "/api/sinks/purge", csrf).status_code == 403
        assert mutate(client, "post", "/api/routes", csrf, json={"sink_id": "x"}).status_code == 403

    def test_operator_scoped_to_device(self, client):
        csrf = login(client, "bob", BOB_PW)
        in_scope = mutate(client, "post", "/api/sinks/hall-strip/clear", csrf)
        out_scope = mutate(client, "post", "/api/sinks/kitchen/clear", csrf)
        assert in_scope.status_code != 403
        assert out_scope.status_code == 403

    def test_route_creation_checks_target_sink(self, client):
        csrf = login(client, "bob", BOB_PW)
        denied = mutate(
            client,
            "post",
            "/api/routes",
            csrf,
            json={"name": "r", "source_id": "s", "sink_id": "kitchen"},
        )
        assert denied.status_code == 403
        allowed = mutate(
            client,
            "post",
            "/api/routes",
            csrf,
            json={"name": "r", "source_id": "s", "sink_id": "hall-strip"},
        )
        assert allowed.status_code != 403

    def test_route_mutation_resolves_existing_target(self, client):
        csrf = login(client, "bob", BOB_PW)
        resp = mutate(client, "post", "/api/routes/r-hall/enable", csrf)
        assert resp.status_code != 403  # r-hall targets hall-strip, in scope

    def test_automation_none_hides_and_forbids(self, client):
        csrf = login(client, "bob", BOB_PW)
        assert client.get("/api/rules").status_code == 403
        assert mutate(client, "post", "/api/rules", csrf, json={}).status_code == 403

    def test_token_automation_run_not_manage(self, client, token_pair):
        token, _ = token_pair
        bearer = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/rules", headers=bearer).status_code != 403
        assert client.post("/api/rules/some-id/enable", headers=bearer).status_code != 403
        assert client.post("/api/rules", headers=bearer, json={}).status_code == 403
        assert client.delete("/api/rules/some-id", headers=bearer).status_code == 403

    def test_config_save_admin_only(self, client, token_pair):
        token, _ = token_pair
        resp = client.post("/api/config/save", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_group_scope_expansion(self, app):
        """A scope naming a group grants control of its member sinks."""
        from ltp_controller.security import Principal, Requirement, Scope
        from ltp_controller.web.auth import _Services

        svc = _Services(app)
        p = Principal(name="x", role="operator", scope=Scope(devices=["stage"]))
        from ltp_controller.security import principal_allows

        req = Requirement(min_role="operator", devices=["strip-1"])
        assert principal_allows(p, req, svc.expand_devices)


class TestAuthDisabled:
    def test_everything_open_without_settings(self):
        controller = MagicMock()
        controller.sources = []
        controller.sinks = []
        controller.purge_offline_sinks.return_value = 0
        router = MagicMock()
        router.routes = []
        app = create_app(controller=controller, router=router)
        client = app.test_client()
        assert client.get("/api/sinks").status_code == 200
        assert client.post("/api/sinks/purge").status_code == 200


class TestCookieFlags:
    def test_secure_flag_follows_settings(self):
        controller = MagicMock()
        controller.sources = []
        controller.sinks = []
        auth = AuthManager.from_config({"password_hash": hash_password("pw")})
        for secure in (True, False):
            app = create_app(
                controller=controller,
                router=MagicMock(),
                web_security=WebSecuritySettings(
                    auth=auth, secret_key=b"s" * 32, secure_cookies=secure
                ),
            )
            resp = app.test_client().post("/login", data={"username": "admin", "password": "pw"})
            cookie = resp.headers.get("Set-Cookie", "")
            assert ("Secure" in cookie) == secure
            assert "HttpOnly" in cookie
            assert "SameSite=Strict" in cookie
