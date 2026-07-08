# Code & Architecture Review — 2026-07-02

Full-codebase review covering `src/libltp`, `src/ltp_source`, `src/ltp_sink`, `src/ltp_artnet`,
`src/ltp_controller` (core + web + virtual sources), `src/ltp_media_source`, `src/ltp_serial_sink`,
`src/ltp_serial_cli`, `src/ltp_thermal_source`, and `arduino/`. Findings were produced by parallel
subsystem reviews and the highest-severity items were independently re-verified against the code
(several libltp items were verified by executing the code). Line numbers refer to the tree at
commit `2c6346e`.

Severity: **Critical** = kills a whole subsystem or the process; **High** = a feature is broken or
data is corrupted in realistic use; **Medium** = wrong behavior or degradation under specific
conditions; **Low** = minor bug or inefficiency.

---

## 1. Cross-cutting architectural problems

These recur across subsystems and are the root cause of many individual findings below.

1. **Split-brain threading model in the controller.** Flask runs with `threaded=True` in a daemon
   thread while the engine runs on one asyncio loop. A `run_async` marshaling helper exists
   (`web/app.py:59-68`) but is used inconsistently: `create_route`, `create_rule`/`delete_rule`,
   `sequence_manager.create/update/delete`, `purge_offline_*`, and `sink_group_manager.update` are
   all called directly on Flask worker threads while the loop iterates the same unlocked dicts.
   This produces the scheduler-death, monitor-death, sequence, and purge races below. **Fix class:**
   route every mutation through `run_async` (or loop-confined methods).

