# LTP Development TODO

## Known Issues

### ESP32 Ring Hang - Most Likely Power Related

**Status**: Documented, not yet resolved

The ESP32 ring controller experiences intermittent hangs where:
- LED pattern freezes
- Ping fails (WiFi stack unresponsive)
- Tends to occur when all/many LEDs are on (bright states)

**Root cause**: Most likely electrical/power issues. The 202 APA102 LEDs at
full brightness draw ~12A, which can cause voltage sag, brownout, or ground
bounce that crashes the ESP32's WiFi radio.

**See**: `arduino/ltp_esp32_ring/HANG_INVESTIGATION.md` for full analysis.

**Next steps**: Hardware debugging (measure voltage under load, test with
reduced brightness, check power supply rating).

---

### Control Set Flakiness

**Status**: Outstanding, to be addressed after protocol cleanup

Setting controls via the web UI fails ~50% of the time. The error chain shows
empty exception messages and "no response" errors. Retry usually works.

**Hypotheses**: Health check TCP connection interference, pool connection
going half-dead, or ESP32 sending response to wrong client.

**See**: `docs/control-set-failure-diagnosis.md` for detailed analysis.

---

## Protocol & UI Cleanup (Priority)

Before addressing control set flakiness, these cleanups should be done:

### 1. Build Information Display

**Status**: DONE

**Goal**: Make build info (git commit, build date, firmware name) available
and displayable on the controller web UI.

**Implementation**:
- Added `INFO_BUILD` (0x07) constant and `BuildInfo` dataclass to protocol
- Added `get_build_info()` method to `LtpDevice` for serial protocol
- V2Renderer queries build info on device connection
- Sink capability response includes `firmware_name`, `git_commit`, `build_date`
- Web UI displays build info on sinks and sources pages
- Shows abbreviated commit hash with date, full details in tooltip

---

### 2. Control Classification (Low-Level vs High-Level)

**Status**: DONE

**Goal**: Clean way to distinguish hardware controls (exported from embedded
firmware) from higher-level controls (managed by Python sink/controller).

**Implementation**:
- Added flags byte to INFO_CONTROLS protocol format
- Added CTRL_FLAG_* constants: HARDWARE, READONLY, VOLATILE, ACTION, HIDDEN
- All firmware controls marked with CTRL_FLAG_HARDWARE
- auto_show, frame_ack, status_interval also marked VOLATILE
- Python DeviceControl has is_hardware, is_volatile, is_action, readonly props
- INFO_CONTROLS also now includes current value (avoids extra query)
- [ ] UI can filter/group controls by category

---

### 3. Action-Type Controls (One-Time Operations)

**Status**: Protocol ready, UI pending

**Goal**: Support controls that trigger one-time operations rather than
setting persistent values.

**Examples**:
- `save` - Save current config to EEPROM/NVS
- `reboot` - Restart device
- `calibrate` - Run calibration routine

**Implementation so far**:
- Added CTRL_TYPE_ACTION (0x06) to protocol
- Added CTRL_FLAG_ACTION flag for additional flexibility
- Python CTRL_TYPE_NAMES includes "action" type

**Remaining tasks**:
- [ ] Add action controls to firmware (save, reboot)
- [ ] Update UI to render actions as buttons instead of inputs

---

### 4. Control Descriptions and Units

**Goal**: Controls export a description string that can include units,
ranges, or usage hints.

**Current state**: Controls have only `name` (short identifier). Users don't
know what "gamma" means or that it's stored as value*10.

**Proposed addition to INFO_CONTROLS**:

```cpp
// Current format per control:
//   id(1) + type(1) + min(2) + max(2) + name(null-term)

// New format:
//   id(1) + type(1) + flags(1) + min(2) + max(2) + name(null-term) + description(null-term)
```

**Example descriptions**:
- brightness: "Global LED brightness (0-255)"
- gamma: "Gamma correction factor (1.0-3.0, stored as x10)"
- cycle_time: "Local mode cycle interval in seconds"
- idle_timeout: "Seconds until local mode activates (0=never)"

**Tasks**:
- [ ] Extend INFO_CONTROLS format with description field
- [ ] Update firmware to include descriptions
- [ ] Update Python parser
- [ ] Display descriptions in UI (tooltip or help text)

---

## Implementation Order

1. **Build info display** - Low risk, immediate user value
2. **Control flags** - Required foundation for #3 and #4
3. **Action controls** - Enables save/reboot buttons
4. **Control descriptions** - Polish, can be added incrementally

After these cleanups, revisit control set flakiness with cleaner codebase.

---

## Notes

- Protocol changes should maintain backward compatibility where possible
  (description can be optional, flags default to 0)
- ESP32 JSON protocol may differ from serial binary protocol - document
  which features apply to which
- Test with both serial sinks and ESP32 ring after changes
