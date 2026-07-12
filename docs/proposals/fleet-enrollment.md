# Proposal: Fleet Enrollment & Remote Key Provisioning (Phase 5)

**Status:** draft — awaiting sign-off on the enrollment-trust model (§5) before implementation.

## 1. Problem

Enabling Layer 2 auth for a *serial* device today requires the same 16-byte
PSK to be placed in **two** locations:

1. the controller keystore (`~/.config/ltp/keys.yaml`) — so the controller can
   claim the device, and
2. the serial-sink fleet's config (`serial-fleet.yaml`, `auth_psk:` on the
   device override) — so the bridge *requires* the claim and enforces the MAC.

The controller's Phase 4a "Set Key" UI handles (1), but (2) is a hand-edit of a
YAML file. When the fleet runs on a **different machine** than the controller
(the common case — fleets live next to their USB devices), this means SSHing to
the fleet host and editing a file per device. That is error-prone, doesn't
scale, and is exactly the friction Phase 4 set out to remove for network
devices.

Serial/AVR devices cannot do the Phase 4b X25519 pairing themselves (no crypto
capacity; the bridge enforces auth on their behalf). So the elegant fix is to
push the trust boundary up one level: make the **fleet** a trusted peer of the
controller, and provision device keys to it over an authenticated, encrypted
control channel — no file editing, works across machines.

## 2. Goals / Non-goals

**Goals**
- Enable/rotate/disable Layer 2 auth on a serial device from the controller UI,
  with the fleet on a *different* host. No hand-edited YAML.
- Establish controller↔fleet trust once per fleet (not per device), with a
  clear, MITM-resistant enrollment step.
- Reuse the existing crypto (`libltp/pairing.py`, SipHash MAC) rather than
  introduce a new secure-channel stack.
- Fail closed: an un-enrolled or untrusted fleet cannot be provisioned, and a
  fleet only accepts provisioning from its one trusted controller.

**Non-goals (this phase)**
- Replacing the per-device PSK model — the device-side auth is unchanged; this
  only automates *distributing* the PSK to the fleet.
- Managing network (ESP32) devices — those already have Phase 4b pairing.
- A general fleet orchestration/telemetry system (see §10 for what the channel
  *could* grow into later).

## 3. Architecture: the fleet as a trusted peer

```
                 mDNS advert (identity + fingerprint)
   ┌───────────┐  ───────────────────────────────►  ┌──────────────────┐
   │ Controller│                                     │  Serial Fleet     │
   │  (admin   │   1. enroll (authenticated X25519,  │  (bridge, remote) │
   │   UI +    │      bound by fleet fingerprint)    │                   │
   │  keystore)│  ◄════════ mutual trust ═══════════►│  identity keypair │
   │           │                                     │  + control endpoint│
   │           │   2. provision(device_id, psk)      │                   │
   │           │      MAC'd + encrypted on channel   │  ─► sets auth on   │
   └───────────┘  ═════════════════════════════════► │     device, re-    │
                                                      │     adopts it      │
                                                      └──────────────────┘
```

Two new pieces:
- **Fleet identity + inbound control endpoint.** The fleet is advertise-only
  today; it gains a static identity keypair and a small authenticated endpoint
  that accepts provisioning commands.
- **Controller "Fleets" management surface.** Discover fleets, verify
  fingerprints, approve/revoke trust; then per-serial-device provisioning
  becomes one click (extends the Phase 4a keystore UI).

## 4. Fleet identity

- On first run the fleet generates a static **X25519** keypair, persists it
  `0600` at `~/.config/ltp/fleet-identity` (honoring `$XDG_CONFIG_HOME`).
- It logs its **fingerprint** (e.g. `SHA-256(pubkey)` truncated to a short
  base32 group, like `AB12-CD34-EF56`) to its console/journal at startup — this
  is the out-of-band value an admin compares during enrollment.
- It advertises `fleet_pub` (hex) and `fpr` in a new `_ltp-fleet._tcp` mDNS
  service (or TXT keys on the existing advert), alongside the host/port of its
  control endpoint.

## 5. Enrollment & trust bootstrap — **the decision to sign off**

The controller→fleet direction is protected by the admin verifying the fleet
fingerprint in the UI (defeats a MITM impersonating the fleet). The **fleet→
controller** direction — "which controller may provision me?" — is the crux: if
any controller can push a PSK, an attacker provisions a key they know and hijacks
the device. Three options, increasing robustness:

| Option | Fleet authorizes controller by… | Out-of-band steps | Weakness |
|---|---|---|---|
| **A. Pure TOFU** | trusting the first controller to enroll | none | attacker who races enrollment wins |
| **B. Enroll code** (recommended) | a one-time code generated in the controller UI, entered once on the fleet: `ltp-serial-sink enroll --controller <addr> --code <code>` | one CLI command per fleet, once | code must be transported to the fleet host (SSH once, or console) |
| **C. Pinned controller fpr** | a one-line `trusted_controller: <fpr>` in fleet config | one config line per fleet, once | still a (tiny, one-time) config edit |

All three are **per-fleet, once** — none is per-device, so all three kill the
current per-device YAML friction. **Recommendation: Option B** (the
`kubeadm join --token` model): the admin clicks "Enroll fleet" in the UI, gets a
short-lived code, runs one command on the fleet host. It needs no persistent
config edits and the code's short TTL bounds the exposure. Option A is
acceptable only on a fully trusted LAN; Option C trades the code for a static
config line.