2. **The UDP chunking wire format has no frame-boundary metadata.** There is no total-pixel count or
   last-fragment flag; every receiver infers frame completion from its own buffer size. Direct
   consequences: any full frame *smaller* than the sink never renders (three sinks affected),
   reordered chunks tear frames, and duplicate-packet stats are meaningless for chunked streams.
   **Fix class:** add a frame-total field (or use the spec's fragment flag) and a shared
   `FrameAssembler` in libltp.

3. **The sink data path is copy-pasted three times** (`ltp_sink/sink.py`, `ltp_artnet/sink.py`,
   `ltp_serial_sink/sink.py`) and the media-source machinery three times (`MediaSource`,
   `MediaSourceGroup`, `MultiChannelSource`). The copies have already diverged (visualizer
   substitution, per-channel rate, gamma exist in some but not others), and every reassembly bug
   must be fixed in triplicate.

4. **Long-lived background tasks lack exception guards.** `_schedule_loop`, `_monitor_loop`, route
   cleanup, and the serial reader thread all die permanently on the first unexpected exception,
   converting transient errors into silent feature loss. **Fix class:** top-level
   `except Exception` + restart-on-crash wrappers + `task.add_done_callback` logging.

5. **Blocking work on the asyncio event loop.** Synchronous `socket.getaddrinfo` in discovery (while
   holding the discovery lock), PyAV decode inside `read_frame()`, serial `open()`/control round
   trips in the serial sink, per-pixel Python render loops, and per-frame `pixels.tolist()` all run
   on loop threads. Under load the loop stalls and UDP drops silently.

6. **Pydantic alias handling silently destroys control constraints** (see §2). Wire format is
   camelCase, models are snake_case, and neither `populate_by_name` nor `by_alias=True` is set —
   constraints evaporate on serialization *and* on every local `set_value`.

7. **Per-pixel Python loops at frame rate are endemic** — source patterns, all controller virtual
   sources, media visualizers, and the terminal renderer. At matrix sizes (64×64 = 4096 px) single
   frames take on the order of seconds and starve everything sharing the loop. Nearly all are
   straightforwardly vectorizable with numpy.

---

## 2. libltp core + ltp_source / ltp_sink / ltp_artnet

### High

- **[bug] Remote `control_set` on a pattern color control kills the source's render loop.**
  `ltp_source/source.py:264-266` passes the validated hex *string* into `Pattern.set_param`, which
  assigns it onto a `tuple[int,int,int]` field with no validation (`patterns/base.py:73-81`,
  `validate_assignment` off). `SolidPattern.render` then raises `ValueError` (verified by
  execution); `_render_loop` (`source.py:271-320`) has no exception guard, so the task dies while
  `is_running` stays True. Streaming stops for all subscribers.

- **[bug] A sink never renders frames from a source with fewer pixels than the sink.**
  `ltp_sink/sink.py:449-460` (verified): any packet with `len < buf_len` is treated as a chunk;
  a 60-px source → 120-px sink fills `[0:60]`, hits `end < buf_len → return` on every packet, and
  `renderer.render()` is never called — permanent "Waiting for data...". Same logic in
  `ltp_artnet/sink.py:319-330` and `ltp_serial_sink/sink.py:593-605` (§5). Root cause: cross-cutting
  item #2.

- **[bug] `Source.connect_to_sink` starts the wrong stream — works only by counter coincidence.**
  `ltp_source/source.py:360-368`: `create_stream()` generates a *local* ID whose return value is
  discarded, then `start_stream()`/`get_stream()` use the *sink's* stream ID from the response.
  `StreamManager.start_stream` is a silent no-op for unknown IDs (`libltp/transport.py:502-506`).
  If counters have diverged, nothing streams or an unrelated stream is corrupted. The
  `ControlClient` is also never closed in `stop()` — leaked TCP connection.

- **[protocol] MONO_PACKED frames can never be decoded.** Sender writes `pixel_count` in the header
  but only `ceil(N/8)` data bytes (`libltp/transport.py:374-389`); `DataPacket._decode_raw`
  (`libltp/protocol.py:414-428`) demands `pixel_count × 1` bytes. Verified by execution: every
  packed packet raises `INVALID_FORMAT` and is dropped.

- **[protocol] Control constraints are serialized with the wrong keys and silently lost.**
  `ControlBase.to_dict` (`libltp/controls.py:47-51`) uses `model_dump()` without `by_alias=True`,
  emitting `min_length`/`max_length` where the spec requires `minLength`/`maxLength`; the receiver
  populates only by alias, so constraints are discarded. Worse, `ControlRegistry.set_value`
  (`controls.py:353-356`) rebuilds the control the same way, so after **one** successful set the
  constraint is gone even locally (verified: a `maxLength=5` string accepted a 24-char value on the
  second set).

### Medium

- **[protocol] Art-Net multi-universe output misaligns pixels.** `ArtNetSender.send_pixels`
  (`ltp_artnet/sender.py:159-165`, verified) slices the flat byte stream at 512-byte boundaries, so
  RGB pixel 170 straddles universes 0/1 and universe 1 starts mid-pixel. The project's own
  `universe_pixel_range` (`ltp_artnet/protocol.py:449`) and standard receivers (WLED) expect
  510 bytes/universe → every universe ≥ 1 shows channel-rotated colors above 170 px.

- **[bug] One malformed or unknown control message tears down the whole TCP connection.**
  `ControlConnection.receive` (`libltp/transport.py:77-84`) catches all exceptions (including
  unknown-`MessageType` `ValueError`) and returns `None`, which `handle_messages` treats as EOF.
  A newer peer sending one extensible message type disconnects the session.

- **[race] `ControlClient` timeout/response race.** `request()` (`libltp/transport.py:279-287`):
  a response landing between `wait_for` cancellation and `_pending` cleanup triggers
  `set_result` on a cancelled future → `InvalidStateError` → the handler-error path sends a bogus
  error frame back to the server. Also `connect()` (line 254) drops the reader-task reference
  (GC hazard per asyncio docs).

- **[bug] Blocking DNS on the event loop while holding the discovery lock.**
  `_add_service` (`libltp/discovery.py:584,596`) calls synchronous `socket.getaddrinfo` inside the
  async handler under `self._lock`; a slow resolution stalls the loop — including active pixel
  streaming in the same process — for seconds.

- **[race] `ControlServer.stop` iterates `_connections` while closes mutate it.**
  `libltp/transport.py:205-207` vs. removal in `_handle_client` (191-194): list shifts during the
  await skip a connection, leaving its socket open and `on_disconnect` unfired.

- **[bug] `ltp-artnet-sink --color-format grb` crashes at startup.** `ltp_artnet/cli.py:88` offers
  `grb`; `cli.py:161` does `ColorFormat["GRB"]` → `KeyError` (no such member in `libltp/types.py`).

- **[inefficiency] Art-Net sink bypasses its own rate limiter.** `ltp_artnet/sink.py:350` calls sync
  `send_pixels` per incoming UDP packet on the event loop; `send_pixels_async` (which enforces
  `max_fps=44`) is dead code. A 60 Hz source is forwarded at 60 Hz with socket writes inline in
  `datagram_received`.

### Low

- Chunking deviates from the spec (reserved byte reused as `chunk_index`, fragment flag never set)
  and is order-fragile; chunks share a sequence number so `SinkStats` counts them as duplicates
  (`libltp/protocol.py:305-321`, `ltp_sink/sink.py:99-108`).
- Scalar payloads use native endianness while headers are big-endian
  (`libltp/protocol.py:541,613,622`) — cross-architecture peers misread FLOAT32/INT16.
- MONO_PACKED size guard is an `assert` (`libltp/transport.py:378`) — vanishes under `python -O`.
  `MAX_CHUNK_PIXELS=480` is RGB-derived; RGBW chunks are 1932 bytes, defeating the ~1500-byte MTU
  goal (`libltp/types.py:418-422`).
- Gradient pattern `color_0..color_3` controls are silently dropped by `set_param`'s `hasattr` gate
  (`ltp_source/patterns/base.py:79-81`).
- Per-pixel Python loops per frame: rainbow/plasma/fire patterns, terminal renderer
  (`ltp_sink/renderers/terminal.py:180-223`), `scale_buffer` (`libltp/topology.py:377-381`);
  `to_stream_order`/`from_stream_order` are O(n²) (`topology.py:135-143,289-312`).
- `get_local_ip` leaks its socket on connect failure (`libltp/addr.py:112-117`); Art-Net
  `packet_bytes` stat counts rows not bytes (`ltp_artnet/sink.py:307`); `DataReceiver` accepts
  pixel data from any host with no source validation (`libltp/transport.py:441-450`).

---

## 3. Controller core (routing, rules, sequences, pools)

### Critical

- **[bug] A looping sequence with zero delays and fast-failing actions starves the event loop
  permanently.** `SequenceManager._run` (`sequence.py:262-293`, verified) only sleeps when
  `delay > 0`; `_pause_event.wait()` returns immediately when set; the rule-engine action handlers
  return synchronously on their failure paths (e.g. "Sink not found",
  `rule_engine.py:435-438,540-543`). A `loop: true` sequence with zero delays whose target sink is
  purged/renamed spins `while True` with no suspension point → the entire controller (web API, all
  routes) freezes until restart. **Fix:** unconditional `await asyncio.sleep(0)` per step + minimum
  loop-iteration delay.

- **[race] The schedule loop dies silently and permanently when rules are mutated during
  iteration.** `_schedule_loop` iterates `self._rules.values()` with awaits inside — including a
  jitter sleep up to `jitter_minutes*60` s (`rule_engine.py:312-318,349`) — while
  `create_rule`/`delete_rule` are called directly from Flask threads (`web/app.py:1851,1934,1981`).
  A dict-size change raises `RuntimeError` from the `for` statement, outside the inner `try` (only
  `CancelledError` is caught at line 319) → all schedule rules stop firing forever, silently. The
  same unprotected iteration in `_on_input_event` (line 213) silently skips remaining rules for an
  event.

- **[race] The route monitor loop can be killed permanently by an exception re-raised from a dead
  route task.** `_stop_route` (`router.py:1189-1200`) does `await task` catching only
  `CancelledError`; a route task that died with a stored exception (e.g. unguarded
  `await route._receiver.stop()` at line 1216, or "dict changed size" from `create_route` inserting
  from a Flask thread at `web/app.py:585`) re-raises inside `_monitor_loop`
  (`router.py:1291-1320`), which has no guard → all subsequent enable/disable/create operations
  queue into `_pending_*` forever. A dead task also stays in `_route_tasks`, blocking restart
  (line 1308).

### High

- **[bug] Restarting a running sequence actually stops it.** `start_sequence`
  (`sequence.py:212-221`) cancels the old task (deferred), sets `state=RUNNING`, creates the new
  task; the old task's `finally` (297-299, verified) then sets `state=STOPPED` / `_task=None`,
  clobbering the new run — the new task exits at its first state check. A rule firing
  `START_SEQUENCE` on an already-running sequence goes dark instead of restarting.

- **[race] Sequence create/update/delete run on Flask threads and call non-threadsafe asyncio
  primitives.** `web/app.py:2037,2073,2089` call the manager directly (start/stop/pause/resume are
  correctly marshaled at 2098-2145). `delete` → `_cancel_run` calls `Task.cancel()` and
  `Event.set()` from the web thread — both documented non-threadsafe; a deleted running sequence
  may keep executing indefinitely.

- **[race] The connection pool holds its global lock across an unbounded TCP connect.**
  `_connect_to_sink` acquires `self._lock` then awaits `ControlClient.connect` — bare
  `asyncio.open_connection` with **no timeout** (`sink_connection_pool.py:119-137`,
  `libltp/transport.py:247-249`). A sink advertising via mDNS but blackholing TCP stalls the pool
  (~2 min kernel SYN retry): every on-demand connect, the reconnect loop, and `pool.stop()` block;
  web paint/control requests 500 on the 10 s `run_async` timeout.

- **[bug] Scalar-source data streaming has never worked, and its shutdown path raises.**
  `scalar_sources/base.py`: `_handle_subscribe` never `await sender.start()` (453-455);
  `_send_data` calls `await sender.send(packet_bytes)` but `DataSender.send` is sync and takes an
  ndarray (395 vs `transport.py:333-339`); `stop()` calls nonexistent `sender.close()` (320) →
  `AttributeError` during `stop_all()`, which aborts the rest of the shutdown chain in
  `cli.py:201-207` (virtual sources, sink controller, router, pool never cleaned up).

### Medium

- **[bug] Schedule jitter blocks the whole scheduler and evaluates later rules against a stale
  clock.** The jitter sleep is inline in `_evaluate_schedule` (`rule_engine.py:346-349`), awaited
  serially per rule; `now` is captured once per pass (311). Rule A jittering 10 min at 08:00 makes
  rule B (08:03) get evaluated at 08:09 against `now=08:00` — it never fires that day.

- **[bug] `frame_rate < 1` crashes the virtual-source route loop into permanent flapping.**
  `router.py:779`: `int(1.0 / frame_interval)` → 0 for `frame_rate=0.5` → `ZeroDivisionError`
  outside the inner try → disconnect/retry loop. The group path guards with `max(1, ...)` (884);
  this path doesn't. `frame_rate` is user-settable and unclamped (`web/app.py:1288`).

- **[bug] Cron `*/N` is off-by-one on 1-based fields; dow `7` never matches.**
  `_cron_field_matches` uses `current % step == 0` (`rule_engine.py:42-44`): day-of-month `*/10`
  fires 10th/20th/30th (not 1st/11th/21st/31st); month `*/3` fires Mar/Jun/Sep/Dec. The docstring
  promises `7=Sun` but there is no 7→6 mapping, so `7` silently never matches.

- **[inefficiency] Full-frame `pixels.tolist()` on every routed frame, on the loop thread, whether
  or not anyone is previewing** (`router.py:1092`, also 767, 865, 1040). A 3840-px group at 30 fps
  allocates ~350k Python ints/s inside `datagram_received`. Snapshot the ndarray; convert in the
  web handler on demand.

- **[inefficiency] One global lock serializes all direct sink painting.** `SinkController._lock`
  (`sink_control.py:78`) wraps every fill/paint/text/image op and `_get_or_create_stream` performs
  up to two 5 s pool requests under it (141,155). One half-dead sink blocks painting on all healthy
  sinks past the web timeout.

- **[race] `purge_offline_sinks/sources` delete from `_sinks`/`_sources` on the Flask thread while
  the loop iterates them once per second per route** (`controller.py:172-186` vs. 465-490,
  `router.py:551-552`) → `RuntimeError` inside a route's keep-alive → spurious route flap.

### Low

- Input-state dicts read from Flask threads while written on the loop (`input_manager.py:63-74` vs.
  242-251) — occasional 500 on `/api/inputs`.
- DST: schedule dedup key is epoch-minute of local time (`rule_engine.py:311,339`) — fall-back hour
  fires twice, spring-forward hour never fires.
- Route reconnect backoff never resets after success (`router.py:328-329,449`).
- Render loops pace with fixed post-render sleeps (`router.py:785,892`) — actual FPS drifts below
  configured as pixel count grows.
- Sink-group member offsets recomputed in place from Flask thread while group fan-out reads them
  (`sink_group.py:145-161` vs. `router.py:1042-1051`) — momentary torn segment mapping.
- Health checks ping devices sequentially with 10 s timeouts (`controller.py:348-371`) — offline
  detection can take minutes with several unreachable devices.

---

## 4. Controller web app + virtual sources

### High

- **[bug] Scene creation 500s whenever any route exists.** `app.py:854-860` (verified) reads
  `route.transform.mirror`, but `RouteTransform` only defines `mirror_x`/`mirror_y`
  (`router.py:61-62`); every route has a default transform, so "+ Save Scene" always fails with ≥1
  route. The activate path (`app.py:929-932`) has the mirrored bug: it sets a new `mirror`
  attribute instead of restoring `mirror_x`/`mirror_y`.

- **[bug] Schedule-trigger rules can never be created — frontend/backend payload mismatch.**
  `rules.html:848-850` sends `{type:'schedule', cron, jitter_minutes, days}` with no
  `sink_id`/`input_id`; `app.py:1832-1838` (verified) indexes both unconditionally → `KeyError` →
  400. Even with those keys, the `Trigger(...)` call never passes `cron`/`jitter_minutes`/`days`,
  so a schedule rule would be created with `cron=""` and never fire. The schedule-rule UI (recent
  commit `a2afadc`) is dead on arrival — the create/update handlers were never extended.

- **[security] Arbitrary file write from user JSON in `POST /api/config/save`.**
  `app.py:984-1022`: `save_path = data.get("path")` is opened for write with
  attacker-influenceable YAML content. Any LAN client can overwrite any file writable by the server
  user. Restrict to a configured directory (or drop the `path` parameter).

- **[race] `render_frame()` is called concurrently from the Flask preview thread and the render
  loop on unlocked, stateful sources.** `app.py:1468` (preview endpoint, polled at 2 Hz by
  `virtual_sources.html:370-378`) vs. `router.py:748,855`. Stateful sources mutate shared arrays in
  `render()`: `FlamePattern._heat`, `SparklePattern._sparkle_values`, `FailingBulb._pixel_health`,
  `Lightning._strike_flashes`, VU/BarGraph peak state. Effects: animation advancing at 2×,
  `FailingBulb` decaying from previews, heat-array reallocation mid-frame when consumers have
  different pixel counts. Preview should return a cached last frame instead of re-rendering.

- **[inefficiency] Recursive glob of all system font directories on every frame for TTF text
  sources.** `text_source.py:297` → `fonts.py:817-818` → `find_ttf_font()` runs
  `glob.glob(".../**/*.ttf", recursive=True)` (`fonts.py:543-565`) with no caching — a full
  font-tree scan per frame per text/counter/clock source at 30 fps. Cache name→path once.

### Medium

- **[bug] `PUT/POST /api/virtual-sources` accepts unvalidated `output_dimensions`/`frame_rate`.**
  `app.py:1281-1288`: string/null elements raise `TypeError` → 500 (`_clamp_dimensions` at
  `base.py:39-41` does `max(1, d)` on non-ints); zero/negative dims pass the clamp → later
  `ZeroDivisionError`/`IndexError` on the render thread (`matrix_patterns.py:103,189,298,518`).
- **[bug] `GET /api/sinks/<id>/preview` misses the `sink_controller` None-check its siblings have
  (`app.py:1107`)** — 500 instead of 503.
- **[bug] Unvalidated enum parsing in route create/update** (`app.py:589,622`, `ScaleMode` via
  `RouteTransform.from_dict`) — unknown values return 500 instead of 400.
- **[bug] Source control/refresh endpoints leak `TimeoutError` as 500** (`app.py:157,169`) where
  sink equivalents return 504 (`app.py:204-225`).
- **[bug] `CounterSource` applies speed twice at the wrong scale.** `text_source.py:606-609`:
  `time_elapsed` is already speed-scaled, then multiplied by the raw 0-100 slider → default speed
  50 advances 50× the configured increment/second; 100 → 400×.
- **[bug] Speed-slider changes rescale the entire elapsed history** (`base.py:253-259`) — a source
  running 10 min jumps minutes of animation on slider move. The curve also gives 4.0 at 100, not
  the documented 10.0.
- **[bug] `rotate` 90/270 scrambles non-square matrix output.** `base.py:227-245`: `np.rot90`
  yields `(w,h)`, then `reshape(-1,3)` flattens in the new order while consumers still assume
  declared `width×height`.
- **[race] Dashboard detail panel rebuilt from a 5 s poll destroys in-progress edits**
  (`dashboard.html:1586-1592` replacing `detail-content.innerHTML`) — focus and unsent values lost
  every 5 s while a node is selected.
- **[bug] Dashboard bulk actions use a page-load snapshot** (`dashboard.html:1603,1620` using
  `rawRoutes`/`rawVS`) — Enable/Disable All silently skips routes created since load and posts IDs
  of deleted ones.
- **[security] Stored XSS via unescaped names in `innerHTML`** (`scenes.js:27-35`,
  `dashboard.html:299,311,336,344`, `rules.html:630-735`). Names are settable via unauthenticated
  API; `esc()` exists and is used elsewhere in the same files.
- **[inefficiency] Every pattern/matrix/monitor/visualizer render is a per-pixel Python loop with a
  fresh allocation per frame** (all of `patterns.py`, `matrix_patterns.py`, `monitors.py`,
  `effects.py`, `visualizers.py`; `MultiBar` re-parses gradient hex per pixel,
  `visualizers.py:371-373`). Dims are allowed up to 65536 px, where a frame takes seconds.

### Low

- `POST /api/virtual-sources/<id>/image` loads any server-side file path and exposes it via
  `/source-image` (`app.py:1410-1417`, `image_source.py:174-196`) — arbitrary file read.
- GET paint-buffer endpoint mutates state cross-thread (`app.py:446-448`).
- Preview page polls with no in-flight guard (`preview.html:147-161`) — out-of-order frames, request
  pile-up on slow links.
- `request.json` of literal `null` → `AttributeError` 500 in bulk endpoints (`app.py:653-655,1964-1966`).
- Jinja precedence bug in fill modal (`sinks.html:128`) — pixel count defaults wrong for sinks
  without `dim`; matrix sinks without `pixels` get 0.
- Lightning strike-probability roll is dead code and `dt` is hardcoded at 1/30
  (`effects.py:312-316,74`).
- `ImageSource` re-extracts + LANCZOS-resizes every frame even when static (`image_source.py:292-324`).
- Paint page sends the full framebuffer as "sparse" JSON on every stroke (`paint.html:500-511`).
- `VUMeter` renders nothing when `num_pixels < segments` (`visualizers.py:498-504`).

---

## 5. Media source pipeline

### High

- **[bug] `--audio-output` is structurally choppy.** `audio_playback.py:195-224`: each callback pops
  exactly one queued block — shorter blocks are zero-padded (silence inserted), longer blocks have
  their tail **discarded**. Video at 30 fps produces ~1470-sample blocks vs `blocksize=2048` →
  ~28% silence per callback, queue fills, `write()` drops blocks (320-321). Needs a
  remainder-carrying buffer.

- **[bug] Audio files longer than 10 minutes play silence.** `inputs/audio.py:169-171` only
  pre-decodes when duration < 600 s; the streaming fallback returns `None` unimplemented (280-281).
  Visualizers render black with only a debug-level log.

- **[bug] Non-looping audio-only playback terminates immediately.** `AudioFileInput.read_frame()`
  always returns `None` (255-257); the frame loops treat `None` + `not loop` as end-of-media
  (`source_group.py:404-408`, `multi_channel.py:861-865`) without consulting
  `context.is_audio_only` (`shared_context.py:194-200`), which exists for exactly this.

- **[bug] Pause/resume skips ahead by the pause duration for audio files.**
  `shared_context.py:382-383` returns early while paused, so `AudioFileInput._last_frame_time` goes
  stale; on resume `elapsed` covers the whole pause (`audio.py:230-237`). Pause 30 s at 1:00 →
  resume at 1:30. `GifInput` has the same stale-clock pattern (`gif.py:100-107`).

- **[bug] Plain-CLI `--audio-file`/`--microphone` modes are completely non-functional.**
  `cli.py:362-386` routes them into `MediaSource`, which never calls `set_shared_context()` and has
  no visualizer — the source advertises and sends nothing forever (`source.py:432-435`,
  `microphone.py:290-292`). Its `--audio-output` flag is parsed and never used (`cli.py:201-205`).

- **[bug] Multi-channel audio channels don't reconcile visualizer type with dimensions.**
  `multi_channel.py:252-257` lacks the linear/matrix substitution `AudioVisualizerSource` has
  (`sources/audio_visualizer.py:112-146`). Matrix dims + default linear `spectrum` → 16 pixels sent
  on a 256-pixel stream; matrix visualizer + 1-D dims → `ValueError` crashes `start()`.

- **[bug] `speed` control is a no-op for video, and runtime speed changes are a no-op everywhere.**
  `VideoInput` never uses `speed` (`video.py:195-245,379-381`); all pacing loops compute
  `frame_interval` once before the loop (`multi_channel.py:845`, `source_group.py:384`,
  `sources/base.py:369`, `source.py:399`). CONTROL_SET `speed=2.0` updates the reported control and
  changes nothing. Spec lists speed as supported (`spec/media-source-plan.md:351,648`).

### Medium

- **[bug] Microphone sample-rate fallback isn't propagated** (`microphone.py:238-241` vs. analyzers
  built with the configured rate, `source_group.py:266-282`) — at 48 kHz all FFT bin→Hz mappings
  are ~8.8% off.
