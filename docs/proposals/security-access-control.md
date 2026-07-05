# Proposal: Basic Access Control and Anti-Hijack for LED Topology

Status: Draft · 2026-07-05
Scope: top-level access control + prevention of unauthorized device takeover.
Confidentiality boundary: **operational data** (pixel frames, control values,
device state) may travel in cleartext; **access-control credentials** (passwords,
API tokens, session cookies, device keys) must **never** appear in cleartext on
the wire. Full payload/transport encryption is a non-goal.

## Problem

LED Topology has **no authentication or access control anywhere**. Any host on
the LAN can today:

- Open the controller's web UI and full JSON API — it binds `0.0.0.0:8080` by
  default (`cli.py:81-85`, `configs/controller-example.yaml`) with no login,
  session, CSRF, or API token (`web/app.py` has no `before_request`,
  `secret_key`, or `session[...]`). Every route/sink/scene/rule/paint mutation
  is an unauthenticated `POST`/`PUT`/`DELETE`.
- Discover every device via mDNS, open its TCP control port, and read/write all
  controls, issue `route_create`/`stream_setup`, or blast UDP pixel frames
  (gated only by a 2-byte magic, `protocol.py:382`).
- Drive a device that another controller is already using — there is **no
  ownership, claim, or lease** concept (`controller.py`, `router.py`). Two
  controllers can fight over the same sink; a rogue one can "hijack" it.
- On ESP32c3 firmware, reach an **unauthenticated telnet console on port 23**
  (`ltp_esp32c3_oled/telnet_server.h:4-71`) and inject UDP frames on 5001.

Device identity is a self-asserted, self-generated `uuid4` broadcast in a
plaintext mDNS TXT record (`discovery.py:115-132`, `types.py:306`). Nothing binds
the advertised `id` to the physical device, so identity is spoofable.

The project spec acknowledges all of this is deferred: *"LTP is designed for
trusted local networks"* (`spec/protocol.md:808-824`). This proposal adds the
**basic** controls that were deferred, without taking on full transport
encryption.

A guiding distinction runs through the whole design: we do **not** try to hide
what the system is doing — pixel frames and control values are operational and
may be sniffed. We **do** ensure that the secrets used to *gain access* — the
web password, API tokens, session cookies, and device keys — are never exposed
in cleartext where an eavesdropper could capture and replay them. That is the
difference between "no wire privacy" (fine) and "credentials on the wire in the
clear" (not fine).

## Goals and Non-Goals

**Goals**

1. **Top-level access control.** The controller (web UI + API) is the front
   door to the whole system; gate it behind a credential and stop binding all
   interfaces by default.
2. **Least-privilege authorization.** Beyond *who* may connect, control *what*
   each credential may do: an admin who manages everything, an operator limited
   to one device or group, a credential barred from touching scheduled
   automation, a read-only viewer. Authentication is not enough; privilege
   levels are a first-class requirement.
3. **Anti-hijack.** A device should only accept privileged commands from a
   controller that has proven it holds the device's shared key, and should not
   let a second, unauthorized controller take it over.
4. **No credentials in cleartext.** No password, API token, session cookie, or
   device key may cross the wire in a form an eavesdropper could capture and
   reuse. This is achieved two ways: on the constrained device channel by
   *never transmitting the secret at all* (challenge-response + a keyed MAC over
   a derived, never-sent session key); on the web channel by *encrypting the
   channel that carries the credential* (TLS). Note this protects the
   credentials, not the operational payloads they authorize.
5. **Accommodate devices that cannot authenticate** — Art-Net fixtures, legacy
   firmware, third-party gear — without blocking them.
6. **Fit the smallest target.** The ATmega328P has 2 KB RAM; the scheme must be
   cheap and must not touch the per-frame hot path.
7. **Opt-in and backward compatible.** Nothing breaks for existing deployments
   until an operator turns security on.

**Non-Goals**

- **Operational confidentiality.** Pixel payloads and control values are not
  encrypted on the data path. An on-path sniffer can see *what colors are
  showing* and *what a brightness value is*. This is accepted (per request) —
  it is explicitly distinct from credential confidentiality, which is a goal.
- Per-frame cryptographic authentication of high-rate pixel data.
- Defeating a determined attacker with a MITM foothold on the same L2 segment
  (see *Residual Risks*). We defend the common cases: unauthorized off-path
  controllers, accidental cross-control, and casual takeover.
