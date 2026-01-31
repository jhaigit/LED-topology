# ESP32 Ring Controller - Hang/Freeze Investigation

> **STATUS (2026-01-30)**: Most likely power-related. Deferred pending hardware
> debugging. See `docs/TODO.md` for current priorities.

## Symptoms
- Device hangs/freezes frequently
- During hang: telnet session also freezes
- **PING FAILS** during hang - WiFi stack itself is dead
- Characters echo during hang - this is **local terminal echo**, TCP timeout not expired
- **Tends to hang when all/many LEDs are on** (bright states)

## Root Cause Hypothesis (Ranked)

### 1. CRITICAL: Electrical/Power Issues (NOW PRIMARY SUSPECT)

**Evidence**: Ping fails + hangs when LEDs on = **WiFi stack crash, not just app hang**

**Power calculations for 202 APA102 ring**:
- Each APA102 LED: up to 60mA at full white (20mA per channel)
- 202 LEDs × 60mA = **12.12 Amps** at full brightness white
- 4 WS2812 LEDs: additional ~240mA
- **Total potential draw: ~12.4A at 5V**

**Possible electrical failure modes**:

1. **Power supply voltage sag**
   - High current draw causes voltage to drop below 4.5V
   - APA102 data/clock signals become unreliable
   - ESP32 5V input drops, 3.3V regulator struggles
   - WiFi radio is very sensitive to voltage - can crash first

2. **ESP32 brownout**
   - ESP32 brownout detector triggers around 2.4V on 3.3V rail
   - Brownout can cause soft crash (WiFi dies, CPU continues partially)
   - Or hard reset (but user would see reboot messages)

3. **Ground bounce / voltage drop on ground plane**
   - High LED current flows through shared ground
   - Ground potential rises relative to ESP32
   - Causes communication errors, WiFi crashes

4. **Inadequate decoupling capacitors**
   - LED switching causes high-frequency noise on power rails
   - Can crash WiFi radio or cause SPI communication errors

5. **Cable/connector resistance**
   - Long wires or poor connections add resistance
   - I²R losses cause voltage drop at LEDs and ESP32

**How to test**:
- Measure 5V rail with multimeter/scope during bright animations
- Measure 3.3V rail on ESP32 during bright animations
- Run with brightness set to 25% - does hang still occur?
- Run with LEDs disconnected entirely - does hang still occur?
- Add large capacitor (1000µF+) near ESP32

---

### 2. HIGH: Blocking TCP/WiFi Operations

**Still relevant if electrical is ruled out**

`wifi.readLine()` and `protocol.processMessage()` in main loop have no timeout:

```cpp
// ltp_esp32_ring.ino:829-837
String line = wifi.readLine();  // NO TIMEOUT - blocks if partial data
if (line.length() > 0) {
    String response = protocol.processMessage(line);  // JSON parse can block
    ...
}
```

**wifi_transport.h:210-231** - `readLine()` has no timeout:
```cpp
String readLine() {
    String line;
    while (client.available()) {  // No timeout!
        char c = client.read();
        if (c == '\n') return line;
        line += c;
    }
    return line;
}
```

---

### 3. HIGH: FastLED + WiFi Timing Conflict

**APA102 uses SPI, WS2812 uses GPIO bit-banging**

During `FastLED.show()`:
- APA102: SPI at 8MHz for 202 LEDs = ~2ms with interrupts disabled
- WS2812: Precise timing requires disabling interrupts for ~1.2ms per 4 LEDs
- **Combined: ~3-5ms with interrupts disabled per show()**

ESP32 WiFi requires timely interrupt servicing. If interrupts are blocked too long:
- WiFi watchdog can trigger
- WiFi stack can crash
- Connection state becomes corrupt

**FastLED on ESP32 has known WiFi issues** - search "FastLED ESP32 WiFi crash"

---

### 4. MEDIUM: Telnet Line Reading Without Timeout

**telnet_server.h:107-134**:
```cpp
while (client.available()) {  // Blocking loop - no yield
    char c = client.read();
    // ...
}
```

---

### 5. MEDIUM: DualPrint Blocking on Slow Client

