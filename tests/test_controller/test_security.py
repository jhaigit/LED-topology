"""Tests for ltp_controller.security (hashing, scopes, transport matrix)."""

import pytest


@pytest.fixture(autouse=True)
def _fast_hash(monkeypatch):
    """PBKDF2 at production strength takes ~0.5s per hash; tests don't need it."""
    monkeypatch.setattr("ltp_controller.security.PBKDF2_ITERATIONS", 1000)


from ltp_controller.security import (
    AuthManager,
    Principal,
    RateLimiter,
    Requirement,
    Scope,
    SecurityConfigError,
    ensure_secret_key,
    ensure_self_signed_cert,
    generate_token,
    hash_password,
    hash_token,
    principal_allows,
    resolve_transport,
    verify_password,
    verify_token,
)


class TestHashing:
    def test_password_roundtrip(self):
        stored = hash_password("hunter2")
        assert stored.startswith("pbkdf2$sha256$")
        assert verify_password("hunter2", stored)
        assert not verify_password("hunter3", stored)

    def test_password_unique_salts(self):
        assert hash_password("x") != hash_password("x")

    def test_garbage_hash_rejected(self):
        assert not verify_password("x", "not-a-hash")
        assert not verify_password("x", "pbkdf2$sha256$bad")

    def test_token_roundtrip(self):
        token, stored = generate_token()
        assert stored == hash_token(token)
        assert verify_token(token, stored)
        assert not verify_token(token + "x", stored)


class TestScopes:
    def _op(self, **scope) -> Principal:
        return Principal(name="op", role="operator", scope=Scope(**scope))

    def test_admin_bypasses_everything(self):
        admin = Principal(name="a", role="admin")
        assert principal_allows(admin, Requirement(min_role="admin"))
        assert principal_allows(admin, Requirement(devices=["x"], automation="manage"))

    def test_viewer_reads_but_never_mutates(self):
        viewer = Principal(name="v", role="viewer")
        assert principal_allows(viewer, Requirement(min_role="viewer"))
        assert not principal_allows(viewer, Requirement(min_role="operator"))
        assert not principal_allows(viewer, Requirement(min_role="admin"))

    def test_operator_cannot_admin(self):
        assert not principal_allows(self._op(), Requirement(min_role="admin"))

    def test_device_scope(self):
        op = self._op(devices=["hall-strip"])
        allow = Requirement(min_role="operator", devices=["hall-strip"])
        deny = Requirement(min_role="operator", devices=["kitchen"])
        partial = Requirement(min_role="operator", devices=["hall-strip", "kitchen"])
        assert principal_allows(op, allow)
        assert not principal_allows(op, deny)
        assert not principal_allows(op, partial)  # ALL targets must be in scope

    def test_unscoped_operator_reaches_all_devices(self):
        assert principal_allows(self._op(), Requirement(min_role="operator", devices=["any"]))

    def test_group_expansion(self):
        op = self._op(devices=["stage"])
        expand = lambda ids: {"stage", "strip-1", "strip-2"}
        req = Requirement(min_role="operator", devices=["strip-2"])
        assert principal_allows(op, req, expand)
        assert not principal_allows(op, req)  # without expansion

    def test_automation_tiers(self):
        none = self._op(automation="none")
        run = self._op(automation="run")
        manage = self._op(automation="manage")
        read_req = Requirement(automation="read")
        run_req = Requirement(min_role="operator", automation="run")
        manage_req = Requirement(min_role="operator", automation="manage")
        assert not principal_allows(none, read_req)  # "none" hides entirely
        assert not principal_allows(none, run_req)
        assert principal_allows(run, read_req)
        assert principal_allows(run, run_req)
        assert not principal_allows(run, manage_req)
        assert principal_allows(manage, manage_req)

    def test_scenes_tiers(self):
        run = self._op(scenes="run")
        assert principal_allows(run, Requirement(min_role="operator", scenes="run"))
        assert not principal_allows(run, Requirement(min_role="operator", scenes="manage"))