- **[bug] Beat detection operates on the smoothed spectrum** (`audio/analyzer.py:67-70,253-274`,
  `beat_detector.py:222-237`) — high `smoothing` values low-pass transients to the running average
  and beat-driven visualizers go dead.
- **[bug] Multi-channel render loop ignores per-channel `rate`** (`multi_channel.py:870-903`) — one
  `min_interval` from the fastest channel; a 10 fps channel renders and transmits at 60 fps despite
  the negotiated SUBSCRIBE rate.
- **[bug] Configured control values never reach the control registry.** `_setup_controls()` runs
  before subclasses assign `_viz_config`/`_video_config`, so every `hasattr` guard is False at
  registration (`sources/audio_visualizer.py:172-227`, `sources/video_source.py:76,90`) —
  CONTROL_GET reports defaults; a syncing UI snaps the source back to defaults.
- **[bug] Frame loops have no drift correction** (`last_frame_time = now` pattern in all four
  loops) — sleep overshoot accumulates; video falls behind wall clock and speaker audio (~2 s/min
  under load).
- **[bug] Audio-extraction loop conditions compare frame counts against sample counts**
  (`video.py:263,285`) — under-feeds the ring buffer on ticks where a buffered frame was popped.
- **[inefficiency] Blocking PyAV demux/decode/resample inside `read_frame()` on the event loop,
  holding the context lock** (`video.py:247-320`, `shared_context.py:385-397`) — a slow disk stalls
  all channels and control handling.
