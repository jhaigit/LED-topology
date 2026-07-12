# ESP32-C3 Layer 2 auth — hardware test plan

The protocol interop (SipHash vectors, MAC canonical form, claim/device
proofs, session-key derivation) is verified on host against the Python
implementation — see the cross-checks in the Phase 2 commit. This document
covers what must be confirmed on the actual board, which the CI/dev VM
cannot do.

## Build

```
cd arduino/ltp_esp32c3_oled
make upload PORT=/dev/ttyUSB0
```

Telnet is compiled out by default. To keep it for debugging:
`make upload EXTRA_FLAGS=-DLTP_ENABLE_TELNET=1` (or add to the Makefile flags).

Confirm at boot the USB console prints `Telnet: disabled (LTP_ENABLE_TELNET=0)`.

## Provision a key (USB, cleartext-free)

```
openssl rand -hex 16          # -> e.g. 00112233445566778899aabbccddeeff
# in the device USB console:
auth-key 00112233445566778899aabbccddeeff
save
reboot
```

`info` should now show `Auth: Level 2 (siphash)`. Add the SAME key to the
controller keystore under this device's UUID (shown by `info`):

```
# ~/.config/ltp/keys.yaml
devices:
  <device-uuid>: "00112233445566778899aabbccddeeff"
```

## Pairing (X25519 + PIN)

An alternative to typing a raw `auth-key`: derive the Layer 2 PSK over the
network with an ephemeral X25519 ECDH, bound by a PIN shown on the OLED. The
key never crosses the wire. See `device_pairing.h` (the C/mbedTLS twin of
`src/libltp/pairing.py`).

```
# in the device USB console:
pair
```

The console prints an 8-digit PIN and the OLED shows `PAIR` with the PIN in a
large font. Then in the controller web UI, click **Pair (ECDH)** on this sink
and type the PIN.

Expected: the exchange completes, the device persists the derived PSK, enables
Level 2 auth automatically (`saveConfig`), and re-arms the auth guard — the
sink's `auth_state` becomes `owned` and the dashboard shows `keyed`. The device
console logs `Paired: Layer 2 key installed, auth enabled`.

Notes:
- The pairing window is **120 s** and **one-shot**: a single `pair_confirm`
  (right or wrong PIN) closes it. A wrong PIN fails key confirmation (`error`
  code 11) and you must re-run `pair`. `pair_begin` before `pair` (or after the
  window expires) returns `error` code 10 (not in pairing mode).
- Pairing runs *before* the auth guard, so it works while the device is still
  unkeyed (that is how the first key is installed). Re-running `pair` on an
  already-keyed device re-pairs and overwrites the PSK.
- The derivation is byte-identical to `pairing.py` and verified against the
  pinned interop vectors on the host (byte order, clamping, HKDF/HMAC layout);
  it has not yet been exercised on real ESP32-C3 mbedTLS. Confirm the pinned
  vectors on-chip once (temporary Serial print of the derived pub/shared for the
  fixed test keys) if you want end-to-end certainty.

## Checks

1. **Discovery advertises auth.** `avahi-browse -r _ltp-sink._tcp` shows
   `auth=siphash` in the TXT record.
2. **Controller claims on connect.** Start the controller; the sink's
   `auth_state` should reach `owned` (dashboard) and the device console logs
   a successful claim.
3. **Frames render.** Route a source to the OLED — pixels display as before.
   The claim/MAC path does not touch the per-frame data plane.
4. **Unauthenticated control rejected.** From another host, connect a raw
   client and send `{"type":"control_set","values":{"brightness":10}}` with
   no auth envelope → `error` code 8 (UNAUTHORIZED); brightness unchanged.
5. **Hijack refused.** Point a second controller (same key) at the device
   while the first holds the lease → `LEASE_HELD`. It cannot command the
   device.
6. **Lease recovery.** Kill the owning controller without a clean release;
   after the lease (~30s) another controller can claim. A clean shutdown
   frees it immediately (release).
7. **Data-plane binding.** With a stream active, send a UDP pixel frame from
   a non-owner IP → dropped (device console: "unbound source"); frames from
   the owner still render.
8. **Wrong key.** Put a different key in the controller keystore → claim
   fails with UNAUTHORIZED; `auth_state` shows `error`.
9. **Backward compat.** `auth-off; save; reboot` → device advertises
   `auth=none` and behaves exactly as pre-Phase-2 (open, unauthenticated).
10. **NVS migration.** A board flashed over an existing v1 config keeps its
    WiFi/name settings and comes up Level 0 (auth absent = disabled) until a
    key is set.

## Notes

- ArduinoJson parse/serialize on-chip matches the host test (same library
  version), but confirm control values with fractional parts (e.g. a gamma
  control) still verify — the canonical form renders integral floats as
  integers on both sides.
- `esp_random()` seeds nonces/tokens; confirm two consecutive claims produce
  different nonces (device console logs, or Wireshark).
