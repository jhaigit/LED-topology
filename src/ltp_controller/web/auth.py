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

# Endpoint names (not paths) exempt from authentication.
_PUBLIC_ENDPOINTS = {"static", "login", "healthz"}

_ANONYMOUS_ADMIN = Principal(name="anonymous", role="admin", kind="anonymous")


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
    # --- device auth keys: admin-only, and BEFORE the operator device rule
    #     below so it doesn't capture /api/sinks/<id>/key as a device target.
    (
        frozenset({"GET", "POST", "PUT", "DELETE"}),
        re.compile(r"^/api/sinks/[^/]+/key$"),
        _R(min_role="admin"),
    ),
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
        if not auth.login_limiter.allow(request.remote_addr or "?"):
            return render_template("login.html", error="Too many attempts — wait a minute."), 429
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        principal = auth.authenticate_password(username, password)
        if principal is None:
            logger.warning(f"Failed login for {username!r} from {request.remote_addr}")
            return render_template("login.html", error="Invalid username or password."), 401
        session.clear()
        session["u"] = principal.name
        session["t"] = time.time()
        session["csrf"] = secrets.token_urlsafe(32)
        logger.info(f"User {principal.name!r} logged in from {request.remote_addr}")
        target = request.args.get("next", "")
        # Only redirect within this app (no scheme/host, no protocol-relative).
        if not target.startswith("/") or target.startswith("//"):
            target = url_for("dashboard")
        return redirect(target)

    @app.route("/logout", methods=["POST"])
    def logout() -> Any:
        session.clear()
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