- **[bug] GIFs with frame durations shorter than the render interval play in slow motion**
  (`gif.py:100-118` advances ≤1 frame and discards excess elapsed time) — a 50 fps GIF at 30 fps
  renders at 60% speed.
- **[inefficiency] Per-pixel Python loops in matrix visualizers** (`visualizers/matrix.py:181-195`
  spectrogram, 603-618 plasma, 268-282 ripples, 356-366 heatmap) — a 64×32 spectrogram at 30 fps is
  ~61k Python color calls/s on the shared loop.

### Low

- `get_spectrum_bands` declares/invalidates `_band_cache` but never writes it
  (`audio/analyzer.py:64,166,219-237`).
- `BeatDetector.update` computes and discards `variance`, converts the deque twice, and gates on
  wall-clock `time.time()` (`beat_detector.py:126,162-163`) — NTP steps can suppress/double beats.
- `SharedMediaContext.seek` notifies observers inside the lock, contradicting its own comment
  (`shared_context.py:362-370`).
- Subscribe failure leaks the created stream in all three source implementations
  (`multi_channel.py:317-326`, `sources/base.py:261-270`, `source.py:254-263`).
- `AudioRingBuffer.read_at_time` is dead code with an off-by-one (`audio_buffer.py:183`).
- Full-frame `.copy()` per tick for static images/GIFs (`image.py:79`, `gif.py:120`).
- `_decode_entire_file` never flushes the resampler and reads possibly-padded planes
  (`audio.py:196-215`).