- **Enterprise RBAC** — arbitrary custom roles, a permission-editor UI, LDAP/SSO,
  per-endpoint ACL authoring. We provide a small **fixed** role set
  (admin / operator / viewer) plus per-device and per-automation **scoping**
  (see *Authorization*); that covers "admin vs. control-one-device vs. no
  scheduled operations" without a policy engine.

## Current State (what exists to build on)

| Layer | Where | Auth today |
|---|---|---|
| Web UI + JSON API | `web/app.py`, served `cli.py:181-185` | none; binds `0.0.0.0` |
| Network control | JSON/TCP, `libltp/transport.py` `ControlServer/Client` | none; first byte is a privileged `capability_request` |
| Network data | binary/UDP `DataPacket`, `libltp/protocol.py:270` | 2-byte magic only |
| Serial control | binary v2, `ltp_serial_cli/protocol.py`, `LtpProtocol` | none; obeys any byte stream on the UART |
| Discovery | mDNS `_ltp-{sink,source,controller}._tcp`, `discovery.py` | self-asserted UUID in TXT |
| Capabilities | `CAPS_*` (serial), `capability_response` (network) | feature bits, **not** permissions |
| Art-Net | `ltp_artnet/` — an LTP sink that re-emits Art-Net UDP broadcast | none (Art-Net has no auth) |

Two useful hooks already exist: capability advertisement (so we can negotiate
auth support cleanly) and the `CAPS_EXTENDED` byte (room for an auth bit).

## Threat Model and Trust Boundaries

We protect three assets: (A) the controller's control surface, (B) a device's
willingness to obey commands, (C) the integrity of an established control
session.

```
   [ operator / scripts ]
          |  Layer 1: web UI + API auth  (asset A)
          v
   [ CONTROLLER ]  <-- keystore of per-device PSKs (protected by Layer 1)
          |  Layer 2: claim + challenge-response + per-message MAC  (assets B,C)
          v
   [ network device ]           [ serial bridge ] --USB--> [ AVR / MCU ]
   (ESP32, LTP sink)                  |  Layer 3
          |                           v  (point-to-point, physical trust)
   (native enforcement)        enforce Layer 2 on the network side
          |
   [ Art-Net egress ] --broadcast--> [ dumb DMX fixtures ]  (untrusted zone)
```

Trust assumptions:

- **Physical/serial access = full trust.** Whoever holds the USB cable owns the
  AVR. We do not try to defend a directly-wired MCU from its host.
- **The controller is the trust anchor.** It holds device keys; compromising it
  (via Layer 1) compromises everything downstream, so Layer 1 is load-bearing.
- **The Art-Net segment is untrusted-downstream.** Fixtures can't authenticate;
  we contain them rather than secure them.

## Design Overview — Three Security Levels

Security is a per-deployment (and per-device) policy, negotiated via
capabilities. All levels are backward compatible; **Level 0 is today's
behavior**.

| Level | Name | What it adds | Default for |
|---|---|---|---|
| 0 | Open | nothing (current behavior) | Art-Net, legacy, third-party |
| 1 | Gated | web UI/API auth + TLS for credentials + loopback bind | **new default for the controller** |
| 2 | Paired | device claim + challenge-response + control-plane MAC | capable devices (ESP32; opt-in AVR) |

A device advertises the highest level it supports; the controller enforces a
configurable minimum and may explicitly allow lower-level devices per device.

## Layer 1 — Controller Access Control (Web UI + API)

This is the "basic access control at the top level" and the single biggest win:
it is pure host-side Python, no protocol change, and closes the widest hole.

### 1.1 Authentication gate

Add a `before_request` hook in `create_app` (`web/app.py:23`) that requires,
for every route except the login page and static assets:

- a valid **signed session cookie** (human login), or
- a valid **API bearer token** (`Authorization: Bearer <token>`) for scripts.

```python
# web/auth.py (new)
@app.before_request
def _require_auth():
    if request.endpoint in _PUBLIC_ENDPOINTS:      # login, static, healthz
        return
    if _valid_bearer(request.headers.get("Authorization")):
        return
    if session.get("uid") and _session_fresh(session):
        return
    if request.accept_mimetypes.accept_html:
        return redirect(url_for("login"))
    return jsonify(error="unauthorized"), 401
```

