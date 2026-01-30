# ESP32 Ring Controller - Hang/Freeze Investigation

## Symptoms
- Device hangs/freezes frequently
- During hang: telnet session also freezes
- Characters echo during hang (likely local terminal echo, not device)
- WiFi stack appears partially responsive (low-level)

## Root Cause Hypothesis (Ranked)

### 1. CRITICAL: Blocking TCP/WiFi Operations (60% likely)

**Primary suspect**: `wifi.readLine()` and `protocol.processMessage()` in main loop

```cpp
// ltp_esp32_ring.ino:829-837
String line = wifi.readLine();  // NO TIMEOUT - blocks if partial data
if (line.length() > 0) {
    String response = protocol.processMessage(line);  // JSON parse can block
    ...
}
```

**Why telnet freezes but echoes**: WiFi stack handles low-level TCP, but main loop is blocked waiting for complete JSON line. Telnet is processed in same loop iteration.

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

### 2. HIGH: Telnet Line Reading Without Timeout

**telnet_server.h:107-134**:
```cpp
while (client.available()) {  // Blocking loop - no yield
    char c = client.read();
    // ...
}
```

### 3. HIGH: DualPrint Blocking on Slow Client

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

### 4. MEDIUM: Heavy Animation + FastLED Timing

Sin wave modes do 202 × sin() calculations per frame at 50 FPS. Combined with FastLED.show() (15-50ms for APA102+WS2812), loop can take >100ms.

### 5. MEDIUM: cmdTouchMon Blocking

**ltp_esp32_ring.ino:558-606** - Blocks main loop for up to 300 seconds:
```cpp
while (millis() < endTime) {  // 10-300 second blocking loop
    delay(10);
    // NO WiFi updates, NO LED updates
}
```

## Debugging Plan

### Phase 1: Isolate the Cause (No Code Changes)

1. **Test A**: Disconnect all TCP clients (controller) - does hang still occur?
2. **Test B**: Set local_mode to 0 (BLANK) - does hang still occur?
3. **Test C**: Don't use telnet at all - does hang still occur?
4. **Test D**: Stop UDP pixel streaming - does hang still occur?
5. **Test E**: Avoid these terminal commands: `touchmon`, `test`, `calibrate`

### Phase 2: Add Instrumentation

Add heartbeat at start of loop to identify where it blocks:
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

### Phase 3: Code Fixes (Priority Order)

1. **Add timeout to wifi.readLine()**
   ```cpp
   String readLine(uint32_t timeoutMs = 100) {
       uint32_t start = millis();
       String line;
       while (millis() - start < timeoutMs) {
           if (client.available()) {
               char c = client.read();
               if (c == '\n') return line;
               line += c;
           }
       }
       return line;  // Return partial or empty on timeout
   }
   ```

2. **Add JSON size limit and validation**
   ```cpp
   if (jsonLine.length() > 2048) {
       return buildError(0, 1, "MESSAGE_TOO_LARGE", "Max 2KB");
   }
   ```

3. **Make DualPrint non-blocking**
   - Add output buffer with size limit
   - Drop output if telnet client is slow

4. **Add yield() calls in blocking loops**
   ```cpp
   while (client.available()) {
       // ...
       yield();  // Let WiFi stack run
   }
   ```

5. **Optimize sin wave calculations**
   - Use lookup table instead of sin()
   - Or reduce frame rate when heavy modes active

## Suspicious Code Locations

| File | Line | Issue |
|------|------|-------|
| ltp_esp32_ring.ino | 829 | `wifi.readLine()` no timeout |
| ltp_esp32_ring.ino | 832 | `protocol.processMessage()` blocking |
| wifi_transport.h | 210-231 | `readLine()` no timeout |
| wifi_transport.h | 295, 303 | Multiple `WiFi.status()` per loop |
| telnet_server.h | 107-134 | `while(available())` no yield |
| telnet_server.h | 31-42 | Blocking write to telnet |
| sink_protocol.h | 82-112 | JSON parse no size limit |
| local_modes.h | 770-809 | Heavy sin() calculations |
| ltp_esp32_ring.ino | 558-606 | cmdTouchMon blocks 10-300s |

## Key Insight

The fact that telnet **echoes characters** but **doesn't respond** strongly indicates:
- WiFi TCP stack is alive (handles SYN/ACK, receives data)
- Main application loop is blocked (not processing received data)
- Most likely blocked in `readLine()` or `processMessage()`

This is NOT a WiFi disconnect or watchdog reset - it's an application-level hang.