**telnet_server.h:31-42** - All `dualOut.printf()` calls block if telnet client not reading:
```cpp
size_t write(const uint8_t* buffer, size_t size) override {
    Serial.write(buffer, size);
    if (telnetClient && telnetClient->connected()) {
        telnetClient->write(buffer, size);  // BLOCKS until client reads
    }
    return size;
}
```

---

### 6. MEDIUM: Heavy Animation CPU Load

Sin wave modes do 202 × sin() calculations per frame at 50 FPS. Combined with FastLED.show() (15-50ms for APA102+WS2812), loop can take >100ms.

---

### 7. LOW: cmdTouchMon Blocking

**ltp_esp32_ring.ino:558-606** - Blocks main loop for up to 300 seconds.

---

## Debugging Plan

### Phase 1: Rule Out Electrical (HIGHEST PRIORITY)

1. **Measure voltage under load**
   - Connect multimeter to 5V rail
   - Run bright white animation (all LEDs full)
   - Watch for voltage drop below 4.5V
   - Measure 3.3V on ESP32 - should stay above 3.0V

2. **Test with reduced brightness**
   - Set brightness to 64 (25%) via controller
   - Does hang still occur at same frequency?

3. **Test with LEDs disconnected**
   - Disconnect LED data lines (leave ESP32 powered)
   - Run normally - does hang still occur?

4. **Add bulk capacitance**
   - Add 1000µF+ capacitor across 5V near ESP32
   - Add 100µF across 3.3V if accessible

5. **Check power supply rating**
   - What is the power supply rated for?
   - Need at least 15A for full brightness + margin

### Phase 2: Software Isolation

1. **Test A**: Disconnect all TCP clients (controller) - does hang still occur?
2. **Test B**: Set local_mode to 0 (BLANK) - does hang still occur?
3. **Test C**: Don't use telnet at all - does hang still occur?
4. **Test D**: Stop UDP pixel streaming - does hang still occur?

### Phase 3: Add Instrumentation

Add heartbeat at start of loop:
```cpp
void loop() {
    static uint32_t lastHeartbeat = 0;
    if (millis() - lastHeartbeat > 1000) {
        Serial.println("HEARTBEAT");
        lastHeartbeat = millis();
    }
    // ... rest of loop
}
```

### Phase 4: Code Fixes (If Software Cause)

1. **Add timeout to wifi.readLine()**
2. **Add yield() in FastLED operations**
3. **Make DualPrint non-blocking**
4. **Reduce animation frame rates**

---

## Suspicious Code Locations

| File | Line | Issue |
|------|------|-------|
| **ELECTRICAL** | - | Power supply, wiring, capacitors |
| ring_driver.h | 44-50 | `FastLED.show()` interrupt timing |
| ltp_esp32_ring.ino | 829 | `wifi.readLine()` no timeout |
| ltp_esp32_ring.ino | 832 | `protocol.processMessage()` blocking |
| wifi_transport.h | 210-231 | `readLine()` no timeout |
| telnet_server.h | 107-134 | `while(available())` no yield |
| telnet_server.h | 31-42 | Blocking write to telnet |
| local_modes.h | 770-809 | Heavy sin() calculations |

---

## Key Insight Update

**Previous theory**: Telnet echo = WiFi alive, app dead
**New evidence**: Ping fails = **WiFi stack itself is crashed/hung**

The telnet echo during hang is **local terminal echo**, not device echo. The TCP connection remains "open" from the client's perspective until TCP timeout (typically 30-120 seconds), but the device is not responding at all.

**Ping failure + correlation with bright LEDs = strong indicator of electrical/power issue**

The ESP32 WiFi radio is very sensitive to:
- Voltage drops below ~4.5V on 5V input
- Voltage noise on power rails
- Interrupt latency (FastLED disables interrupts)

---

## Hardware Checklist

- [ ] Power supply rated for 15A+ at 5V?
- [ ] Short, thick power cables to LEDs?
- [ ] Separate power injection points along ring?
- [ ] Bulk capacitor (1000µF+) near ESP32?
- [ ] Decoupling caps on ESP32 3.3V rail?
- [ ] Good ground connection (star ground, not daisy chain)?
- [ ] Level shifter for 3.3V→5V on data lines (optional but helps)?