### 1.2 Credentials and config

Extend the YAML config with a `web.auth` block; passwords stored **hashed**
(argon2id or PBKDF2-HMAC-SHA256), never plaintext:

```yaml
web:
  host: "127.0.0.1"          # CHANGED default: was 0.0.0.0
  port: 8080
  auth:
    enabled: true            # CHANGED default: was (absent) = off
    password_hash: "$argon2id$v=19$m=65536,t=3,p=4$..."   # minimal one-admin form;
                             # see Authorization §A.4 for multi-principal users/roles
    tokens:                  # long random bearer tokens for automation
      - name: "home-assistant"
        hash: "sha256:...."
    session_ttl_seconds: 43200
  tls:                       # protects the credential-bearing channel
    enabled: true            # REQUIRED whenever host is non-loopback
    cert: "~/.config/ltp/web-cert.pem"   # auto-generated self-signed on first run
    key:  "~/.config/ltp/web-key.pem"
    # or terminate TLS at a reverse proxy and set trust_proxy: true instead
```

### 1.3 TLS for the credential channel (required off-loopback)

The web tier is where real reusable credentials live — the login password, API
bearer tokens, and the session cookie. Sending any of these over plain HTTP puts
them in cleartext on the wire, which the confidentiality boundary forbids.
Therefore:

- **TLS is required whenever the web tier is reachable off-loopback.** The
  controller serves HTTPS directly (Python `ssl` context on the Flask/WSGI
  server) using a cert/key pair, **or** sits behind a TLS-terminating reverse
  proxy (`trust_proxy: true`).
- **Zero-config default:** on first run the controller **auto-generates a
  self-signed cert** into the config dir and enables HTTPS. A LAN operator gets
  an encrypted login out of the box (with a browser trust prompt, or a pinned
  cert). For a public hostname, drop in a CA/Let's-Encrypt cert or proxy.
- **Loopback exception:** when `web.host` is `127.0.0.1`, the credential never
  leaves the host, so TLS is optional there (browser-on-same-machine or an
  SSH-tunnel workflow). This keeps the simple single-box case friction-free.
- Cookies are issued `Secure` + `HttpOnly` + `SameSite=Strict`; bearer tokens
  are only accepted over TLS (or from loopback).

### 1.4 Fail-closed binding

- **Default `web.host` becomes `127.0.0.1`.** Local-only by default.
- The controller **refuses to start** if `web.host` is **non-loopback** and
  either `web.auth.enabled` is false **or** TLS is neither enabled nor delegated
  to a trusted proxy. You cannot accidentally expose an unauthenticated *or*
  cleartext-credential API to the LAN.
- Set a persistent random `secret_key` (generated on first run, stored in the
  config dir) for session-cookie signing.

### 1.5 CSRF + hardening

- CSRF token on all mutating (`POST/PUT/DELETE`) endpoints; the SPA sends it in
  a header.
- Keep the XSS fixes already landed (`scenes.js`, `dashboard.html`, `rules.html`)
  — Layer 1 makes those defense-in-depth rather than the only barrier.
- Rate-limit and constant-time-compare the login/token check to blunt
  brute-force and timing attacks.

Layer 1 alone means: *the only way to command any device is through an
authenticated controller, and its credentials never cross the wire in the
clear.* For serial-only and Art-Net deployments, this may be all that is needed.

## Authorization — Privilege Levels (Roles & Scopes)

Layer 1 answers *who are you*. This section answers *what may you do*. Because
the controller mediates every device, route, and automation action, it is the
natural **policy enforcement point**: each authenticated principal is granted a
role and an optional scope, and every controlling or mutating operation checks
that grant before the controller acts — including before it uses a device's
Layer-2 key. Authorization is therefore pure controller-side Python; devices
stay oblivious to users.

### A.1 Principals

A principal is any credential from §1.2 — a named **user** (password) or a named
**API token**. Every principal has a **role** (coarse capability tier) and an
optional **scope** that narrows which resources the role applies to.

### A.2 Roles (fixed, basic set)

| Role | May do |
|---|---|
| `admin` | Everything: manage users/tokens, pair devices & hold keys, edit security & config, create/edit/delete routes, automation, scenes, groups; control all devices. |
| `operator` | Control devices and start/stop routes & scenes **within scope**; may **not** manage security, pairing, users, or keys — and touches automation only as its scope allows. |
| `viewer` | Read-only: dashboards, device status, previews. No control, no mutation. |