- `cli_multi_channel.py` help advertises visualizer/color-mode names that don't exist in the
  registry (startup `ValueError` or silent rainbow fallback, `multi_channel.py:247-250`).
- Several visualizers normalize by the current frame's own max (SpectrumBars, LevelMeter) — output
  is amplitude-invariant, `gain` is cosmetic, and mic noise floor renders at full scale.

---

## 6. Serial sink + serial CLI + thermal source

### High

- **[race] Device-reset callback runs on the serial reader thread and performs synchronous serial
  queries — stalls I/O and leaks the port FD.** `device.py:890-897` (verified) invokes
  `_reset_callback()` from `_reader_loop`'s own thread; `v2_renderer.py:388-397` then calls
  `get_controls()`/`get_inputs()` and `sink.py:293-296` chains per-control `get_control()` — all
  block in `_wait_for_response`, which needs the reader thread that is currently stuck in this
  callback. Every query times out (~2 s × N); the first `LtpError` → `_close_device()` →
  `device.close()` calls `self._reader_thread.join()` *from the reader thread* →
  `RuntimeError: cannot join current thread`, swallowed at `v2_renderer.py:186-189`, so
  `self._serial.close()` never runs — FD leaked, sink stalled tens of seconds. **Fix:** dispatch
  reset handling to a worker thread; never run queries on the reader thread.

