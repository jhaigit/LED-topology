"""Security primitives for the controller web tier.

Framework-independent: password/token hashing, principals (roles + scopes),
the transport decision matrix (HTTP vs HTTPS vs refuse-to-start), and
first-run secret/certificate generation. Flask integration lives in
ltp_controller.web.auth.

See docs/proposals/security-access-control.md and
docs/proposals/security-implementation-plan.md.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PBKDF2_ITERATIONS = 600_000

ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}
AUTOMATION_RANK = {"none": 0, "run": 1, "manage": 2}
SCENES_RANK = {"run": 1, "manage": 2}


class SecurityConfigError(Exception):
    """Raised when the security configuration is invalid or unsafe."""


# ---------------------------------------------------------------------------
# Password / token hashing
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256 (stdlib only)."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2$sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored hash (pbkdf2 or argon2id)."""
    if stored.startswith("pbkdf2$"):
        try:
            _, algo, iterations, salt_hex, dk_hex = stored.split("$")
            dk = hashlib.pbkdf2_hmac(
                algo, password.encode(), bytes.fromhex(salt_hex), int(iterations)
            )
            return hmac.compare_digest(dk.hex(), dk_hex)
        except (ValueError, TypeError):
            return False
    if stored.startswith("$argon2"):
        try:
            from argon2 import PasswordHasher  # type: ignore[import-not-found]
        except ImportError:
            logger.error("argon2 password hash configured but argon2-cffi is not installed")
            return False
        try:
            PasswordHasher().verify(stored, password)
            return True
        except Exception:
            return False
    logger.error("Unrecognized password hash format")
    return False


# Fixed dummy hash so authentication of unknown users costs the same as a
# real verification (blunts username enumeration via timing).
_DUMMY_HASH = "pbkdf2$sha256$600000$" + "00" * 16 + "$" + "00" * 32


def generate_token() -> tuple[str, str]:
    """Generate a new API bearer token. Returns (token, stored_hash)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def verify_token(token: str, stored: str) -> bool:
    if not stored.startswith("sha256:"):
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    return hmac.compare_digest(digest, stored[len("sha256:") :])


# ---------------------------------------------------------------------------
# Principals: roles and scopes
# ---------------------------------------------------------------------------


class Scope(BaseModel):
    """Narrows what a role may touch. Absent fields grant the role's full reach."""

    devices: list[str] | None = None  # sink/source ids or group ids; None = all
    automation: Literal["none", "run", "manage"] = "manage"
    scenes: Literal["run", "manage"] = "manage"


class Principal(BaseModel):
    name: str
    role: Literal["admin", "operator", "viewer"]
    kind: Literal["user", "token", "anonymous"] = "user"
    scope: Scope = Field(default_factory=Scope)


class UserDef(BaseModel):
    name: str
    role: Literal["admin", "operator", "viewer"] = "admin"
    password_hash: str
    scope: Scope = Field(default_factory=Scope)


class TokenDef(BaseModel):
    name: str
    role: Literal["admin", "operator", "viewer"] = "operator"
    hash: str
    scope: Scope = Field(default_factory=Scope)


@dataclass
class Requirement:
    """What an endpoint demands of the caller.

    devices lists every device the operation targets; all must fall inside
    the principal's devices scope. automation/scenes name the minimum scope
    tier ("read" for automation additionally means merely seeing the data).
    """

    min_role: Literal["viewer", "operator", "admin"] = "viewer"
    devices: list[str] | None = None
    automation: Literal["read", "run", "manage"] | None = None
    scenes: Literal["run", "manage"] | None = None


def principal_allows(
    principal: Principal,
    req: Requirement,
    expand_devices: Any = None,
) -> bool:
    """Effective permission = intersection of role and scope (proposal §A.3).

    expand_devices: callable(list[str]) -> set[str] that expands group ids in
    a scope to member device ids; identity when omitted.
    """
    if principal.role == "admin":
        return True
    if ROLE_RANK[principal.role] < ROLE_RANK[req.min_role]:
        return False
    scope = principal.scope
    if req.automation is not None:
        if req.automation == "read":
            if scope.automation == "none":
                return False
        elif AUTOMATION_RANK[scope.automation] < AUTOMATION_RANK[req.automation]:
            return False
    if req.scenes is not None:
        if SCENES_RANK[scope.scenes] < SCENES_RANK[req.scenes]:
            return False
    if req.devices is not None and scope.devices is not None:
        allowed = set(scope.devices)
        if expand_devices is not None:
            allowed = set(expand_devices(scope.devices))
        for device in req.devices:
            if device not in allowed:
                return False
    return True


