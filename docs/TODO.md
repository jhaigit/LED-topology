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

**Status**: DONE

**Goal**: Support controls that trigger one-time operations rather than
setting persistent values.

**Examples**:
- `save` - Save current config to EEPROM/NVS
- `reboot` - Restart device
- `calibrate` - Run calibration routine

**Implementation**:
- Added CTRL_TYPE_ACTION (0x06) to protocol
- Added CTRL_FLAG_ACTION flag for additional flexibility
- Firmware: CTRL_ID_SAVE_CONFIG (0xF0) and CTRL_ID_REBOOT (0xF1)
- All three firmwares (serial_v2, octo_v2, apa102_strip) handle save/reboot
- Platform-specific reboot: AVR watchdog, Teensy SCB_AIRCR, ESP32 restart
- Python sink registers ActionControl and handles trigger without storing value
- UI renders action controls as buttons with feedback (Done!/Error)
- Reboot action triggers page reload after 2 seconds

---

### 4. Control Descriptions and Units

**Status**: DONE

**Goal**: Controls export a description string that can include units,
ranges, or usage hints.

**Implementation**:
- Firmware: Added `description` field to ControlDef struct in all firmwares
- Protocol: INFO_CONTROLS format extended with description(null-term) after name
- Python: DeviceControl dataclass includes description, passed through to UI
- UI: Descriptions shown as tooltips on control names (dotted underline, help cursor)
- Fixed buffer overflow (increased response buffer, used terse descriptions)

**Control descriptions** (terse format to save RAM):
- brightness: "0-255"
- gamma: "x10, 10=1.0"
- idle_timeout: "secs, 0=off"
- auto_show: "after pixel cmds"
- frame_ack: "ack frames"
- status_interval: "ms, 0=off"
- local_mode: "0=off, 255=cycle"
- cycle_time: "ms"
- save: "save to EEPROM"
- reboot: "restart device"

---

### 5. Robust Signal Handling for mDNS Cleanup

**Status**: DONE

**Goal**: Ensure mDNS service registrations are cleaned up even when the
sink is terminated abnormally (SIGTERM, SIGKILL, crash).

**Problem**: When the sink was killed before cleanup ran, avahi-publish-service
processes became orphaned zombies, leaving stale mDNS registrations that caused
discovery issues on restart.

**Implementation**:
- Added SIGTERM/SIGINT handlers to sink CLI for graceful shutdown
- Added atexit handler to kill orphaned avahi-publish-service processes
- Use PR_SET_PDEATHSIG so avahi subprocess dies if parent dies unexpectedly
- Start avahi in new process group for clean termination
- Track all avahi processes globally for cleanup on module exit
- Controller auto-refetches capabilities when device port changes on discovery

---

## Implementation Order

1. ~~**Build info display**~~ - DONE
2. ~~**Control flags**~~ - DONE
3. ~~**Action controls**~~ - DONE
4. ~~**Control descriptions**~~ - DONE
5. ~~**Signal handling**~~ - DONE

After these cleanups, revisit control set flakiness with cleaner codebase.

---

## Notes

- Protocol changes should maintain backward compatibility where possible
  (description can be optional, flags default to 0)
- ESP32 JSON protocol may differ from serial binary protocol - document
  which features apply to which
- Test with both serial sinks and ESP32 ring after changes