- **[race] No TX lock on the shared serial port.** `device.py:822-831` writes with no lock; the
  render thread writes pixel frames (`sink.py:660` → `v2_renderer.py:580`) while the asyncio thread
  writes SET_CONTROL/GET_CONTROL (`sink.py:448-553`). pyserial's POSIX write loops over partial
  `os.write` calls, so a multi-KB PIXEL_FRAME can interleave mid-stream with a control packet —
  corrupted framing whenever a control changes during streaming.

- **[protocol] The packet parser never validates the LENGTH field.** `protocol.py:373-378`
  (verified) accepts any 16-bit length (spec caps payload at 1024). Line noise corrupting a length
  high byte to 0xFF makes the parser wait for a ~65 KB phantom packet, swallowing every subsequent
  real response — all commands time out forever with no resync.

- **[bug] Any full frame smaller than the sink's pixel count is misclassified as a chunk and never
  rendered.** `sink.py:593-605` — same defect as §2's sink bug (cross-cutting item #2). A 64-px
  thermal source streaming to an 80-px strip leaves the strip black with no error.

### Medium

- **[protocol] No command correlation on responses.** `device.py:926-934,955-963`: any queued NAK
  fails the current request regardless of its failed-command byte; `get_status`/`get_stats`/
  `get_controls`/`get_build_info` all wait on the same `CMD_INFO_RESPONSE` — a stale duplicate
  INFO_RESPONSE from a `connect()` retry gets parsed by a later `get_build_info()` as garbage.
- **[protocol] A checksum failure stops delivery of already-buffered good packets.**
  `protocol.py:389-391` returns `None` on bad checksum, breaking `feed()`'s loop (353-357); the
  real response sitting behind the bad packet isn't parsed until more bytes arrive.
- **[bug] Reader thread dies on any non-serial exception.** `device.py:874-882` (verified) catches
  only `OSError`/`SerialException`; `_parse_hello` raises `LtpProtocolError` on a truncated HELLO
  (983) → thread dies, `is_connected` stays True, idle sink never reconnects.
- **[race] Serial disconnect is invisible while idle** — reader breaks but `is_open` stays True
  until `close()` (`v2_renderer.py:179-181`, `sink.py:705`); recovery only on next write failure.
- **[inefficiency/bug] Blocking serial I/O on the asyncio loop thread.** `sink.py:713-722`
  (`renderer.open()` ≈ 8 s worst case) and `sink.py:448-529` (set + confirm round trip per control)
  run in-loop — device unplug freezes UDP reception and all TCP clients for seconds per backoff.
- **[inefficiency] ~100 ms latency per synchronous command** — `_reader_loop` reads
  `self._serial.read(256)` under a 0.1 s timeout (`device.py:298,875`); should read
  `max(1, in_waiting)`.
- **[bug] Chunk reassembly ignores `packet.sequence`** (`sink.py:591-604`) — lost final chunk
  stalls the frame; consecutive frames blend; reordered last chunk submits stale interior data.
- **[bug] `_response_queue` grows without bound** (`device.py:914-917`) — frame-ack is enabled but
  never disabled (`v2_renderer.py:252-253`), so a device with persisted `frame_ack=on` queues one
  FRAME_ACK per SHOW forever.
- **[protocol] No frame fragmentation on send; payload cap disagrees with spec.**
  `v2_renderer.py:577-580` sends the whole strip as one PIXEL_FRAME; spec chunks above ~340 px and
  caps payloads at 1024 bytes vs. code's `LTP_MAX_PAYLOAD=4096` (`protocol.py:14`) — small-buffer
  devices NAK every frame; >1365 px raises `ValueError` per frame.

### Low

- `get_control` doesn't sign-extend INT16/INT8 (`device.py:553-558`), inconsistent with
  `get_controls` (614-631).
- Input event values coerced to `bool(data[0])` (`v2_renderer.py:340`) — destroys encoder deltas
  and analog values.
- `set_reset_callback` only installed on the reconnect path (`sink.py:718` vs. 799-803);
  `_setup_device_controls` runs twice per reconnect (721-722 duplicates `_update_from_device`).
- Test pattern assigns RGB rows into RGBW buffers → broadcast `ValueError`, renders nothing
  (`sink.py:678-682`).
- AMG8833 thermistor decoded as two's complement; datasheet is sign-magnitude
  (`amg8833.py:136-139`; currently unused).
- Resync pops noise one byte at a time — O(n²) (`protocol.py:364-365`); per-pixel loops in
  `palettes.py:89-93` and the per-packet test-pattern regeneration (`sink.py:675-684`).

---

## 7. Arduino firmware

Reviewed: LtpProtocol library, ltp_serial_v2, ltp_328p_dual, ltp_328p_pwm, ltp_apa102_strip,
ltp_octo_v2, ltp_esp32_ring, ltp_esp32c3_oled. Critical items independently re-verified against the
code. The AVR handlers are heavily copy-pasted, so most bugs recur in 3-5 sketches; they are
reported once with all locations.