# ---------------------------------------------------------------------------
# Auth manager (credential verification + rate limiting)
# ---------------------------------------------------------------------------


class RateLimiter:
    """Fixed-window per-key limiter; thread-safe (Flask runs threaded)."""

    def __init__(self, max_attempts: int = 10, window_seconds: float = 60.0):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            start, count = self._attempts.get(key, (now, 0))
            if now - start >= self.window_seconds:
                start, count = now, 0
            count += 1
            self._attempts[key] = (start, count)
            # Opportunistic cleanup so the dict cannot grow unboundedly.
            if len(self._attempts) > 1024:
                self._attempts = {
                    k: v for k, v in self._attempts.items() if now - v[0] < self.window_seconds
                }
            return count <= self.max_attempts


class AuthManager:
    """Verifies credentials from the web.auth config block."""

    def __init__(
        self,
        users: list[UserDef],
        tokens: list[TokenDef],
        session_ttl: int = 43200,
    ):
        self.users = {u.name: u for u in users}
        self.tokens = tokens
        self.session_ttl = session_ttl
        self.login_limiter = RateLimiter(max_attempts=10, window_seconds=60.0)
        self.token_limiter = RateLimiter(max_attempts=30, window_seconds=60.0)

    @classmethod
    def from_config(cls, auth_config: dict[str, Any]) -> "AuthManager":
        users = [UserDef(**u) for u in auth_config.get("users", [])]
        tokens = [TokenDef(**t) for t in auth_config.get("tokens", [])]
        # Single password_hash shorthand: one admin user named "admin".
        if auth_config.get("password_hash") and not any(u.name == "admin" for u in users):
            users.append(
                UserDef(name="admin", role="admin", password_hash=auth_config["password_hash"])
            )
        if not users and not tokens:
            raise SecurityConfigError(
                "web.auth.enabled is true but no users, tokens, or password_hash "
                "are configured — this would lock everyone out. Add a credential "
                "(generate a hash with: ltp-controller --hash-password) or disable auth."
            )
        return cls(
            users=users,
            tokens=tokens,
            session_ttl=int(auth_config.get("session_ttl_seconds", 43200)),
        )

    def authenticate_password(self, username: str, password: str) -> Principal | None:
        user = self.users.get(username)
        stored = user.password_hash if user else _DUMMY_HASH
        ok = verify_password(password, stored)
        if ok and user:
            return Principal(name=user.name, role=user.role, kind="user", scope=user.scope)
        return None

    def authenticate_token(self, token: str) -> Principal | None:
        matched: TokenDef | None = None
        for t in self.tokens:
            # Check every token (no early exit) to keep timing uniform.
            if verify_token(token, t.hash):
                matched = t
        if matched:
            return Principal(
                name=matched.name, role=matched.role, kind="token", scope=matched.scope
            )
        return None

    def get_user_principal(self, username: str) -> Principal | None:
        """Re-resolve a session's user so config edits apply to live sessions."""
        user = self.users.get(username)
        if not user:
            return None
        return Principal(name=user.name, role=user.role, kind="user", scope=user.scope)


# ---------------------------------------------------------------------------
# Transport decision matrix (the with/without-HTTPS switch)
# ---------------------------------------------------------------------------


@dataclass
class TransportDecision:
    scheme: str  # "http" | "https"
    use_tls: bool
    insecure_transport: bool  # credentials cross the network in cleartext
    secure_cookies: bool
    trust_proxy: bool
    warnings: list[str] = field(default_factory=list)


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Unresolvable/hostname bind: assume reachable off-host (fail closed).
        return False


