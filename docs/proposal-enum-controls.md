# Proposal: Enum Controls with Named Values in the Embedded Protocol

## Problem

1. **`local_mode` renders as a slider on the dashboard** because it arrives as `CTRL_TYPE_UINT8` with min=0, max=255. The dashboard's `renderInteractiveControls()` sees a number with min/max and renders `<input type="range">`. But `local_mode` is discrete: 0=blank, 1=cylon, 2=rainbow, 3=fire, 4=sparkle, 5=chase, 255=cycle. A slider is wrong here.

2. **The sinks page renders it as a number input** because `sources.html`/`sinks.html` always use `<input type="number">` for number controls. This is better but still unlabeled — the user must know that 3 means "fire".

3. **No way for firmware to declare enum values.** The protocol has `CTRL_TYPE_ENUM = 0x10` reserved but unused. There is no wire format for transmitting option labels from firmware to the host.

## Proposed Solution

### Layer 1: Firmware Protocol Extension

Add `CTRL_TYPE_ENUM` (0x10) support to the binary protocol. The wire format for an enum control appends option labels after the name and description strings:

```
Existing fields (same for all types):
  id(1), type(1), flags(1), min(2), max(2), value(1), name(\0), description(\0)

New for CTRL_TYPE_ENUM — append after description:
  num_options(1)
  For each option:
    option_label(\0)    // null-terminated string
```

The numeric value maps to options by index (option 0 = value 0, option 1 = value 1, etc.). Special values outside the option count (like 255 for "cycle") are declared by setting max to the special value and including it as the last option with its own label.

**Example for `local_mode`:**
```c
// Control definition becomes:
{ CTRL_ID_LOCAL_MODE, CTRL_TYPE_ENUM, CTRL_FLAG_HARDWARE, 0, 255, cn6, cd6 }

// Enum option strings (PROGMEM):
static const char em6_0[] PROGMEM = "Off";
static const char em6_1[] PROGMEM = "Cylon";
static const char em6_2[] PROGMEM = "Rainbow";
static const char em6_3[] PROGMEM = "Fire";
static const char em6_4[] PROGMEM = "Sparkle";
static const char em6_5[] PROGMEM = "Chase";
static const char em6_6[] PROGMEM = "Cycle";

// Enum definition struct:
struct EnumDef {
    uint8_t num_options;
    uint8_t values[8];           // actual numeric values
    const char* labels[8];       // PROGMEM pointers
};

static const EnumDef localModeEnum PROGMEM = {
    7,
    { 0, 1, 2, 3, 4, 5, 255 },
    { em6_0, em6_1, em6_2, em6_3, em6_4, em6_5, em6_6 }
};
```

**Wire format for enum options:**
```
num_options(1)
For each option:
    value(1)            // the actual numeric value for this option
    label(\0)           // null-terminated label string
```

This costs ~1 byte per option for the value plus the label strings. For `local_mode` with 7 options, that's about 7 + 3+6+7+5+8+6+6 = ~48 bytes additional.

**RAM impact:** All strings are in PROGMEM, so zero additional RAM. The `EnumDef` structs can also be PROGMEM. The streaming send approach means no buffer is needed.

### Layer 2: ControlDef Struct Change

Add an optional pointer to an `EnumDef`:

```c
struct ControlDef {
    uint8_t id;
    uint8_t type;
    uint8_t flags;
    int16_t minVal;
    int16_t maxVal;
    const char* name;
    const char* description;
    const EnumDef* enumDef;   // NULL for non-enum controls
};
```

For backward compatibility, sketches that don't use enums just pass `NULL` for the last field.

### Layer 3: Serial CLI Parser (`ltp_serial_cli/device.py`)

In `get_controls()`, after parsing name and description, if `ctrl_type == CTRL_ENUM`:

```python
if ctrl_type == CTRL_ENUM:
    num_options = packet.payload[offset]
    offset += 1
    enum_values = []
    for _ in range(num_options):
        opt_value = packet.payload[offset]
        offset += 1
        label_end = offset
        while label_end < len(packet.payload) and packet.payload[label_end] != 0:
            label_end += 1
        label = packet.payload[offset:label_end].decode("utf-8", errors="replace")
        offset = label_end + 1
        enum_values.append({"value": opt_value, "label": label})
    controls.append({
        "id": ctrl_id,
        "type": ctrl_type,      # CTRL_ENUM = 0x10
        "flags": ctrl_flags,
        "min": min_val,
        "max": max_val,
        "value": value,
        "name": name,
        "description": description,
        "enum_values": enum_values,
    })
```

