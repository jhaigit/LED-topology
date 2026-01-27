# Control Set Failure Diagnosis

## Symptom
Setting controls (e.g., `local_mode`) via the web UI fails ~50% of the time.
Retry usually works. The ESP32 does not hang.

### Error chain
```
Pool: Request to <sink_id> failed:          ← empty exception message
Failed to set control on Time-Machine: no response
PUT /api/sinks/.../controls → 200 with {"local_mode":"error"}
```

---

## Hypotheses

### Category A: ESP32 Connection/Socket Issues

#### A1. Health check opens competing TCP connection
**Status**: UNTESTED
**Likelihood**: HIGH

The controller health check (`_ping_device`) opens a new raw TCP connection to
port 5000 every 30 seconds, then immediately closes it. This briefly occupies
one of the ESP32's 4 client slots. While the health check socket is connected,
the ESP32's `readLine()` iterates all clients — if it encounters the health
check client first and sets `activeClientIdx`, the response could be sent to
the wrong (immediately-closed) client.

**Test**: Set `health_check_interval` to 3600 (1 hour) and observe if failures
stop. Or monitor ESP32 USB for "WiFi: Client N connected/disconnected" churn
during failures.

**Fix if confirmed**: Change health check to use the pool connection (send a
lightweight ping message) instead of opening a separate TCP socket. Or have the
ESP32 ignore clients that haven't sent any data.

---

#### A2. Pool connection silently dropped (half-dead socket)
**Status**: UNTESTED — diagnostic logging added
**Likelihood**: MEDIUM