### Critical

- **[memory] 16-bit arithmetic wrap defeats the PIXEL_FRAME bounds checks on all AVR sketches.**
  `ltp_serial_v2.ino:1093-1113`, `ltp_328p_dual.ino:1291-1301`, `ltp_328p_pwm.ino:938-956`
  (verified). `expectedBytes = count * bpp`, `dataOffset + expectedBytes`, and `start + count` are
  all `uint16_t`. A crafted `start=43850, count=21846` gives `expectedBytes=2` (passes the length
  check with a ~12-byte payload) and `start+count=160` (passes the range check); the copy loop then
  reads `pixelData[i*3]` up to offset 65535 — sweeping the entire AVR address space including
  memory-mapped I/O registers with read side effects (e.g. UDR0 consumes RX bytes) — and in
  serial_v2 writes garbage into pixels 0-159. (apa102/octo on 32-bit Teensy are immune.) **Fix:** a
  shared `validatePixelRange(start, count, max)` using 32-bit intermediates in the LtpProtocol
  library.

- **[memory] GET_PIXELS range check wraps/underflows → out-of-bounds RAM streamed to the host.**
  `ltp_serial_v2.ino:1370-1393`, `ltp_328p_pwm.ino:1128-1148`, `ltp_328p_dual.ino:1511-1543`
  (verified). `if (count==0) count = N - start` underflows when `start > N`, and `start+count > N`
  wraps, so the guard passes; the code then streams `count*bpp` bytes from past the pixel buffer.
  pwm has **no clamp at all**: `start=100, numChannels=8` → `count=65444` → ~196 KB of device RAM
  streamed over serial in a checksum-valid packet. An unauthenticated RAM info-leak. (dual's
  `maxRespPixels=83` clamp limits it to ~249 bytes but still from a wrapped pointer, and its matrix
  path calls `mapPixel(idx >= numPixels)`.)

- **[race/protocol] Remote null-pointer panic via `control_get` with a non-string id (both ESP32
  sketches).** `ltp_esp32_ring/sink_protocol.h:360-364,441` and
  `ltp_esp32c3_oled/sink_protocol.h:331-335,396` (verified). A TCP client sends
  `{"type":"control_get","ids":[1]}`; ArduinoJson returns `nullptr` from `id.as<const char*>()` for
  a numeric element, passed into `getControlValue()` → `strcmp(nullptr, "brightness")` → null deref
  → LoadProhibited panic and reboot. Repeatable unauthenticated LAN DoS.

### High

- **[logic] `CTRL_ID_REBOOT` uses `wdt_enable()` on AVR → reset loop on old-bootloader Nanos.**
  `ltp_serial_v2.ino:1285-1287`. The project's own fix (`asm volatile ("jmp 0")`, used by
  `CMD_RESET` at line 1417 and both 328p sketches) was not applied here; the watchdog fires during
  the bootloader's ~2 s delay, looping until power-cycled. This is a known-hazard the memory notes
  explicitly warn against.

- **[logic] Octo `getPixelColor()` reads the DMA waveform buffer as packed RGB.**
  `ltp_octo_v2/led_driver_octo.h:206-209`. `octoDrawingMemory` is OctoWS2811's bit-interleaved DMA
  buffer, not `0xRRGGBB`; readback must use `leds.getPixel(n)`. GET_PIXELS returns garbage, and
  every fade-based effect (Cylon, Sparkle) reads garbage, fades it, and writes it back.

- **[memory] Use-after-free of a temporary `String` in mDNS TXT records.**
  `ltp_esp32_ring/wifi_transport.h:154`: `txt.add("dim", String(pixels).c_str())` — the temporary
  is destroyed at end of statement but the pointer is stored and dereferenced later in
  `txt.apply()`, reading freed heap.

- **[pwm-specific timing] Fast PWM presets break the library's raw-`millis()` inter-byte timeout.**
  `ltp_328p_pwm.ino:198-207`. The sketch corrects its own timing via `correctedMillis()`, but
  `LtpProtocol::processInput()` uses raw `millis() - lastByteTime > 10ms`. At /8 presets Timer0
  overflows 4-8× faster, shrinking the real timeout to 1.25-2.5 ms; any packet split across USB
  chunks (e.g. a 186-byte frame split by FTDI latency) is reset mid-parse and dropped — persisted
  to EEPROM, so large frames never work at those presets across reboots.

### Medium

- **[logic] Idle timer not reset by SET_ALL / SET_RANGE / SUBMATRIX** — only `handleShow`/
  `handlePixelFrame` call `resetActivityTimer()` (`ltp_serial_v2.ino:1016-1213`,
  `ltp_apa102_strip.ino:1014-1052`, `ltp_octo_v2.ino:919-1157`). A host animating exclusively via
  those commands with `auto_show` on gets blanked mid-stream.
- **[logic] Brightness applied twice (quadratic dimming, broken fades).**
  `ltp_serial_v2/led_driver_lpd8806.h:95-112`, `led_driver_apa102.h:85,109-111`. `setPixel` bakes
  brightness into the buffer and `getPixel` returns the scaled value, so read-modify-write fades
  re-multiply each frame → trails collapse to black. Brightness should be applied only at `show()`.
- **[protocol] GET_PIXELS returns raw native driver bytes, not decoded RGB**
  (`ltp_serial_v2.ino:1392-1395`) — LPD8806 GRB wire bytes / APA102 stride mismatch; readback never
  matches what was written.
- **[protocol] UINT16 SET_CONTROLs silently ACK a too-short value.** `ltp_serial_v2.ino:1238-1255`,
  `ltp_octo_v2.ino:1183-1217`, both 328p sketches (`ltp_328p_dual.ino:1343-1379`,
  `ltp_328p_pwm.ino:990-1023`) — `if (length >= 3)` skips the assignment but falls through to
  `sendAck`; should NAK `ERR_INVALID_LENGTH`.
