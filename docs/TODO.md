# LTP Development TODO

## Known Issues

### ESP32 Ring Hang - Power Related

**Status**: Resolved

The ESP32 ring controller experienced intermittent hangs (LED freeze, WiFi
unresponsive) under high brightness. Root cause was electrical — 202 APA102
LEDs at full brightness draw ~12A, causing voltage sag/brownout.

**See**: `arduino/ltp_esp32_ring/HANG_INVESTIGATION.md` for full analysis.

---

### Control Set Flakiness

**Status**: Mostly resolved, low priority

The ~50% failure rate for control sets via the web UI has been largely fixed
through communication and connection pool improvements. A remaining edge case
exists where commands sent in rapid succession can cause one to be lost, but
this needs more testing to confirm. Low priority.

**See**: `docs/control-set-failure-diagnosis.md` for original analysis.

---

### Serial Sink Reconnect Failure

**Status**: DONE

The serial sink (v2_renderer) failed to reconnect after a communication error
because error paths set `_connected = False` without closing the old LtpDevice.
The orphaned reader thread held the serial port open and consumed incoming
bytes, preventing all future reconnects.

**Fix**: Added `_close_device()` helper that properly stops the reader thread
and closes the serial port. Called from all error paths and before creating a
new device in `open()`.

---

## Protocol & UI Cleanup

All 6 protocol cleanup items are complete.

### 1. Build Information Display — DONE
### 2. Control Classification (Low-Level vs High-Level) — DONE

- [ ] UI can filter/group controls by category (low priority)

### 3. Action-Type Controls — DONE
### 4. Control Descriptions and Units — DONE
### 5. Robust Signal Handling for mDNS Cleanup — DONE
### 6. mDNS Discovery IPv4/IPv6 Fixes — DONE

---

## Sequences & Scheduled Automation

### Sequences and Timed Execution

**Status**: DONE

Ordered lists of actions with delay and random jitter between steps.
Supports loop mode and start/stop/pause/resume lifecycle. Full CRUD API
and dashboard UI with step editor modal.

### Schedule Triggers for Rules

**Status**: DONE

Rules can now use cron-based time triggers with a time picker UI,
day-of-week checkboxes, and optional jitter. Rules can trigger
Start/Stop Sequence actions.

### Sequence UI Improvements

**Status**: DONE

- Sink/source/route/sequence targets use dropdowns instead of typed names
- Fill Solid uses a color picker instead of typed hex
- Set Control shows a dropdown of available controls fetched from the sink
- Enum controls (e.g. local_mode) show labeled option dropdowns
- Boolean/number controls get appropriate input types
- Delay and jitter fields show placeholder text when empty

---

## Future Considerations

### Unify Rule and Sequence Action Sets

Rules and sequences should support the same set of actions wherever that
makes sense. Currently some action types are only available in one context.

### Absolute Wall-Clock Times in Sequences

Sequence steps currently only support relative delays from the previous step.
If the schedule trigger + sequence combination proves inadequate for certain
use cases, consider adding support for absolute wall-clock times within
sequence steps.

---

## Notes

- Protocol changes should maintain backward compatibility where possible
  (description can be optional, flags default to 0)
- ESP32 JSON protocol may differ from serial binary protocol - document
  which features apply to which
- Test with both serial sinks and ESP32 ring after changes