### Layer 4: Serial Sink Bridge (`ltp_serial_sink/v2_renderer.py`)

Update `CTRL_TYPE_NAMES`:
```python
CTRL_TYPE_NAMES = {
    CTRL_BOOL: "bool",
    CTRL_UINT8: "uint8",
    CTRL_UINT16: "uint16",
    CTRL_INT8: "int8",
    CTRL_INT16: "int16",
    CTRL_ACTION: "action",
    CTRL_ENUM: "enum",
}
```

When registering device controls, if `ctrl_type == "enum"`, use the `enum_values` from the parsed control instead of requiring `DeviceControl.enum_values` to be pre-populated:

```python
elif device_ctrl.control_type == "enum":
    options = [
        EnumOption(value=str(ev["value"]), label=ev["label"])
        for ev in device_ctrl.enum_values
    ]
    # Map numeric value to the matching option
    str_value = str(current_value)
    self._controls.register(
        EnumControl(
            id=device_ctrl.name,
            name=ctrl_name,
            description=device_ctrl.description,
            value=str_value,
            options=options,
            group="hardware",
        )
    )
```

The set_control path must convert enum string values back to the numeric value for the firmware.

### Layer 5: Web UI

No changes needed. The dashboard `renderInteractiveControls()` and the sinks/sources pages already render `enum` controls as `<select>` dropdowns with option labels. Once the control arrives as type `"enum"` with `options`, it will render correctly everywhere.

### Layer 6: Protocol Constants

Add to `ltp_protocol.h`:
```c
#define CTRL_TYPE_ENUM      0x10  // Already reserved, just needs implementation
```

Add to `ltp_serial_cli/protocol.py`:
```python
CTRL_ENUM = 0x10    # Already defined, just needs parsing support
```

## Immediate Fix (Before Protocol Change)

While the protocol extension is being implemented, `local_mode` and similar controls can be handled by a lookup table in the serial sink bridge. The sink already has special-case handling for `gamma` — the same approach works here:

In `_register_device_controls()` in `sink.py`, add a mapping for known enum-like controls:

```python
KNOWN_ENUMS = {
    "local_mode": [
        (0, "Off"), (1, "Cylon"), (2, "Rainbow"),
        (3, "Fire"), (4, "Sparkle"), (5, "Chase"),
        (255, "Cycle"),
    ],
    "matrix_layout": [
        (0, "Normal"), (1, "Serpentine"), (2, "Vertical"),
        (3, "Vert+Serp"), (4, "Bottom"), (5, "Bottom+Serp"),
        (6, "Bottom+Vert"), (7, "Bottom+V+S"),
    ],
    "active_strip": [
        (0, "Strip 0"), (1, "Strip 1"),
    ],
}
```

When a uint8 control name matches a key in `KNOWN_ENUMS`, register it as an `EnumControl` instead of `NumberControl`.

## Migration Path

1. **Phase 1 (immediate):** Apply the `KNOWN_ENUMS` lookup in `sink.py` so existing firmware gets proper dropdowns.
2. **Phase 2:** Implement `CTRL_TYPE_ENUM` in the protocol library (`ltp_protocol.h`), update `sendInfoControls()` in firmware, update the parser in `device.py`.
3. **Phase 3:** Convert `local_mode`, `matrix_layout`, and `active_strip` in firmware sketches from `CTRL_TYPE_UINT8` to `CTRL_TYPE_ENUM` with proper option labels.
4. **Phase 4:** Remove the `KNOWN_ENUMS` fallback once all firmware is updated.

## Costs

- **Firmware flash:** ~50 bytes per enum control for PROGMEM strings + struct.
- **Firmware RAM:** Zero (all PROGMEM).
- **Protocol overhead:** ~50 bytes per enum control in INFO_CONTROLS response (one-time at connection).
- **Python changes:** Minimal — parser addition and sink bridge enum registration.
- **Web UI changes:** None.