def resolve_transport(
    host: str,
    tls_mode: str = "auto",
    trust_proxy: bool = False,
    allow_insecure_http: bool = False,
    auth_enabled: bool = True,
) -> TransportDecision:
    """Implements the startup matrix from the implementation plan.

    Raises SecurityConfigError when the combination is unsafe and the operator
    has not explicitly opted out with allow_insecure_http.
    """
    # YAML 1.1 parses bare on/off as booleans, so "mode: off" arrives as False.
    if tls_mode is True:
        tls_mode = "on"
    elif tls_mode is False:
        tls_mode = "off"
    if tls_mode not in ("auto", "on", "off"):
        raise SecurityConfigError(f"web.tls.mode must be auto|on|off, got {tls_mode!r}")

    loopback = is_loopback_host(host)
    use_tls = tls_mode == "on" or (tls_mode == "auto" and not loopback)
    warnings: list[str] = []

    if use_tls:
        decision = TransportDecision(
            scheme="https",
            use_tls=True,
            insecure_transport=False,
            secure_cookies=True,
            trust_proxy=trust_proxy,
        )
    elif trust_proxy:
        decision = TransportDecision(
            scheme="http",
            use_tls=False,
            insecure_transport=False,
            secure_cookies=True,  # the proxy terminates TLS; X-Forwarded-Proto is trusted
            trust_proxy=True,
        )
    elif loopback:
        decision = TransportDecision(
            scheme="http",
            use_tls=False,
            insecure_transport=False,  # credentials never leave the host
            secure_cookies=False,
            trust_proxy=False,
        )
    else:
        if not allow_insecure_http:
            raise SecurityConfigError(
                f"web.host {host!r} is reachable from the network but TLS is off. "
                "Either enable TLS (web.tls.mode: on|auto), terminate TLS at a "
                "reverse proxy (web.tls.trust_proxy: true), or explicitly accept "
                "cleartext credentials with web.allow_insecure_http: true."
            )
        warnings.append(
            "INSECURE TRANSPORT: serving plain HTTP on a network-reachable "
            "address — login credentials, tokens, and session cookies will "
            "cross the network in cleartext (web.allow_insecure_http: true)."
        )
        decision = TransportDecision(
            scheme="http",
            use_tls=False,
            insecure_transport=True,
            secure_cookies=False,
            trust_proxy=False,
        )

    if not auth_enabled and not loopback:
        if not allow_insecure_http:
            raise SecurityConfigError(
                f"web.host {host!r} is reachable from the network but web.auth "
                "is not enabled. Configure web.auth (see docs/proposals/"
                "security-access-control.md) or explicitly accept an open API "
                "with web.allow_insecure_http: true."
            )
        warnings.append(
            "OPEN API: authentication is disabled on a network-reachable "
            "address — any host on the network has full control "
            "(web.allow_insecure_http: true)."
        )

    decision.warnings = warnings
    return decision


# ---------------------------------------------------------------------------
# First-run secrets and certificates
# ---------------------------------------------------------------------------


def default_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "ltp"


def ensure_secret_key(config_dir: Path | None = None) -> bytes:
    """Load or create the persistent session-signing key (0600)."""
    config_dir = config_dir or default_config_dir()
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_file = config_dir / "secret_key"
    if key_file.exists():
        return key_file.read_bytes()
    key = secrets.token_bytes(32)
    key_file.touch(mode=0o600)
    key_file.write_bytes(key)
    logger.info(f"Generated web session secret key at {key_file}")
    return key


def ensure_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    """Generate a self-signed certificate pair if it does not exist yet."""
    if cert_path.exists() and key_path.exists():
        return
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError as e:
        raise SecurityConfigError(
            "TLS is enabled but no certificate exists and the 'cryptography' "
            "package is not installed to generate one. Install it "
            "(pip install 'ltp[controller]') or provide web.tls.cert/key."
        ) from e

    import datetime
    import socket

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ltp-controller")])
    san_entries: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    hostname = socket.gethostname()
    if hostname and hostname != "localhost":
        san_entries.append(x509.DNSName(hostname))
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_path.touch(mode=0o600)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    logger.info(f"Generated self-signed TLS certificate at {cert_path}")


# ---------------------------------------------------------------------------
# Bundle handed to create_app
# ---------------------------------------------------------------------------


@dataclass
class WebSecuritySettings:
    """Everything the Flask layer needs to know, resolved at startup."""

    auth: AuthManager | None = None  # None = authentication disabled
    secret_key: bytes | None = None
    secure_cookies: bool = False
    insecure_transport: bool = False
    trust_proxy: bool = False

    @property
    def auth_enabled(self) -> bool:
        return self.auth is not None