Roles are deliberately few. Finer control comes from **scope**, not from
inventing new roles.

### A.3 Scope (least privilege)

An operator's (or token's) reach is narrowed by an optional scope:

- `devices: [id | group …]` — `control` applies only to these devices/groups;
  omitted means all. → *"access to control only one device."*
- `automation: none | run | manage` — `none` hides and forbids rules,
  sequences, and schedule triggers entirely; `run` may enable/disable/trigger
  existing ones; `manage` may create/edit/delete them. → *"not do / control
  scheduled operations"* is simply `automation: none`.
- `scenes: run | manage` — apply existing scenes vs. edit them.

Effective permission is the **intersection** of role and scope. The two examples
from the request fall straight out: an `operator` with
`devices: ["hall-strip"], automation: none` can drive exactly one strip and
cannot see or alter any schedule.

### A.4 Config

Extends the `web.auth` block from §1.2 (the single `password_hash` there is just
the minimal one-admin case):

```yaml
web:
  auth:
    users:
      - name: alice
        role: admin
        password_hash: "$argon2id$..."
      - name: bob                       # one device, no schedules
        role: operator
        password_hash: "$argon2id$..."
        scope:
          devices: ["hall-strip"]
          automation: none
    tokens:
      - name: home-assistant            # scoped automation token
        role: operator
        hash: "sha256:..."
        scope:
          devices: ["living-room", "kitchen"]
          automation: run
```

### A.5 Enforcement (endpoint → required permission)

Each API route declares the permission it needs; a role-aware gate (a
`@requires(action, resource)` decorator plus the `before_request` hook) rejects a
principal whose grant does not cover it with **403 Forbidden** — distinct from
Layer 1's **401 Unauthenticated**. Representative mapping onto today's routes:

| Endpoint (`web/app.py`) | Requires |
|---|---|
| `GET` dashboards / status / preview | `read` |
| `POST /api/sinks/<id>/control`, paint, fills | `control` on `<id>` (scope-checked) |
| `POST/PUT/DELETE /api/routes*`, `/api/groups*` | `control` on the target device(s), operator+ |
| `POST/PUT/DELETE /api/rules*`, `/api/sequences*`, schedule triggers | `automation: manage` |
| enable / disable / trigger a rule or sequence | `automation: run` |
| `POST /api/config/save`, pairing, user/token/key management | `admin` |

Crucially, device-scope is enforced where the controller **resolves a target
sink**, not just in the UI: `set_device_control` (`controller.py:491`), route
creation targeting a sink (`router.py`), and `SinkController` operations
(`sink_control.py`) each check the caller's `devices` scope before acting. A
hidden or out-of-scope endpoint returns 403 and is also omitted from the UI, so
`viewer`/scoped `operator` sessions never render controls they cannot use.

### A.6 Relationship to device keys (who decides vs. who is trusted)

Authorization lives **at the controller** — the single policy decision and
enforcement point. Layer-2 device keys authenticate the *controller* to the
*device*; devices have no concept of users. Per-user, per-device authorization is
thus applied by the controller *before* it reaches for a device key, and the only
way to bypass it is to hold a device PSK directly — which is an **admin-managed
secret never issued to `operator`/`viewer` principals**. (Optional hardening, see
Open Questions: mint a per-operator controller sub-capability so even a leaked
operator credential cannot be replayed against a device off-controller.)

## Layer 2 — Device Claim and Anti-Hijack (network devices)

Layer 1 protects the front door, but native network devices (ESP32 LTP sinks)
are independently reachable on the LAN. Layer 2 lets such a device refuse
privileged commands from anyone who does not hold its pre-shared key, and grants
**exclusive control** to one owner at a time.

### 2.1 Primitive: SipHash-2-4 keyed MAC

We need a keyed MAC, not encryption. **SipHash-2-4** (128-bit key, 64-bit tag)
is purpose-built for short-message authentication on constrained MCUs: a few
hundred bytes of flash, ~64 bytes transient RAM, sub-millisecond at 16 MHz. It
is used both for the handshake proof and for per-message control tags. Capable
devices (ESP32) may additionally advertise `hmac-sha256`; the controller picks
the strongest the device offers.

The pre-shared key (PSK) is a 128-bit secret stored per device (EEPROM/NVS) and
in the controller's keystore. **It is never transmitted.**