- **[protocol] INFO_CONTROLS streamed response omits `FLAG_RESPONSE`** — `streamBegin` hardcodes
  `flags=0` (`ltp_serial_v2.ino:756`); a host validating the RESPONSE bit drops the reply. (328p
  sketches set it correctly.)
- **[protocol] `sendInputEvent` transmits one uninitialized byte past the name**
  (`ltp_apa102_strip.ino:493-505`, `ltp_328p_dual.ino:626-636`) — packet length is `7+nameLen` but
  only `6+nameLen` bytes are written.
- **[memory] `handleGetPixels` heap-allocates up to 512 bytes on a 2 KB ATmega328P**
  (`ltp_serial_v2.ino:1380`, `new uint8_t[...]`) — risks malloc failure / heap-stack collision;
  should stream like INFO_CONTROLS.
- **[memory] Unbounded `strncpy` into config with no forced terminator (ESP32 ring)**
  (`ltp_esp32_ring.ino:112-113,523-537`).
- **[logic] Config `version` read but never validated on both ESP32 sketches**
  (`ltp_esp32_ring.ino:82-90`, `ltp_esp32c3_oled.ino:74-82`) — a `CONFIG_VERSION` bump silently
  loads stale NVS values. (AVR sketches check the version correctly.)
- **[logic] `setChannel` truncates the wrapped index to uint8, aliasing OOB reads back into live
  channels** (`ltp_328p_pwm.ino:380-383`) — visible output flicker on top of the OOB read.
- **[protocol] `sendInputEvent` sends one byte more than it writes (328p_dual)**
  (`ltp_328p_dual.ino:626,634-636`) — uninitialized stack byte in a checksum-valid packet.

### Low

- `resetConfig()` omits `cycleTime` (`ltp_serial_v2.ino:255-266`, apa102, octo) — factory reset
  persists a possibly-bad cycle time.
- `cycle_time=0` not range-checked → strobing (`ltp_octo_v2.ino:1213-1217`).
- HELLO vs INFO_ALL advertise different capability bytes (`ltp_serial_v2.ino:742 vs 882`,
  `ltp_octo_v2.ino:680-730`) — a host querying INFO_ALL thinks the device can't persist config.
- millis() rollover via absolute-deadline comparison (`ltp_serial_v2.ino:659` mitosis) — freezes
  after ~49.7 days.
- Dead controls: gamma and status_interval advertised/persisted but never applied
  (`ltp_serial_v2.ino:1229-1255`, apa102).
- Fire effect uint8 overflow: saturation check is dead code and the hottest pixel's blue wraps to 0
  (`ltp_328p_dual.ino:787-799`, `ltp_octo_v2.ino:497-500`) — visual only.
- Octo matrix fire heat buffer hardcoded `heat[60*16]`, exactly full at `PIXELS_PER_STRIP=120`
  (`ltp_octo_v2.ino:433`) — raising that config value silently overflows RAM; add a `static_assert`.
- Telnet IAC handling broken on xtensa (`char` unsigned) desyncs real telnet clients
  (`ltp_esp32_ring/telnet_server.h:132-139`).
- Single-input GET_INPUT truncates the name to 4 bytes ("Butt") (`ltp_apa102_strip.ino:987-990`,
  `ltp_328p_dual.ino:1212-1220`).
- `handlePixelSetRange` doesn't validate `start` (`ltp_apa102_strip.ino:1036-1042`).
- OLED explicitly draws dark pixels after `clearBuffer()` (`ltp_esp32c3_oled/oled_driver.h:60-110`)
  — roughly doubles render CPU.
- ESP32-C3 UDP `receive()` drains in an unbounded `while(true)` (`udp_receiver.h:101-109`) — a UDP
  flood can starve `loop()`.
- ESP32-C3 wake-from-idle redraws the info screen over a live stream (`ltp_esp32c3_oled.ino:573-575`).

### Verified-clean (checked, deliberately not flagged)

PROGMEM access is correct throughout the AVR sketches (`pgm_read_byte/word/ptr`, `strlen_P`,
`strncpy_P`); `sendInfoControls` two-pass length matches the streamed bytes; 328p reboot paths use
`jmp 0`; all `millis()` comparisons except the mitosis one use rollover-safe subtraction; ESP32
`udp_receiver` bounds every length against the received size; both ESP32 sketches poll WiFi/UDP/touch
from `loop()` with no callback/ISR pixel-buffer races. **The `ltp_328p_dual` sketch is the cleanest
of the set** (matrix divide-by-zero guard, validated `matrix_width`, version check, `jmp 0` reboot,
correct PROGMEM) and reads like the reference the others should converge on.

---

## 8. Recommended priorities

1. **Controller stability (critical):** guard `_schedule_loop`/`_monitor_loop` with top-level
   exception handling and restart; add an unconditional yield to `SequenceManager._run`; marshal
   *all* Flask-thread mutations through `run_async`.
2. **Fix the frame/chunk wire format** (frame-total or fragment flag) and extract one shared
   `FrameAssembler` — fixes "small source never renders" in three sinks at once.
3. **Serial-device thread safety:** TX lock, reset-callback dispatch off the reader thread, LENGTH
   validation with resync.
4. **Web regressions:** scene save (`transform.mirror`), schedule-rule create/update payload, input
   validation on VS endpoints, `/api/config/save` path restriction.
5. **Controls layer:** pydantic `populate_by_name` + `by_alias=True` round-trip fix.
6. **Media pipeline:** audio playback rebuffering, >10-min files, pause/resume clock, and
   consolidating the three copies of the source machinery.
7. **Firmware memory safety (critical):** add a 32-bit `validatePixelRange(start, count, max)` helper
   in the LtpProtocol library and route every AVR PIXEL_FRAME / GET_PIXELS handler through it; fix
   the ESP32 `control_get` null-deref; switch `CTRL_ID_REBOOT` in serial_v2 to `jmp 0`.
8. **Performance sweep:** vectorize per-pixel render loops; remove per-frame `tolist()`, font
   globbing, and blocking calls from loop threads.