`ControlClient.is_connected` checks `not self._connection.is_closed`, but
`is_closed` is only set when `close()` is called explicitly or `handle_messages()`
exits. If the TCP connection is half-dead (ESP32 reset the socket, but the
controller hasn't tried to read), `is_connected` returns True. The next
`request()` then fails on `send()` or `drain()` with a broken pipe.

**Test**: Enhanced logging added to pool `request()` to print
`type(e).__name__: {e!r}` — will reveal exact exception type
(ConnectionResetError, BrokenPipeError, TimeoutError, etc.).

**Fix if confirmed**: Add a TCP keepalive or periodic protocol-level ping on
the pool connection.

---

#### A3. ESP32 WiFi stack drops connections under load
**Status**: UNTESTED
**Likelihood**: LOW

The ESP32's LWIP TCP stack has limited resources. Concurrent touch events,
UDP pixel data, and control messages could overwhelm it.

**Test**: Test control sets with no touch activity and no pixel streaming
active. If it works reliably in isolation, load is the cause.

**Fix if confirmed**: Reduce concurrent socket usage; prioritize control
channel.

---

#### A4. ESP32 `readLine()` sends response to wrong client
**Status**: UNTESTED — diagnostic logging added
**Likelihood**: HIGH (related to A1)

`readLine()` iterates all 4 client slots, returns the first complete line, and
sets `activeClientIdx`. If data arrives from a different client slot (e.g., the
health check connection or a stale slot), `activeClientIdx` points wrong and
the response goes to the wrong client.

**Test**: ESP32 debug logging added: `TCP rx[N]` and `TCP tx[N]` showing which
client index received the request and which sends the response. If N differs,
this is confirmed.

**Fix if confirmed**: Track which client sent the request and always respond to
that specific client, regardless of `activeClientIdx`.

---

#### A5. ESP32 response truncated or delayed
**Status**: UNTESTED
**Likelihood**: LOW

If the JSON response is larger than one TCP segment and a segment is delayed,
the controller's `readline()` may not complete before the 5-second timeout.

**Test**: ESP32 debug logging shows response length (`TCP tx[N]: M bytes`).
Control set responses are small (~100 bytes), so this is unlikely.

**Fix if confirmed**: N/A — unlikely to be the cause for small responses.

---

### Category B: Controller/Pool Issues

#### B1. Sequence number 0 not echoed
**Status**: RULED OUT

Investigated: `ControlClient._next_seq()` increments before returning, so the
first seq is 1 (never 0). The ESP32 code `if (seq > 0) resp["seq"] = seq`
would skip seq=0, but seq=0 never occurs.

---

#### B2. `handle_messages` reader task dies silently
**Status**: UNTESTED
**Likelihood**: MEDIUM

`ControlClient.connect()` spawns `handle_messages()` as a fire-and-forget task.
If it crashes (e.g., malformed JSON from the ESP32), the connection's `is_closed`
is set to True in the `finally` block. But there's a race: `get_connection()`
may return the conn before `is_closed` is updated.

The empty exception message in the log (`failed: `) could be a
`ConnectionError("")` raised by `send()` when `_closed` is True.

**Test**: Enhanced logging in `_handle_response` now logs unmatched messages.
Also check if `handle_messages` exit is logged: "Connection closed: <peer>".

**Fix if confirmed**: Add reconnection logic when `handle_messages` exits, or
check `is_connected` inside `request()` after acquiring `conn.lock`.

---

#### B3. Pool `get_connection()` races with reconnect loop
**Status**: UNTESTED
**Likelihood**: LOW-MEDIUM

`get_connection()` reads `self._connections` without holding `self._lock`.
The reconnect loop holds `self._lock` while disconnecting and reconnecting.
A race: `request()` obtains a `conn` reference, then the reconnect loop
disconnects and replaces it. `request()` then uses the now-closed client.

**Test**: Check if failures correlate with reconnect log entries
("Pool: Connected to sink...").

**Fix if confirmed**: Hold `self._lock` during the full `request()` flow, or
re-check `conn.connected` after acquiring `conn.lock`.

---

### Category C: Timing/Interaction Issues

#### C1. Health check TCP connect exhausts ESP32 client slots
**Status**: UNTESTED (variant of A1)
**Likelihood**: MEDIUM

With the pool connection occupying 1 slot and health check briefly occupying
another, 2 of 4 slots are used. If multiple health checks overlap (unlikely at
30s interval) or other connections exist, slots could be exhausted.

**Test**: Covered by A1 test.

---

#### C2. Touch input events interleave with control responses
**Status**: UNTESTED
**Likelihood**: LOW

Touch events call `wifi.send()` to push `input_event` JSON. If this data
reaches the controller interleaved with a `control_set_response`, the
controller's reader might see the input_event first. However, input_events
have no `seq`, so `_handle_response` correctly routes them as unsolicited
messages, and the actual response should arrive next.

**Test**: Disable touch input events (`inputEventsEnabled = false` via USB
terminal) and test controls. If failures stop, interleaving is the cause.

**Fix if confirmed**: Queue input events and only send them between
request/response cycles, or use separate TCP connections for events vs control.

---

## Diagnostic Changes Made

### Controller (Python)
- `sink_connection_pool.py`: Enhanced `request()` logging with connection state
  before send and exception type/repr on failure.
- `libltp/transport.py`: Added logging in `_handle_response` for unmatched
  messages, showing pending seq numbers.

### ESP32 (Arduino)
- `ltp_esp32_ring.ino`: Added `TCP rx[N]` and `TCP tx[N]` debug output showing
  which client index handles each request/response.
- `wifi_transport.h`: Added `getActiveClient()` accessor for debug logging.

### How to use diagnostics
1. Flash ESP32 with diagnostic build
2. Monitor USB serial for `TCP rx/tx` and `WiFi: Client` messages
3. Run controller with `--log-level debug` or set `logging.DEBUG` for
   `ltp_controller.sink_connection_pool` and `libltp.transport`
4. Trigger control set from web UI, note success/failure, correlate logs

---

### Category D: ESP32 Hang (LED Freeze + Ping Failure)

These hypotheses address a separate symptom: the LED pattern freezes entirely
and the device stops responding to pings. May or may not be related to control
set failures.

#### D1. WiFi `print()` blocks indefinitely on full TCP send buffer
**Status**: INVESTIGATED — partially addressed
**Likelihood**: HIGH

`WiFiClient.print()` is a blocking call. If the controller stops reading (or
the TCP connection is half-dead), the send buffer fills and `print()` blocks
the main loop. Since all LED updates, touch polling, and WiFi maintenance run
in `loop()`, a blocked `print()` freezes everything. The ESP32 WiFi stack
runs on a separate core but the Arduino `loop()` is single-threaded.

Non-blocking send was attempted but caused worse problems (dropped protocol
responses, broken capability discovery). Reverted to original blocking
`print()`.

**Test**: Monitor USB serial during a hang. If output stops mid-message or
after a `wifi.send()` call, this is confirmed. The telnet interface will also
freeze since it shares the main loop.

**Fix if confirmed**: Set `client.setTimeout()` to a bounded value (e.g., 500ms)
so `print()` returns rather than blocking forever. Or move WiFi sends to a
separate FreeRTOS task with a queue.

---

#### D2. Watchdog timer not feeding (WDT reset vs hang)
**Status**: UNTESTED
**Likelihood**: MEDIUM

The ESP32 has a task watchdog (TWDT) that resets the chip if a task doesn't
yield for too long. If the main loop blocks (e.g., in `print()`), the TWDT
may fire and reset the chip. This would look like a brief disconnect followed
by the device coming back. If the TWDT is disabled or has a very long timeout,
the device would appear hung instead.

**Test**: Check if the device eventually recovers (WDT reset) or stays hung
indefinitely. USB serial may show `rst:0xc (SW_CPU_RESET)` on recovery.

**Fix if confirmed**: Ensure TWDT is enabled with a reasonable timeout. If the
hang is caused by a blocking call, fix the blocking call (see D1).

---

#### D3. Stack overflow in main task
**Status**: UNTESTED
**Likelihood**: LOW-MEDIUM

The Arduino main task has a default stack of 8192 bytes. Large local variables
(e.g., `ArduinoJson` `JsonDocument` on the stack, 1KB line buffers) could
overflow it. A stack overflow corrupts memory and causes unpredictable hangs.

**Test**: Increase Arduino loop task stack size via `SET_LOOP_TASK_STACK_SIZE(16384)`
in the sketch. If hangs stop, stack overflow was the cause. Also check
`uxTaskGetStackHighWaterMark(NULL)` periodically.

**Fix if confirmed**: Increase stack size or move large buffers to heap/global.

---

#### D4. LWIP/WiFi stack deadlock
**Status**: UNTESTED
**Likelihood**: LOW-MEDIUM

The ESP32 WiFi/LWIP stack uses internal mutexes. Certain sequences of
operations (e.g., `send()` from the main loop while a TCP callback is being
processed on the WiFi task) could deadlock. This would freeze the main loop
and prevent ping responses since LWIP is also blocked.

**Test**: Hard to reproduce. Adding `esp_task_wdt_status()` checks or
enabling core dump on panic may reveal the deadlock location.

**Fix if confirmed**: Avoid WiFi operations from interrupt context; ensure
all network I/O happens from the same task.

---

#### D5. FastLED SPI transaction blocks or corrupts WiFi timing
**Status**: UNTESTED
**Likelihood**: LOW

FastLED's APA102 SPI output disables interrupts briefly during data transfer.
For 202 LEDs at SPI speeds, this takes ~1ms. If the WiFi stack needs to
service a time-critical operation during that window, packets could be lost
or the WiFi association could degrade.

**Test**: Reduce `show()` frequency or test with fewer LEDs. Monitor WiFi
RSSI and disconnection events during LED updates.

**Fix if confirmed**: Use DMA-based SPI for LED output (ESP32 hardware SPI
with DMA doesn't disable interrupts).

---

#### D6. Heap exhaustion or memory fragmentation
**Status**: UNTESTED
**Likelihood**: LOW-MEDIUM

Repeated `String` allocations (JSON building, `readLine()` return values)
fragment the heap. Eventually, an allocation fails and the WiFi stack (which
also uses heap) can't function. The device appears hung because it can't
send or receive any network traffic.

**Test**: Periodically log `ESP.getFreeHeap()` and `ESP.getMinFreeHeap()`.
If free heap trends downward over time and hangs correlate with low heap,
this is confirmed.

**Fix if confirmed**: Pre-allocate buffers, avoid `String` where possible,
use `char[]` buffers.

---

## Diagnostic Tools Added

### Telnet Server
A telnet server on port 23 mirrors all USB serial output and accepts the
same terminal commands. This allows remote monitoring without physical USB
access. All `Serial.print` calls have been replaced with `dualOut.print`
(writes to both Serial and any connected telnet client).

**Usage**: `telnet <esp32-ip>` or `nc <esp32-ip> 23`

---

## Test Plan Priority

| Priority | Test | Targets |
|----------|------|---------|
| 1 | Monitor ESP32 USB/telnet + controller debug logs during failures | A1, A2, A4, B2, D1 |
| 2 | Disable health check (set interval to 3600) | A1, A4, C1 |
| 3 | Log `ESP.getFreeHeap()` periodically, check during hangs | D6 |
| 4 | Disable touch input events | C2 |
| 5 | Test with no pixel streaming active | A3 |
| 6 | Increase loop task stack size to 16KB | D3 |
| 7 | Check USB serial after hang recovery for WDT reset message | D2 |

---

## Resolution Log

*(Updated as tests are run and data comes in)*

| Date | Test | Result | Conclusion |
|------|------|--------|------------|
| | | | |