### 2.2 Capability negotiation

- **mDNS TXT** gains an `auth` key so a controller knows before it connects:
  `auth=none` | `auth=siphash` | `auth=hmac`. (Added in `_build_txt_properties`,
  `discovery.py:115`.)
- **`capability_response.device`** gains an `auth` object:
  ```json
  "auth": { "mode": "siphash", "required": true, "claimed": false }
  ```

### 2.3 Challenge-response claim (establishing a session)

New network `MessageType`s (`types.py:137`): `auth_challenge_request`,
`auth_challenge`, `claim`, `claim_response`, `claim_renew`, `release`. New
`ErrorCode`s: `UNAUTHORIZED=8`, `LEASE_HELD=9`.

```
Controller                                  Device (owner = none)
   |  auth_challenge_request { controller_id }     |
   |----------------------------------------------->|
   |  auth_challenge { nonce(16B), device_id }      |   nonce = random, single-use
   |<-----------------------------------------------|
   |                                                |
   |  proof = SipHash(PSK, nonce || device_id || controller_id)
   |  claim { controller_id, proof, lease_req: 30 } |
   |----------------------------------------------->|
   |                            verify proof; if ok:|
   |                            owner = controller_id
   |                            session_key = SipHash(PSK, nonce || "session")
   |                            token = random(16B); lease = 30s
   |  claim_response { token, lease_seconds,        |
   |                   device_proof }               |   device_proof authenticates the device too (mutual)
   |<-----------------------------------------------|
```

Properties:

- The PSK never crosses the wire; the nonce makes the proof non-replayable.
- `device_proof = SipHash(PSK, nonce || "device")` lets the controller verify it
  is talking to the **real** device and not an mDNS impostor (mutual auth,
  optional but recommended).
- A `session_key` is **derived**, never sent.
- **This satisfies the no-cleartext-credential goal on the device channel
  without any encryption.** The only long-term secret (the PSK) and the only
  session secret (`session_key`) are never transmitted — only one-way
  SipHash outputs of them are. The `token` in `claim_response` *is* sent in the
  clear, but it is a **session identifier, not a bearer credential**: on its own
  it authorizes nothing, because every privileged message must also carry a
  fresh `mac` computed with the never-transmitted `session_key` (§2.4). Capturing
  the token grants no ability to command the device. This is why the constrained
  device channel needs no TLS to meet the confidentiality boundary, while the web
  channel — which carries genuinely reusable bearer credentials — does (§1.3).

### 2.4 Exclusive lease + per-message control MAC

Once claimed, the device tracks `(owner_id, session_key, token, lease_expiry)`.

- **Privileged** messages (`control_set`, `stream_setup`, `stream_control`,
  `route_create`, `route_delete`, `subscribe`, reset/save) MUST carry:
  ```json
  { "type": "control_set", "seq": 42, "token": "…",
    "channel": "brightness", "value": 128,
    "mac": "SipHash(session_key, seq || canonical(body))" }
  ```
  The device recomputes the MAC and checks `seq` is monotonic (anti-replay). A
  wrong/absent MAC or token → `UNAUTHORIZED`. Because the MAC is keyed by the
  never-transmitted `session_key`, **even an eavesdropper cannot forge a control
  command or replay it with new parameters** — this is real control-plane
  anti-hijack without encrypting anything.
- A **second controller** that tries to claim while a lease is held gets
  `LEASE_HELD` (with time remaining). It cannot command the device.
- **Read-only** messages (`capability_request`, `control_get`, `pixel_read`) are
  gated by a per-device policy flag `read_open` (default: open, so discovery and
  dashboards keep working for observers).

### 2.5 Lease lifecycle (no permanent lockout)

- Lease is short (default 30 s), renewed by `claim_renew` (carries a fresh MAC).
  A crashed controller's lease simply expires and the device becomes claimable
  again — no device gets stuck owned by a dead controller.
- `release` frees the device immediately on graceful shutdown.
- Optional policy `preempt: true` lets a *different* controller that also holds
  the PSK take over after a grace period (for HA failover); default `false`.

### 2.6 Data plane (UDP pixels) — binding, not encryption

High-rate pixel frames are not individually authenticated (that would violate
the no-wire-privacy / low-cost goals). Instead:

- The device only accepts a UDP stream that was set up **inside the
  authenticated session** (`stream_setup` is a privileged, MAC'd message), and
  binds the accepted stream to the **owner's source IP:port**. Frames from other
  sources are dropped.
- Sequence numbers already exist in `DataPacket`; add simple monotonic-window
  sanity to shrink the spoofing window.
- Optional (behind a flag) lightweight rolling per-packet tag for operators who
  want it; off by default because it costs the frame path.

This stops off-path frame injection and casual cross-streaming. On-path frame
spoofing remains possible and is an accepted non-goal.

## Layer 3 — Serial Devices and the Bridge

A directly-wired AVR is point-to-point: the threat is not "another controller on
the UART," it is the **network bridge** (`ltp_serial_sink/sink.py`, which runs a
network `ControlServer` in front of the serial device). Therefore:

- **The bridge enforces Layer 2 on its network side.** It presents itself as a
  Level-2 network sink; the AVR behind it can stay Level 0 on its private UART.
  This gives full anti-hijack for serial devices **with zero AVR code change and
  zero AVR RAM cost** — the common case.
- **Optional AVR-side auth** for shared-bus/RS-485 or hostile-host scenarios:
  add serial opcodes in the config range and an advertised capability bit:
  - `CMD_AUTH_CHALLENGE=0x46` (device returns a nonce),
  - `CMD_AUTH_CLAIM=0x47` (host sends SipHash proof; device sets owner),
  - `CMD_SET_AUTH_KEY=0x48` / `CMD_CLEAR_AUTH=0x49` (key management, see below),
  - new `CAPS_AUTH=0x80` in the extended caps byte (`protocol.h:122`),
  - new `ERR_UNAUTHORIZED=0x0C`.
  When enabled, privileged serial commands (`CMD_SET_CONTROL`, `CMD_RESET`,
  `CMD_SAVE_CONFIG`, pixel writes) require a prior successful claim on that
  serial session. **Default off** to respect the 2 KB budget.

## Art-Net and Legacy Device Accommodation

Art-Net fixtures and legacy/third-party firmware cannot authenticate. They are
accommodated by **containment at the controller**, not by device auth:

1. **Classification.** Devices advertise `auth=none`; the controller treats them
   as Level 0 and, by policy, will only route to them if the operator has set
   `allow_insecure: true` on that device (default requires an explicit opt-in so
   an operator can't unknowingly command unauthenticated gear).
2. **Secure the mediator, not the fixture.** The `ArtNetSink` process is itself
   an LTP sink (`ltp_artnet/sink.py:75`). Run it at **Level 2** (or bind its LTP
   control to loopback) so only the authorized controller can feed it. The
   untrusted, unauthenticated part (broadcast Art-Net UDP, port 6454) is then
   confined to the downstream fixture segment.
3. **Egress allowlist.** Add a controller/`ArtNetSink` allowlist of permitted
   `{host, universe}` targets (`sender.py`). A source cannot cause Art-Net
   output to hosts/universes outside the configured set — this bounds the blast
   radius of the one inherently-open hop.
4. **Segmentation guidance (docs).** Recommend putting Art-Net fixtures on an
   isolated VLAN/subnet reachable only by the controller/`ArtNetSink`, and
   disabling the ESP32c3 telnet console (or gating it) in firmware builds.

The net effect: even though the fixtures are open, the only thing that can talk
to them is an authenticated controller driving a claimed mediator, and only to
allowlisted targets.

## Key Management and Pairing

- **Per-device PSKs** (recommended) — a compromised key affects one device. A
  shared **zone key** is allowed for groups of identical fixtures where per-device
  provisioning is impractical.
- **Controller keystore.** PSKs live in the controller config dir
  (`~/.config/ltp/keys.yaml`, `0600`), or referenced from the OS keyring where
  available. The keystore is protected by Layer 1.
- **Provisioning must not put the key on the wire in cleartext.** This is the
  one place the confidentiality boundary bites hardest — the PSK is the crown
  jewel — so the enrollment paths are chosen to never transmit it in the clear:
  - *Serial devices (default, simplest):* set the key over the USB console
    (`CMD_SET_AUTH_KEY`) — physical access is already full trust and the USB link
    is not "the wire" we are protecting. Persist to EEPROM (bump the firmware
    `CONFIG_VERSION`). **No secret ever touches the LAN.**
  - *Network devices (ESP32), key-agreement enrollment:* during a **pairing
    window** (first boot / button-hold), controller and device run an ephemeral
    **X25519 ECDH** exchange (only public points cross the wire) and both
    **derive** the PSK — the key itself is never transmitted. To stop a
    man-in-the-middle *during pairing*, bind the exchange to a short **pairing
    PIN** shown/known out-of-band (a PAKE such as SPAKE2, or an HKDF that mixes
    the PIN into the derived key). ESP32 has mbedTLS for X25519; this runs once,
    at pairing, on a capable device only. AVR is never network-provisioned.
  - *Wi-Fi co-provisioning:* if the PSK is delivered alongside Wi-Fi credentials
    via the existing preferences flow, that must happen over the same protected
    path used for the Wi-Fi passphrase (serial/USB or BLE provisioning), not
    cleartext TCP.
- **Pairing UX (web UI):** device list → "Pair" → for serial, controller
  generates a key and pushes it over USB; for network, controller runs the
  ECDH+PIN exchange in the pairing window; it then stores the derived key and
  marks the device Level 2. "Unpair"/"Rotate key" issue `CMD_CLEAR_AUTH` /
  re-provision. All of this happens *inside* the TLS-protected web session, so
  the operator's view of the key is protected too.

## Protocol Changes Summary

**Network (`libltp`), PROTOCOL_VERSION `0.1 → 0.2`:**
- New `MessageType`: `auth_challenge_request`, `auth_challenge`, `claim`,
  `claim_response`, `claim_renew`, `release`.
- New pairing `MessageType`: `pair_begin`, `pair_ecdh` (carries X25519 public
  points + PIN-bound confirmation), `pair_complete` — enrollment via derived
  key, never a transmitted key.
- New `ErrorCode`: `UNAUTHORIZED=8`, `LEASE_HELD=9`.
- `capability_response.device.auth` object; privileged messages gain optional
  `token`, `seq`, `mac`.
- mDNS TXT gains `auth=none|siphash|hmac`.

**Serial v2 (`ltp_serial_cli` + `LtpProtocol`), minor version bump (optional feature):**
- New opcodes `CMD_AUTH_CHALLENGE=0x46`, `CMD_AUTH_CLAIM=0x47`,
  `CMD_SET_AUTH_KEY=0x48`, `CMD_CLEAR_AUTH=0x49`.
- New `CAPS_AUTH=0x80` (extended caps byte); new `ERR_UNAUTHORIZED=0x0C`.
- Firmware `CONFIG_VERSION` bump to store the PSK.

**Controller/web (Python only):**
- `web.auth` config + `before_request` gate + CSRF + loopback default +
  fail-closed bind + auto-TLS.
- **Authorization:** `users`/`tokens` with `role` (admin/operator/viewer) and
  `scope` (`devices`, `automation`, `scenes`); `@requires(action, resource)`
  enforcement returning 403; device-scope checks in `set_device_control`,
  route creation, and `SinkController`.
- Per-device policy: `min_level`, `allow_insecure`, `read_open`, `preempt`.
- Keystore + pairing endpoints; router refuses to route to devices below
  `min_level` unless `allow_insecure`.

## AVR / Embedded Feasibility

| Cost | SipHash-2-4 on ATmega328P |
|---|---|
| Flash | ~300–500 B |
| RAM (transient) | ~64 B during a MAC; **zero on the frame path** |
| Time | sub-ms per short message at 16 MHz |
| Persistent | 16 B PSK in EEPROM |

The handshake runs once per session; the per-message MAC runs only on control
commands (low rate — a brightness tweak, a config write), never per frame. On the
2 KB-RAM 328P the feature is **off by default**; the serial bridge provides
anti-hijack for those devices without touching the MCU, and the 328P is only
ever key-provisioned over USB (no ECDH on-chip). ESP32 has ample headroom →
Level 2 on by default, `hmac-sha256` offered, and mbedTLS supplies the X25519
used for cleartext-free network pairing (§Key Management).

## Backward Compatibility and Migration

- Everything is negotiated. `auth=none` (Level 0) is exactly today's behavior;
  unmodified devices keep working.
- A Level-2 device with **no key set** behaves as Level 0 until paired.
- Old controllers talking to new devices: if the device requires auth and the
  controller can't provide it, the controller sees `UNAUTHORIZED` and surfaces a
  "device is paired — add its key" prompt.
- The one **behavior change** is the controller's own defaults (loopback bind +
  auth-required-to-expose + auto-generated self-signed TLS when off-loopback).
  This is intentional and the highest-value change; document it prominently in
  the upgrade notes (including the first-run cert-trust step for browsers).

