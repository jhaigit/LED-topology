# Implementation Plan: Security Access Control

Status: Draft · 2026-07-07
Implements: [security-access-control.md](security-access-control.md)

One amendment to the proposal is baked into this plan (§1.3/§1.4 there):
**HTTPS is configurable, not mandatory.** The controller must be able to run
with or without TLS at the top level by explicit configuration. Running
non-loopback without TLS is a conscious, explicitly-acknowledged downgrade —
credentials (password, tokens, session cookie) then cross the LAN in cleartext
and the proposal's confidentiality boundary is knowingly waived by the
operator. The system fails closed unless that acknowledgment is present.

## Transport modes (the with/without-HTTPS requirement)

Config:

```yaml
web:
  host: "127.0.0.1"            # new default (was 0.0.0.0)
  port: 8080
  tls:
    mode: auto                 # auto | on | off
    cert: "~/.config/ltp/web-cert.pem"   # auto-generated self-signed if absent
    key:  "~/.config/ltp/web-key.pem"
    trust_proxy: false         # TLS terminated upstream; trust X-Forwarded-Proto
  allow_insecure_http: false   # explicit ack: serve plain HTTP off-loopback
```

Startup decision matrix (enforced in `cli.py` before the web thread starts;
`tls.mode: auto` resolves to `on` when `host` is non-loopback, `off` on
loopback):

| `host` | TLS resolved | `allow_insecure_http` | Result |
|---|---|---|---|
| loopback | off | — | HTTP. Fine: credentials never leave the host. |
| any | on | — | HTTPS. Self-signed cert auto-generated on first run if none configured. |
| non-loopback | off | `false`/absent | **Refuse to start.** Error names both remedies: enable TLS or set `allow_insecure_http: true`. |
| non-loopback | off | `true` | **HTTP, degraded-security mode** (below). |
| non-loopback | off (`trust_proxy: true`) | — | HTTP to the proxy; requests treated as secure only when `X-Forwarded-Proto: https`. |

Degraded-security (plain HTTP off-loopback) mode is fully functional but loud:

- One prominent `WARNING` at startup: credentials will cross the network in
  cleartext.