Enrollment handshake (Option B): controller and fleet run an **authenticated
X25519 exchange (reuse `pairing.py`)**, where the enroll code is mixed into the
KDF exactly as the PIN is in device pairing, and the admin-verified fleet
fingerprint binds the controller→fleet direction. On success both sides pin the
other's static public key and derive a long-lived channel secret. This reuses
Phase 4b wholesale — the fleet fingerprint and enroll code are the two binding
factors.

## 6. Secure channel

Post-enrollment, controller↔fleet messages are carried over a channel
authenticated and encrypted with a key derived (HKDF) from the pinned identities
+ channel secret. Per-message integrity uses the existing SipHash MAC with a
monotonic counter (reuse the `deviceauth` machinery), and the payload — which
carries a PSK — is encrypted (e.g. XChaCha20-Poly1305 via `cryptography`, or an
HKDF-derived stream + MAC) so the key never appears in cleartext on the wire.
Transport can be a small TLS-wrapped HTTP endpoint on the fleet or a raw
newline-JSON socket like the existing LTP control channel; **decision deferred to
implementation** — either works once the channel crypto is in place.

## 7. Provisioning flow (device PSK push)

Once a fleet is trusted, "Set Key" / "Rotate" / "Unpair" for a *serial* device
in the keystore UI:
1. Controller generates (or rotates) the device PSK, stores it in its keystore.
2. Controller sends `fleet_provision {device_match, device_id, psk}` over the
   trusted channel.
3. Fleet applies it: updates the running device's `auth_psk`, persists it to its
   own fleet state, enables auth, and re-adopts the device so the change takes
   effect. Returns success.
4. Unpair sends `fleet_provision {…, psk: null}` → fleet disables auth and
   re-adopts.

Result: identical UX to the Phase 4b network-device flow, but for serial
devices behind a remote bridge — one click, no YAML.

## 8. Revocation & re-key

- **Revoke a fleet** from the UI → controller drops the pinned fleet key and
  the channel secret; the fleet is untrusted again (and should drop the pinned
  controller key on its side, e.g. via `ltp-serial-sink enroll --reset`).
- **Rotate the channel** → re-run enrollment.
- A fleet that loses its identity file (disk wipe) re-generates and appears as a
  new untrusted fleet with a new fingerprint — the admin must re-enroll, which is
  the correct, visible behavior.

## 9. Controller UI

- New **Fleets** tab: discovered fleets with fingerprint + trust status
  (untrusted / enrolling / trusted / error), an "Enroll" action that shows the
  one-time code, and "Revoke".
- The keystore UI's per-serial-device provisioning becomes live (pushes to the
  device's owning fleet) instead of showing "edit YAML" guidance. Detect the
  owning fleet from the device's advertisement.

## 10. Future (explicitly out of scope now)

Once a trusted channel exists, it is the natural place for: fleet-reported
device **inventory** (what's plugged in where), **health/telemetry**, remote
fleet **config** (rescan interval, includes), and pushing **firmware update**
notices. Noting so the message set is designed to be extensible, not built now.

## 11. Threat model summary

- **Passive eavesdropper:** sees fleet public key + enrollment public points
  only; channel payloads (incl. PSKs) are encrypted → no key leak.
- **Active MITM at enrollment:** must defeat *both* the admin-verified fleet
  fingerprint and (Option B) the one-time enroll code → not feasible without
  compromising the out-of-band step.
- **Rogue controller on the LAN:** cannot provision — the fleet only accepts its
  one pinned controller identity.
- **Compromised fleet host:** already has physical/root access to the USB
  devices; auth cannot defend below that line (documented assumption, same as
  Phase 2's "USB access = trust").
- **Residual:** the enroll code (Option B) is a bearer secret in transit for its
  TTL; keep the TTL short and single-use.

## 12. Phasing

1. **P5.1 — Fleet identity + enrollment.** Identity keypair, mDNS advert,
   enroll handshake (reusing `pairing.py`), pinning on both sides, `enroll`
   CLI, controller "Fleets" tab (discover/verify/trust/revoke). Interop-locked
   with pinned vectors + tests; no device provisioning yet.
2. **P5.2 — Provisioning channel + push.** Encrypted+MAC'd channel, the
   `fleet_provision` message, fleet applies/persists/re-adopts, keystore UI
   wired to push. End-to-end serial-device set/rotate/unpair from the UI.
3. **P5.3 (optional) — inventory/health** over the same channel.

## 13. Open decisions (need sign-off)

1. **Enrollment-trust model:** A / B / C from §5. *Recommendation: B.*
2. **Channel transport:** TLS-wrapped HTTP endpoint on the fleet vs. raw
   newline-JSON socket. *Recommendation: reuse the LTP newline-JSON socket
   pattern the fleet's sinks already speak, with the channel crypto on top.*
3. **Payload encryption primitive:** XChaCha20-Poly1305 (via `cryptography`) vs.
   an HKDF-stream + SipHash-MAC. *Recommendation: XChaCha20-Poly1305 — the
   controller already depends on `cryptography`; simpler and standard.* (The AVR
   is never in this path, so the constrained-device constraint doesn't apply.)