class TestAuthManager:
    def test_shorthand_admin(self):
        auth = AuthManager.from_config({"password_hash": hash_password("pw")})
        p = auth.authenticate_password("admin", "pw")
        assert p is not None and p.role == "admin"
        assert auth.authenticate_password("admin", "wrong") is None
        assert auth.authenticate_password("nobody", "pw") is None

    def test_lockout_config_rejected(self):
        with pytest.raises(SecurityConfigError):
            AuthManager.from_config({"enabled": True})

    def test_users_and_tokens(self):
        token, token_hash = generate_token()
        auth = AuthManager.from_config(
            {
                "users": [
                    {
                        "name": "bob",
                        "role": "operator",
                        "password_hash": hash_password("bobpw"),
                        "scope": {"devices": ["hall-strip"], "automation": "none"},
                    }
                ],
                "tokens": [{"name": "ha", "role": "operator", "hash": token_hash}],
            }
        )
        bob = auth.authenticate_password("bob", "bobpw")
        assert bob is not None
        assert bob.scope.devices == ["hall-strip"]
        assert bob.scope.automation == "none"
        ha = auth.authenticate_token(token)
        assert ha is not None and ha.kind == "token"
        assert auth.authenticate_token("wrong") is None


class TestRateLimiter:
    def test_limits_per_key(self):
        rl = RateLimiter(max_attempts=3, window_seconds=60)
        assert all(rl.allow("a") for _ in range(3))
        assert not rl.allow("a")
        assert rl.allow("b")  # other keys unaffected


class TestTransportMatrix:
    """The with/without-HTTPS startup matrix from the implementation plan."""

    def test_loopback_http_ok(self):
        d = resolve_transport("127.0.0.1", tls_mode="auto")
        assert d.scheme == "http" and not d.use_tls and not d.insecure_transport

    def test_nonloopback_auto_gets_tls(self):
        d = resolve_transport("0.0.0.0", tls_mode="auto")
        assert d.scheme == "https" and d.use_tls and d.secure_cookies

    def test_nonloopback_no_tls_refused(self):
        with pytest.raises(SecurityConfigError):
            resolve_transport("0.0.0.0", tls_mode="off")

    def test_nonloopback_no_tls_explicit_optout(self):
        d = resolve_transport("0.0.0.0", tls_mode="off", allow_insecure_http=True)
        assert d.scheme == "http"
        assert d.insecure_transport
        assert not d.secure_cookies
        assert d.warnings

    def test_trust_proxy_http_ok(self):
        d = resolve_transport("0.0.0.0", tls_mode="off", trust_proxy=True)
        assert d.scheme == "http" and not d.insecure_transport and d.secure_cookies

    def test_nonloopback_no_auth_refused(self):
        with pytest.raises(SecurityConfigError):
            resolve_transport("0.0.0.0", tls_mode="on", auth_enabled=False)

    def test_nonloopback_no_auth_explicit_optout(self):
        d = resolve_transport(
            "0.0.0.0", tls_mode="on", auth_enabled=False, allow_insecure_http=True
        )
        assert d.use_tls and d.warnings

    def test_loopback_no_auth_ok(self):
        d = resolve_transport("localhost", auth_enabled=False)
        assert d.scheme == "http" and not d.warnings

    def test_tls_forced_on_loopback(self):
        assert resolve_transport("127.0.0.1", tls_mode="on").use_tls

    def test_bad_mode_rejected(self):
        with pytest.raises(SecurityConfigError):
            resolve_transport("127.0.0.1", tls_mode="yes")

    def test_yaml_boolean_mode_normalized(self):
        # YAML 1.1 parses "mode: off" as False and "mode: on" as True.
        assert not resolve_transport("0.0.0.0", tls_mode=False, allow_insecure_http=True).use_tls
        assert resolve_transport("127.0.0.1", tls_mode=True).use_tls


class TestFirstRunArtifacts:
    def test_secret_key_persists(self, tmp_path):
        k1 = ensure_secret_key(tmp_path / "ltp")
        k2 = ensure_secret_key(tmp_path / "ltp")
        assert k1 == k2 and len(k1) == 32
        assert oct((tmp_path / "ltp" / "secret_key").stat().st_mode & 0o777) == "0o600"

    def test_self_signed_cert_generated_once(self, tmp_path):
        cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
        ensure_self_signed_cert(cert, key)
        assert b"BEGIN CERTIFICATE" in cert.read_bytes()
        assert b"PRIVATE KEY" in key.read_bytes()
        assert oct(key.stat().st_mode & 0o777) == "0o600"
        before = cert.read_bytes()
        ensure_self_signed_cert(cert, key)  # idempotent
        assert cert.read_bytes() == before