- A persistent banner in the web UI on every page ("Insecure transport —
  login credentials are not protected on this network").
- Session cookies drop the `Secure` flag (they must, or browsers discard
  them) but keep `HttpOnly` + `SameSite=Strict`. Bearer tokens are accepted.
- Everything else — auth, roles, scopes, CSRF — behaves identically, so the
  security model degrades only in transport confidentiality, not in access
  control.

Auth itself is likewise independently switchable (`web.auth.enabled`), with
the same fail-closed rule: non-loopback bind with auth disabled refuses to
start unless `allow_insecure_http: true` also covers that case — i.e. the
flag is the single "I accept LAN exposure" acknowledgment.

## Phase 1 — Layer 1: web auth, authorization, transport modes

All host-side Python. No protocol change. Ships alone.

**1a. Config plumbing** (`cli.py`)
- Parse `web.auth`, `web.tls`, `allow_insecure_http`; change `host` default
  to `127.0.0.1`; add `--insecure-http` / `--tls-cert/--tls-key` CLI flags
  mirroring the YAML.
- Startup validation implementing the matrix above; clear, actionable error
  strings.
- First-run secrets: create `~/.config/ltp/` (0700), generate and persist
  Flask `secret_key` (0600).

**1b. Credential store** (`web/auth.py`, new)
- Password hashing: PBKDF2-HMAC-SHA256 via stdlib `hashlib` (no new
  dependency); verify with `hmac.compare_digest`. Accept `$argon2id$` hashes
  too when `argon2-cffi` is importable.
- `ltp-controller hash-password` CLI subcommand so operators can generate
  hashes for the YAML.
- Principals model (pydantic): `users` / `tokens`, each with `role`
  (admin/operator/viewer) and optional `scope` (`devices`, `automation`:
  none/run/manage, `scenes`: run/manage), per proposal §A.
- Single `password_hash` shorthand = one admin user, per proposal §1.2.

**1c. Authentication gate** (`web/auth.py` + `create_app`)
- `before_request` hook: allow-list login/static/healthz; accept signed
  session cookie or `Authorization: Bearer` token; else 401 (JSON) or
  redirect to login (HTML).
- Login/logout routes + minimal login template consistent with `base.html`.
- In-memory rate limit on login and token verification (per-IP, fixed
  window); constant-time comparisons throughout.

**1d. TLS serving** (`cli.py`)
- `app.run(ssl_context=...)` with the configured cert/key (werkzeug supports
  this; the web tier already runs in a daemon thread).
- Self-signed cert auto-generation on first HTTPS run. Needs the
  `cryptography` package — add to the `controller` extra (it is also
  required later for X25519 pairing in Phase 4, so it earns its place).
- Log the correct scheme in the "Web interface available at …" line.

**1e. CSRF** (`web/auth.py` + `static/js/app.js`)
- Per-session token; require `X-CSRF-Token` header on POST/PUT/DELETE for
  session-authenticated requests (bearer-token requests are exempt — no
  cookie, no CSRF risk). Inject the token via a meta tag in `base.html`;
  add it centrally in the shared JS fetch helper.

**1f. Authorization enforcement**
- `@requires(action, resource)` decorator on each mutating route in
  `web/app.py`, per the endpoint table in proposal §A.5; 403 distinct
  from 401.
- Device-scope checks at the resolution points, not only in routes:
  `Controller.set_device_control`, route creation in `RoutingEngine`,
  and `SinkController` operations.
- Template context gets the principal's role/scope so the UI hides
  controls the caller cannot use.

**1g. Tests + docs**
- Unit: hash/verify, token parsing, scope intersection logic.
- App-level (Flask test client): 401/403 matrix per role × endpoint class;
  CSRF acceptance/rejection; loopback-no-TLS OK; the four startup-matrix
  outcomes (start / refuse / degraded warning); cookie flags per mode.
- Upgrade notes: the default-bind change and first-run browser cert trust
  are the two operator-visible behavior changes.

Exit criteria: an unauthenticated LAN host can no longer reach any mutating
endpoint; credentials ride TLS unless the operator explicitly opted out;
`viewer`/scoped-`operator` limits hold at the API, not just the UI.

## Phase 2 — Layer 2: device claim + anti-hijack (network devices)

Protocol 0.1 → 0.2 (all additions negotiated; Level 0 devices unaffected).

- **libltp**: new `MessageType`s (`auth_challenge_request`, `auth_challenge`,
  `claim`, `claim_response`, `claim_renew`, `release`) and `ErrorCode`s
  (`UNAUTHORIZED=8`, `LEASE_HELD=9`) in `types.py`; `auth` object in
  `capability_response`; `token`/`seq`/`mac` fields on privileged messages;
  `auth=none|siphash|hmac` in mDNS TXT (`discovery.py`).
- **SipHash-2-4**: small pure-Python implementation in `libltp` (keyed MAC of
  short control messages; no dependency needed) + test vectors from the
  SipHash paper.
- **Controller side**: keystore `~/.config/ltp/keys.yaml` (0600); claim/renew/
  release session management in `ControlClient` (`transport.py`) — derive
  session key, MAC privileged messages, monotonic `seq`; surface
  `LEASE_HELD`/`UNAUTHORIZED` in the UI; per-device policy knobs
  (`min_level`, `allow_insecure`, `read_open`, `preempt`).
- **ESP32 firmware**: PSK in NVS; challenge/claim/lease state machine; MAC
  verification on privileged commands; UDP stream source-binding to the
  owner (`stream_setup` fixes the accepted source IP:port); gate or remove
  the port-23 telnet console.
- **Tests**: loopback claim/renew/expiry/second-controller-rejection against
  a Python mock device; MAC-tamper and replay rejection.

## Phase 3 — Serial bridge enforcement + optional AVR auth

- `ltp_serial_sink` presents Level 2 on its network side (reuses all Phase-2
  controller/device code paths); AVR behind it stays Level 0 — zero MCU RAM
  cost, covers the common case.
- Optional AVR opcodes behind a compile flag, off by default (2 KB budget):
  `CMD_AUTH_CHALLENGE=0x46`, `CMD_AUTH_CLAIM=0x47`, `CMD_SET_AUTH_KEY=0x48`,
  `CMD_CLEAR_AUTH=0x49`; `CAPS_AUTH=0x80`; `ERR_UNAUTHORIZED=0x0C`; PSK in
  EEPROM (bump `CONFIG_VERSION`). SipHash in C fits ~300–500 B flash, ~64 B
  transient RAM, nothing on the frame path.

## Phase 4 — Art-Net containment, pairing UX, key management

- `allow_insecure: true` required per Level-0 device before the router will
  target it; `ArtNetSink` runs at Level 2 (or loopback-bound); egress
  allowlist of `{host, universe}` in `sender.py`; VLAN segmentation docs.
- Pairing UX in the web UI (admin-only): serial devices — generate key, push
  over USB; network devices — X25519 ECDH with PIN-bound derivation during a
  pairing window (`pair_begin` / `pair_ecdh` / `pair_complete`), using
  `cryptography` on the controller and mbedTLS on ESP32. Unpair / rotate-key
  actions.

## Sequencing and sizing

| Phase | Size | Depends on | Delivers |
|---|---|---|---|
| 1 | M–L (pure Python) | — | Closes the widest hole; the HTTPS/HTTP switch |
| 2 | L (Python + ESP32 firmware) | 1 (keystore behind auth) | Cryptographic anti-hijack |
| 3 | S–M | 2 | Serial fleet covered via bridge |
| 4 | M | 1 (UX), 2 (pairing msgs) | Containment + operator-friendly keys |

Phase 1 is independently shippable and should land first; it also carries the
requested HTTP/HTTPS configurability. Phases 2–4 follow in order; 3 and 4 can
overlap.
