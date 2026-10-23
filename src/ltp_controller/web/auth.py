"""Authentication, authorization, and CSRF for the controller web app.

Enforcement is centralized in a before_request gate rather than per-route
decorators: authentication (session cookie or bearer token), CSRF for
session-authenticated mutations, then an ordered policy table that maps
(method, path) to a Requirement checked against the principal's role and
scope. Unmatched mutating endpoints require admin (default deny).
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from typing import Any, Callable, cast

from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ltp_controller.security import (
    Principal,
    Requirement,
    WebSecuritySettings,
    principal_allows,
)

logger = logging.getLogger(__name__)

_MUTATING = {"POST", "PUT", "DELETE", "PATCH"}
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# Endpoint names (not paths) exempt from authentication.
_PUBLIC_ENDPOINTS = {"static", "login", "healthz", "api_session"}

# Sessions expire on idle: the issue stamp is pushed forward by user activity
# (mutations and page navigations — not the pages' background API polling, so
# an abandoned tab still times out). Rewriting the cookie on every such request
# is pointless churn; refresh at most this often.
SESSION_REFRESH_INTERVAL = 60.0
# Client-side warning threshold, reported by /api/session as `warn_below`.
SESSION_WARN_BELOW = 300.0

_ANONYMOUS_ADMIN = Principal(name="anonymous", role="admin", kind="anonymous")
# Granted to unauthenticated read requests when public_read is enabled: can see
# state (viewer role), but the policy table still blocks admin-gated reads.
_ANONYMOUS_VIEWER = Principal(name="anonymous", role="viewer", kind="anonymous")


# ---------------------------------------------------------------------------
# Policy table
# ---------------------------------------------------------------------------


def _json(silent_request: Any) -> dict[str, Any]:
    data = silent_request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _rules_bulk(m: re.Match, req: Any, svc: "_Services") -> Requirement:
    action = _json(req).get("action")
    if action == "delete":
        return Requirement(min_role="operator", automation="manage")
    return Requirement(min_role="operator", automation="run")


def _route_target(m: re.Match, req: Any, svc: "_Services") -> Requirement:
    route = svc.router.get_route(m.group("rid")) if svc.router else None
    devices = [route.sink_id] if route else []
    return Requirement(min_role="operator", devices=devices)


def _route_create(m: re.Match, req: Any, svc: "_Services") -> Requirement:
    sink_id = _json(req).get("sink_id")
    return Requirement(min_role="operator", devices=[sink_id] if sink_id else [])


def _routes_bulk(m: re.Match, req: Any, svc: "_Services") -> Requirement:
    devices = []
    if svc.router:
        for rid in _json(req).get("route_ids", []):
            route = svc.router.get_route(rid)
            if route:
                devices.append(route.sink_id)
    return Requirement(min_role="operator", devices=devices)


def _group_members_from_body(m: re.Match, req: Any, svc: "_Services") -> Requirement:
    devices = []
    for member in _json(req).get("members", []):
        if isinstance(member, dict) and member.get("sink_id"):
            devices.append(member["sink_id"])
        elif isinstance(member, str):
            devices.append(member)
    return Requirement(min_role="operator", devices=devices)


def _group_members_existing(m: re.Match, req: Any, svc: "_Services") -> Requirement:
    devices = []
    group = svc.groups.get(m.group("gid")) if svc.groups else None
    if group:
        devices = [mem.sink_id for mem in group.members]
    return Requirement(min_role="operator", devices=devices)


def _device_target(param: str) -> Callable[..., Requirement]:
    def build(m: re.Match, req: Any, svc: "_Services") -> Requirement:
        return Requirement(min_role="operator", devices=[m.group(param)])

    return build


_R = Requirement

# Ordered: first match wins. (methods, path regex, Requirement or builder).
_POLICY: list[tuple[frozenset[str], re.Pattern[str], Any]] = [
    # --- admin surface ---
    (frozenset({"POST"}), re.compile(r"^/api/config/save$"), _R(min_role="admin")),
    # --- config exports reveal full rule/route definitions ---
    (frozenset({"GET"}), re.compile(r"^/api/config/.*export$"), _R(min_role="operator")),
    # --- automation (rules, sequences, schedule triggers ride on rules) ---
    (frozenset({"POST"}), re.compile(r"^/api/rules/bulk-action$"), _rules_bulk),
    (
        frozenset({"POST"}),
        re.compile(r"^/api/rules/[^/]+/(enable|disable)$"),
        _R(min_role="operator", automation="run"),
    ),
    (
        frozenset({"POST"}),
        re.compile(r"^/api/sequences/[^/]+/(start|stop|pause|resume)$"),
        _R(min_role="operator", automation="run"),
    ),
    (
        frozenset(_MUTATING),
        re.compile(r"^/api/(rules|sequences)(/[^/]+)?$"),
        _R(min_role="operator", automation="manage"),
    ),
    (frozenset({"GET"}), re.compile(r"^/api/(rules|sequences)"), _R(automation="read")),
    (frozenset({"GET"}), re.compile(r"^/rules$"), _R(automation="read")),
    # --- scenes ---
    (
        frozenset({"POST"}),
        re.compile(r"^/api/scenes/[^/]+/activate$"),
        _R(min_role="operator", scenes="run"),
    ),
    (
        frozenset(_MUTATING),
        re.compile(r"^/api/scenes(/[^/]+)?$"),
        _R(min_role="operator", scenes="manage"),
    ),
    # --- housekeeping (before the <sid> patterns so "purge" isn't a device id) ---
    (
        frozenset({"POST"}),
        re.compile(r"^/api/(sinks|sources)/purge$|^/api/discovery/refresh$"),
        _R(min_role="operator"),
    ),
    # --- device auth keys + pairing: admin-only, and BEFORE the operator
    #     device rule below so it doesn't capture these as a device target.
    (
        frozenset({"GET", "POST", "PUT", "DELETE"}),
        re.compile(r"^/api/sinks/[^/]+/(key|pair)$"),
        _R(min_role="admin"),
    ),
    # --- fleet enrollment (Phase 5.1): mutating actions (enroll/revoke) are
    #     admin-only; listing requires a signed-in viewer.
    (
        frozenset(_MUTATING),
        re.compile(r"^/api/fleets(/[^/]+)?(/enroll)?$"),
        _R(min_role="admin"),
    ),
    (frozenset({"GET"}), re.compile(r"^/api/fleets$"), _R(min_role="viewer")),
    # --- session keep-alive: any signed-in user may extend their own session.
    (frozenset({"POST"}), re.compile(r"^/api/session/refresh$"), _R(min_role="viewer")),
    (frozenset({"GET"}), re.compile(r"^/fleets$"), _R(min_role="viewer")),
    # --- per-device control (scope-checked at the resolved target) ---
    (
        frozenset(_MUTATING),
        re.compile(r"^/api/sinks/(?P<sid>[^/]+)(/|$)"),
        _device_target("sid"),
    ),
    (
        frozenset(_MUTATING),
        re.compile(r"^/api/sources/(?P<sid>[^/]+)(/|$)"),
        _device_target("sid"),
    ),
    # --- routes target a sink; scope-checked against it ---
    (frozenset({"POST"}), re.compile(r"^/api/routes$"), _route_create),
    (frozenset({"POST"}), re.compile(r"^/api/routes/bulk-action$"), _routes_bulk),
    (
        frozenset(_MUTATING),
        re.compile(r"^/api/routes/(?P<rid>[^/]+)(/(enable|disable))?$"),
        _route_target,
    ),
    # --- sink groups grant control over their members ---
    (frozenset({"POST"}), re.compile(r"^/api/sink-groups$"), _group_members_from_body),
    (
        frozenset(_MUTATING),
        re.compile(r"^/api/sink-groups/(?P<gid>[^/]+)$"),
        _group_members_existing,
    ),
    # --- controller-local sources: operator, no device scope ---
    (
        frozenset(_MUTATING),
        re.compile(r"^/api/(virtual-sources|scalar-sources)(/|$)"),
        _R(min_role="operator"),
    ),
]

_DEFAULT_READ = Requirement(min_role="viewer")
# Default deny: any mutating endpoint not classified above is admin-only.
_DEFAULT_MUTATE = Requirement(min_role="admin")


class _Services:
    """Late-bound handles the policy builders need."""

    def __init__(self, app: Flask):
        self._app = app

    @property
    def router(self) -> Any:
        return self._app.config.get("router")

    @property
    def controller(self) -> Any:
        return self._app.config.get("controller")

    @property
    def groups(self) -> Any:
        return getattr(self.controller, "sink_group_manager", None)

    def expand_devices(self, entries: list[str]) -> set[str]:
        """Expand group ids/names in a scope to member sink ids (checked live,
        so membership changes apply immediately — proposal open question 11)."""
        allowed = set(entries)
        mgr = self.groups
        if mgr is not None:
            for entry in entries:
                group = mgr.get(entry)
                if group is not None:
                    allowed.update(m.sink_id for m in group.members)
        return allowed


def requirement_for(method: str, path: str, req: Any, svc: _Services) -> Requirement:
    for methods, pattern, spec in _POLICY:
        if method not in methods:
            continue
        m = pattern.match(path)
        if not m:
            continue
        if callable(spec):
            return cast(Requirement, spec(m, req, svc))
        return cast(Requirement, spec)
    return _DEFAULT_MUTATE if method in _MUTATING else _DEFAULT_READ


# ---------------------------------------------------------------------------
# Flask integration
# ---------------------------------------------------------------------------


def init_auth(app: Flask, settings: WebSecuritySettings) -> None:
    """Install the auth gate, login/logout routes, CSRF, and template context."""
    services = _Services(app)
    auth = settings.auth

    @app.route("/healthz")
    def healthz() -> Any:
        return jsonify({"status": "ok"})

    @app.context_processor
    def _inject_auth_context() -> dict[str, Any]:
        return {
            "auth_enabled": settings.auth_enabled,
            "principal": g.get("principal"),
            "insecure_transport": settings.insecure_transport,
            "csrf_token": session.get("csrf", "") if settings.auth_enabled else "",
        }

    def _session_status() -> dict[str, Any]:
        """What the browser's session cookie currently buys it. Lets pages
        notice an expired login *before* the user starts an interaction
        that will fail at the end (see the session monitor in app.js)."""
        if auth is None:
            return {"auth_enabled": False, "authenticated": False}
        principal = _session_principal()
        if principal is None:
            return {"auth_enabled": True, "authenticated": False}
        remaining = max(0.0, auth.session_ttl - (time.time() - float(session["t"])))
        return {
            "auth_enabled": True,
            "authenticated": True,
            "name": principal.name,
            "role": principal.role,
            "expires_in": round(remaining),
            "warn_below": SESSION_WARN_BELOW,
        }

    @app.route("/api/session")
    def api_session() -> Any:
        # Public (no gate): an expired session must be reportable as such
        # rather than answered with a 401/redirect. Never refreshes the stamp.
        return jsonify(_session_status())

    @app.route("/api/session/refresh", methods=["POST"])
    def api_session_refresh() -> Any:
        # Gated + CSRF-checked like any mutation; being a mutation, the gate
        # has already slid the expiry forward. Just report the new state.
        return jsonify(_session_status())

    if auth is None:
        # Authentication disabled: everything runs with full (anonymous)
        # authority — exactly today's behavior. The startup matrix in cli.py
        # has already decided this is either loopback-only or explicitly
        # accepted via allow_insecure_http.
        @app.before_request
        def _anonymous() -> None:
            g.principal = _ANONYMOUS_ADMIN

        return

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if request.method == "GET":
            if _session_principal() is not None:
                return redirect(url_for("dashboard"))
            return render_template("login.html", error=None)
        # The in-page login modal posts with Accept: application/json so it
        # can stay on the page (and replay the request that got the 401)
        # instead of navigating; the no-JS /login form gets HTML as before.
        wants_json = request.accept_mimetypes.best == "application/json"

        def fail(message: str, status: int) -> Any:
            if wants_json:
                return jsonify({"error": message}), status
            return render_template("login.html", error=message), status

        if not auth.login_limiter.allow(request.remote_addr or "?"):
            return fail("Too many attempts — wait a minute.", 429)
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        principal = auth.authenticate_password(username, password)
        if principal is None:
            logger.warning(f"Failed login for {username!r} from {request.remote_addr}")
            return fail("Invalid username or password.", 401)
        session.clear()
        session["u"] = principal.name
        session["t"] = time.time()
        session["csrf"] = secrets.token_urlsafe(32)
        logger.info(f"User {principal.name!r} logged in from {request.remote_addr}")
        if wants_json:
            return jsonify(
                {
                    "status": "ok",
                    "csrf": session["csrf"],
                    "principal": {"name": principal.name, "role": principal.role},
                    "session": _session_status(),
                }
            )
        target = request.args.get("next", "")
        # Only redirect within this app (no scheme/host, no protocol-relative).
        if not target.startswith("/") or target.startswith("//"):
            target = url_for("dashboard")
        return redirect(target)

    @app.route("/logout", methods=["POST"])
    def logout() -> Any:
        session.clear()
        # With public read on, the pages are still viewable anonymously —
        # land back on the dashboard rather than the login form.
        if settings.public_read:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    def _session_principal() -> Principal | None:
        username = session.get("u")
        issued = session.get("t")
        if not username or not isinstance(issued, (int, float)):
            return None
        if time.time() - issued > auth.session_ttl:
            return None
        return auth.get_user_principal(username)

    def _bearer_principal() -> Principal | None:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        if not auth.token_limiter.allow(request.remote_addr or "?"):
            return None
        return auth.authenticate_token(header[len("Bearer ") :])

    @app.before_request
    def _gate() -> Any:
        g.principal = None
        if request.endpoint in _PUBLIC_ENDPOINTS:
            return None

        principal = _bearer_principal()
        via_session = False
        if principal is None:
            principal = _session_principal()
            via_session = principal is not None

        if principal is None:
            # Open read access: an unauthenticated read gets a viewer principal
            # so system state is visible without logging in (mutations and
            # admin-gated reads are still refused by the policy table below).
            if settings.public_read and request.method in _READ_METHODS:
                principal = _ANONYMOUS_VIEWER
            else:
                # API paths always answer 401 JSON (curl's Accept: */* would
                # otherwise count as "accepts HTML" and get a redirect); page
                # requests from browsers bounce to the login form.
                if (
                    not request.path.startswith("/api/")
                    and request.method == "GET"
                    and request.accept_mimetypes.accept_html
                ):
                    return redirect(url_for("login", next=request.full_path.rstrip("?")))
                return jsonify({"error": "unauthorized"}), 401

        g.principal = principal

        # Sliding expiry: user activity keeps a login alive. Background API
        # polling (GET /api/...) deliberately does not count, so a tab left
        # open overnight still times out; page navigations and mutations do.
        if via_session and (
            request.method in _MUTATING
            or (request.method == "GET" and not request.path.startswith("/api/"))
        ):
            issued = float(session.get("t", 0))
            if time.time() - issued > SESSION_REFRESH_INTERVAL:
                session["t"] = time.time()

        # CSRF: only session-cookie auth is forgeable cross-site; bearer
        # requests carry the credential explicitly and are exempt.
        if via_session and request.method in _MUTATING:
            supplied = request.headers.get("X-CSRF-Token") or request.form.get("_csrf")
            expected = session.get("csrf", "")
            if not expected or not secrets.compare_digest(supplied or "", expected):
                return jsonify({"error": "CSRF token missing or invalid"}), 403

        requirement = requirement_for(request.method, request.path, request, services)
        if not principal_allows(principal, requirement, services.expand_devices):
            logger.warning(
                f"Forbidden: {principal.kind} {principal.name!r} ({principal.role}) "
                f"-> {request.method} {request.path}"
            )
            return jsonify({"error": "forbidden"}), 403
        return None