## Implementation Order

1. **Phase 1 — Layer 1 + Authorization (web/API auth, roles & scopes, auto-TLS,
   loopback default, fail-closed bind).** Pure Python, no protocol change, closes
   the widest hole, keeps login/token credentials off the cleartext wire, and
   delivers the admin/operator/viewer + per-device/automation scoping. Ship
   first. (Roles ride on the same gate as auth, so they land together.)
2. **Phase 2 — Network Layer 2 on ESP32** (SipHash, capability negotiation,
   claim/lease, per-message control MAC, data-plane binding). Disable/gate the
   ESP32c3 telnet backdoor here.
3. **Phase 3 — Serial bridge enforcement** (bridge presents Level 2 on the
   network side) + **optional** AVR-side serial auth opcodes.
4. **Phase 4 — Art-Net containment** (egress allowlist, `allow_insecure` policy,
   segmentation docs) + key-management/pairing UX in the web UI.

Phases 1 and 4 (policy) deliver most of the practical protection; Phases 2–3 add
the cryptographic anti-hijack for capable devices.

## Residual Risks and Non-Goals (stated plainly)

- **Operational data is visible (accepted).** Pixel frames and control *values*
  are cleartext on the data path; a sniffer can see what is showing and what a
  brightness value is. This is the explicit non-goal — distinct from credentials,
  which are protected.
- **Credentials are not cleartext (goal met).** Passwords, tokens, and cookies
  ride TLS on the web tier; device keys are never transmitted (challenge-response
  + derived session key; ECDH/PIN or USB enrollment). No access-control secret
  crosses the wire in the clear.
- **On-path attacker.** The per-message control MAC prevents an eavesdropper from
  forging or replaying **control** commands even without encryption. But an
  attacker with a true MITM position can still drop/observe traffic, and can spoof
  **UDP pixel frames** (data-plane) from the owner's address. Defending the *data*
  path against this requires transport encryption (out of scope). The *credential*
  and *control* paths are already defended.
- **Controller compromise** defeats everything below it (it holds the keys) —
  which is why Layer 1 is the priority.
- **DoS.** Auth adds a cheap pre-check but does not stop UDP flooding; pair with
  rate-limiting (complementary, not covered here).
- **Physical/serial access** = full device trust, by design.

## Open Questions

1. **Per-device vs zone keys** as the default? (Proposal: per-device, with zone
   keys allowed.)
2. **Are read-only ops gated or open** by default? (Proposal: open, via
   `read_open`, to preserve dashboards/discovery for observers.)
3. **Lease preemption** for HA/failover — allow a second PSK-holding controller
   to take over, or require explicit `release`? (Proposal: off by default.)
4. **Optional data-plane per-packet MAC** — worth the frame-path cost for anyone?
5. **Key rotation** UX and cadence.
6. Should the controller advertise its own auth requirement so **sources**
   (which push frames) can likewise be gated, symmetric to sinks?
7. **TLS cert management:** self-signed + browser trust prompt is friction; is a
   local-CA helper (generate a CA the operator installs once) or ACME/Let's
   Encrypt worth it for the common single-box LAN case? Should loopback
   deployments also default to TLS for uniformity?
8. **Network pairing MITM:** is the pairing-PIN PAKE (SPAKE2) warranted, or is a
   plain ECDH in a physically-supervised pairing window acceptable for a home
   LAN? (Proposal: PIN-bound by default, plain ECDH as an opt-out.)
9. **Per-operator device sub-capabilities:** should the controller derive a
   scoped, per-principal capability from a device PSK so a leaked operator
   credential cannot be replayed against a device outside the controller? Adds
   complexity; likely beyond "basic," but noted.
10. **Custom roles / per-scene permissions:** the three fixed roles + scope cover
    the stated cases; is a user-defined role or per-scene ACL ever needed, or
    does that cross into the enterprise-RBAC non-goal?
11. **Group vs. device scoping precedence:** when a principal is scoped to a
    group but a device leaves that group, does access follow the group or the
    device id? (Proposal: resolve groups to member ids at check time.)
