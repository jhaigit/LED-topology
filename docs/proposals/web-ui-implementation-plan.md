# Web UI Improvements: Implementation Plan

## Architecture Overview

The current stack is:
- **Backend**: Flask running in a daemon thread, using `run_async()` to bridge to the main asyncio event loop for device communication. A single `app.py` file (~1506 lines) contains all routes and API endpoints.
- **Frontend**: Vanilla HTML/CSS/JS (no framework), Jinja2 templates. One shared `app.js` (~103 lines) provides `apiCall()`, `showToast()`, `confirmAction()`, and Save Config. Each template embeds its own `<script>` and `<style>` blocks inline. No module system, no bundler.
- **Communication**: REST with Fetch, multiple independent `setInterval` polling loops (100ms for preview images, 1s for routes/sensors, 2s for inputs, 5s for device status).
- **Styling**: CSS custom properties in a single `style.css` (~614 lines). Dark theme only. 768px responsive breakpoint.

The plan is organized into four phases, matching the proposal's effort/dependency grouping. Each step identifies exact files, new endpoints, JS modules, CSS additions, and template changes.

---

## Phase 1: Quick Wins (No Infrastructure Changes)

### 1A. Dashboard Improvements

**Goal**: System diagram, activity feed, performance metrics, quick actions.

**Backend changes** (`app.py`):
1. Add `GET /api/status/extended` endpoint that returns enriched status:
   - Per-route performance metrics: `frames_routed`, `frames_per_second` (computed from `_frames_routed` delta over time), `last_frame_time`.
   - Recent activity events (keep an in-memory ring buffer of the last 50 events). Define an `ActivityEvent` dataclass with `timestamp`, `event_type` (device_online, device_offline, route_error, rule_triggered, config_saved), `message`, `entity_id`. Hook into: `controller` device callbacks, `rule_engine.on_trigger`, route status changes.
   - Add a new `_activity_log: list[dict]` list on the app config, populated by a helper `log_activity(event_type, message, entity_id)` called from relevant existing handlers.
2. Add `POST /api/routes/bulk` endpoint for bulk enable/disable all routes.
3. Add `POST /api/virtual-sources/bulk` endpoint for bulk start/stop all virtual sources.

**Template changes** (`dashboard.html`):
1. Add a fourth stat card: "Virtual Sources" count (running/total), and optionally "Rules" (active/total).
2. Add a "Quick Actions" panel below the stats grid with buttons: "Enable All Routes", "Disable All Routes", "Start All Virtual Sources", "Stop All Virtual Sources". Each calls its bulk endpoint and shows a toast.
3. Add an "Activity Feed" panel: a `<ul id="activity-feed">` populated via JS from `/api/status/extended`. Show the last 20 events with relative timestamps ("2 min ago"). Auto-refresh every 5 seconds alongside existing status polling.
4. Add a "Route Performance" panel: a small table showing each route name, FPS, and frame count. Data comes from `/api/status/extended`.
5. Add a mini system diagram (simplified): render sources on the left, sinks on the right, and lines between them for active routes, using inline SVG built in JS. This is a simplified precursor to the full visual route builder (Phase 3). Use `/api/routes` data (which already includes `source_name` and `sink_name` via `_enrich_route`).

**CSS additions** (`style.css`):
- `.quick-actions` panel with horizontal button row.
- `.activity-feed` list styling (timestamps right-aligned, event type badges).
- `.mini-diagram svg` sizing constraints.

**JS** (inline in `dashboard.html`):
- `fetchExtendedStatus()` function replacing the existing `updateStatus()`.
- `renderMiniDiagram()` function that draws a simple SVG from routes data.
- Quick action button handlers.

**Dependencies**: None. Pure frontend additions with minor backend enrichment.

---

### 1B. Search, Filter, and Bulk Operations

**Goal**: Search bars, tags, multi-select, sort on all list pages.

**New JS module**: Create `static/js/list-tools.js` as a reusable module providing:
- `createSearchBar(containerId, filterFn)` — injects a search input that calls `filterFn` on each keystroke to show/hide cards or table rows.
- `createSortButtons(containerId, sortFns)` — sort by name, status, date.
- `createBulkSelectUI(containerId, onBulkAction)` — "Select All" checkbox in table headers, per-row checkboxes, action bar that appears when items are selected.
- Event delegation on `.device-grid` and `.table` containers.

**Template changes**:
1. `sources.html`, `sinks.html`, `virtual_sources.html`, `scalar_sources.html`:
   - Add a `.list-toolbar` div above the device grid containing a search input and sort dropdown.
   - Each `.device-card` gets a `data-name`, `data-status`, `data-type` attribute for filtering.
   - Filtering is pure client-side: hide cards where no attribute matches the search string.

2. `routes.html`, `rules.html`:
   - Add search input above the table.
   - Add checkboxes column to tables.
   - Add a `.bulk-actions-bar` that appears when checkboxes are selected: "Enable Selected", "Disable Selected", "Delete Selected".

**Backend changes** (`app.py`):
- Add `POST /api/routes/bulk-action` endpoint: accepts `{action: "enable"|"disable"|"delete", ids: [...]}`.
- Add `POST /api/rules/bulk-action` endpoint: same pattern.

**CSS additions** (`style.css`):
- `.list-toolbar` flex row with search input and sort dropdown.
- `.bulk-actions-bar` sticky bar styling.
- `.device-card.hidden` with `display: none`.
- Checkbox styling for table rows.

**Dependencies**: None. Loads after `app.js` in `base.html` via a new `<script>` tag.

---

### 1C. Theming and Accessibility

**Goal**: Light theme toggle, WCAG AA contrast, aria labels, keyboard navigation.

**CSS changes** (`style.css`):
1. Define a `[data-theme="light"]` selector block that overrides all CSS custom properties:
   ```css
   [data-theme="light"] {
       --bg-primary: #f5f5f5;
       --bg-secondary: #ffffff;
       --bg-card: #e8eaf6;
       --text-primary: #212121;
       --text-secondary: #616161;
       --accent: #c62828;
       --accent-hover: #e53935;
       --border: #e0e0e0;
       /* ... etc */
   }
   ```
2. Add `:focus-visible` outline styles for all interactive elements (buttons, links, inputs, select) — currently missing.
3. Ensure all status colors have sufficient contrast against their backgrounds.

**Template changes** (`base.html`):
1. Add a theme toggle button in `.navbar-actions` (a sun/moon icon button). On click, toggle `data-theme` attribute on `<html>` element and store preference in `localStorage`.
2. Add `aria-label` attributes to all icon-only buttons (close button `&times;`, status dots, etc.).
3. Add `role="status"` and `aria-live="polite"` to the toast container so screen readers announce notifications.
4. Add `role="dialog"` and `aria-modal="true"` to all `.modal` elements.

**JS changes** (`app.js`):
1. Add theme initialization: read from `localStorage`, apply on page load, optionally auto-detect with `prefers-color-scheme` media query.
2. Add `toggleTheme()` function.
3. Add keyboard trap for modals (Tab cycles within modal when open, Escape closes).

**Dependencies**: None.

---

## Phase 2: Medium Effort Features (No New Infrastructure)

### 2A. Paint Tool Enhancements

**Goal**: Undo/redo, eyedropper, keyboard shortcuts, zoom/pan.

**Template changes** (`paint.html`):
1. Add undo/redo buttons to toolbar.
2. Add "Eyedropper" option to the tool selector dropdown.
3. Add zoom controls (+/- buttons and a percentage display).
4. Add a keyboard shortcuts help tooltip (?) button.

**JS changes** (inline in `paint.html`, refactored):
The paint JS is already ~450 lines inline. It should be extracted to `static/js/paint.js` for maintainability. New features:

1. **Undo/Redo Stack**:
   - Maintain `undoStack: []` and `redoStack: []` of pixel snapshots.
   - Before each mutation (mouse down/draw/text/image/clear), push a deep copy of `pixels` array onto `undoStack` (limit to 50 entries).
   - `undo()`: pop from undoStack, push current state to redoStack, restore pixels, redraw, send to sink.
   - `redo()`: pop from redoStack, push current state to undoStack, restore, redraw, send.
   - Bind Ctrl+Z to undo, Ctrl+Shift+Z / Ctrl+Y to redo.

2. **Eyedropper Tool**:
   - When selected and user clicks canvas, read the pixel color from the `pixels[]` array at the clicked coordinate.
   - Convert RGB to hex and set `#paintColor` input value.
   - Switch cursor to `crosshair`.

3. **Keyboard Shortcuts**:
   - Listen for keydown events on the document (not when an input is focused).
   - B = brush (pixel), L = line, F = fill, E = eyedropper, T = text, I = image.
   - Update the tool dropdown selection and call `onToolChange()`.

4. **Zoom and Pan**:
   - Add a `zoomLevel` state variable (default 1.0, range 0.25 to 4.0).
   - Multiply `pixelSize` by `zoomLevel` when calculating canvas dimensions and rendering.
   - +/- buttons and mousewheel on canvas adjust `zoomLevel`, then call `resizeCanvas()` and `drawCanvas()`.
   - When zoomed in beyond viewport, the `.paint-canvas-container` already has `overflow-x: auto`. Add `overflow-y: auto` as well.
   - Pan via middle-mouse-drag or shift+drag: translate the canvas container scroll position.

5. **Animation Timeline** (stretch goal):
   - Add a "Frames" panel below the canvas: a list of frame thumbnails.
   - "Add Frame" duplicates current pixels into a frames array. "Delete Frame" removes it.
   - A play button iterates through frames, sending each to the sink at a configurable FPS.
   - Backend addition: `POST /api/sinks/<sink_id>/paint/sequence` that accepts `{frames: [...], fps: 10, loop: true}` and runs a background task pushing frames.

**CSS additions**: Zoom button styling, eyedropper cursor, undo/redo button icons, keyboard shortcut overlay.

**Dependencies**: None on backend for core features. Animation timeline needs a new endpoint.

---

### 2B. Configuration Scenes

**Goal**: Named scenes, quick-switch, export/import.

**Backend changes** (`app.py`):

1. Define scene data structure. A scene is a snapshot of:
   - `routes`: list of `{route_id, enabled, transform}` (only mutable state, not the route definition itself).
   - `virtual_sources`: list of `{id, enabled, control_values}`.
   - `rules`: list of `{id, enabled}`.

2. Add an in-memory `_scenes: dict[str, dict]` storage on the app config, persisted to the config YAML file under a `scenes:` key.

3. New API endpoints:
   - `GET /api/scenes` — list all scenes (name, id, created_at, description).
   - `POST /api/scenes` — create a scene: captures current state snapshot. Body: `{name, description}`.
   - `GET /api/scenes/<scene_id>` — get scene details.
   - `PUT /api/scenes/<scene_id>` — update scene metadata (name, description).
   - `DELETE /api/scenes/<scene_id>` — delete a scene.
   - `POST /api/scenes/<scene_id>/activate` — apply scene: iterate through its route/vs/rule state and apply each.
   - `GET /api/scenes/<scene_id>/export` — export scene as YAML download.
   - `POST /api/scenes/import` — import a scene from uploaded YAML.
   - `POST /api/scenes/<scene_id>/update-snapshot` — re-snapshot current state into existing scene.

4. Modify `api_config_save` to include the scenes list in the YAML file under `scenes:`.

5. Modify config loading in `cli.py` to restore scenes from the `scenes:` key.

**Frontend changes**:
1. Add a "Scenes" dropdown or panel in the navbar (inside `.navbar-actions`, next to Save Config button). Shows a dropdown list of saved scenes with "Activate" action on each.
2. Create `static/js/scenes.js` module:
   - `loadScenes()` fetches from `/api/scenes`, populates the dropdown.
   - `saveScene()` prompts for name via a small modal, POSTs to `/api/scenes`.
   - `activateScene(id)` POSTs to `/api/scenes/<id>/activate`, shows toast, refreshes page.
3. On the Dashboard, add a "Scenes" quick-switch panel showing scene buttons.

**Template changes** (`base.html`): Add scene dropdown UI and import `scenes.js`.

**CSS additions**: Scene dropdown styling, active-scene indicator badge.

**Dependencies**: Requires the existing config save infrastructure. No external dependencies.

---

## Phase 3: Infrastructure + High-Value Features

### 3A. WebSocket Real-Time Updates

**Goal**: Replace polling with push-based updates where beneficial.

**Architectural decision**: Use `flask-sock` (lightweight WebSocket for Flask). It works with the existing `app.run(threaded=True)` model. `flask-socketio` was considered but requires `eventlet` or `gevent`, which would conflict with the existing asyncio event loop.

**Backend changes** (`app.py`):

1. Add WebSocket endpoint:
   ```python
   from flask_sock import Sock
   sock = Sock(app)

   @sock.route('/ws')
   def ws_handler(ws):
       # Register this client for updates
       # Send initial state
       # Loop reading messages (for client->server commands)
   ```

2. Create a new module `web/ws_manager.py` — a `WebSocketManager` class:
   - Maintains a set of connected clients.
   - Provides `broadcast(event_type, data)` method that serializes and sends to all clients.
   - Handles client disconnection gracefully.
   - Thread-safe (Flask runs in its own thread, asyncio loop on the main thread).

3. Hook broadcast calls into existing subsystems:
   - **Device status**: Controller mDNS discovery detects online/offline transitions → `ws_manager.broadcast("device_status", {...})`.
   - **Route status**: `RoutingEngine` changes route status → broadcast `route_update`.
   - **Frame data**: Routing engine frame callback → broadcast `frame_data` with pixel array. Throttle to max 15 FPS per route.
   - **Scalar source samples**: Scalar source produces sample → broadcast `sensor_data`.
   - **Rule triggers**: `RuleEngine` fires a rule → broadcast `rule_triggered`.
   - **Control changes**: Control set via API → broadcast `control_changed`.

4. WS protocol — simple JSON messages:
   ```json
   {"type": "device_status", "data": {"id": "...", "online": true}}
   {"type": "route_update", "data": {"id": "...", "status": "connected", "frames_routed": 500}}
   {"type": "frame_data", "route_id": "...", "pixels": [[r,g,b], ...]}
   {"type": "sensor_data", "source_id": "...", "values": [...]}
   ```

5. Client-to-server subscription messages:
   ```json
   {"subscribe": "frames", "route_id": "..."}
   {"unsubscribe": "frames", "route_id": "..."}
   ```

**Frontend changes** (`app.js`):
1. Create `static/js/ws-client.js`:
   - `class LtpWebSocket` that manages the WebSocket connection with auto-reconnect.
   - `connect()`, `disconnect()`, `send(msg)`.
   - `on(eventType, callback)` registration for event handlers.
   - Exponential backoff reconnect (1s, 2s, 4s, max 30s).
   - Heartbeat ping every 30s to keep connection alive.

2. Each page's inline JS registers its own handlers:
   - Dashboard: listen for `device_status`, `route_update`, `rule_triggered`.
   - Sources/Sinks: listen for `device_status` to toggle online/offline.
   - Routes: listen for `route_update` to update status badges and frame counts.
   - Rules: listen for `rule_triggered` to update trigger counts.
   - Scalar sources: listen for `sensor_data` to update channel bars.

3. Remove `setInterval` polling loops from templates. The WS client is the single source of real-time updates.

4. **Fallback**: If WebSocket connection fails, fall back to polling (keep existing polling code guarded by a flag).

**Template changes** (`base.html`): Add `<script src="...ws-client.js">` before page-specific scripts. Initialize the global `window.ltpWS = new LtpWebSocket()` connection.

**New dependency**: `flask-sock>=0.7.0` added to pyproject.toml `[controller]` extras.

---

### 3B. Live Preview Overhaul

**Goal**: Canvas rendering, WebSocket frames, auto-scale, fullscreen.

**Template changes** (`preview.html`): Major rewrite.
1. Replace `<img>` SVG preview with `<canvas>` elements, one per route.
2. Each canvas auto-sizes LED pixel dimensions based on the route's source dimensions and available viewport width.
3. Add fullscreen button per preview card (uses Fullscreen API).
4. Add FPS counter display per route.

**New JS module**: `static/js/led-canvas.js`:
1. `class LedCanvas`:
   - Constructor takes a canvas element and dimensions (width, height).
   - `render(pixels)` draws pixel data onto the canvas with 1px gap between LEDs.
   - Auto-computes pixel size from canvas container width and LED count.
   - For 1D strips: single row of rectangles. For 2D matrices: grid of rectangles.
   - `requestAnimationFrame`-based rendering (only redraws when new data arrives).

2. `enterFullscreen(canvasContainer)` — puts a container element into fullscreen mode.

3. Custom LED layout maps (stretch): accept a JSON XY coordinate list for non-grid arrangements.

**Integration with WebSocket** (depends on 3A):
1. On the preview page, for each route, send `{"subscribe": "frames", "route_id": "..."}`.
2. On `frame_data` events, call `ledCanvas.render(pixels)`.
3. Track FPS: count frames received per second, display in the preview card header.

**Backend changes** (`app.py`):
1. Add `GET /api/routes/<route_id>/frame` — returns the last frame as JSON (REST fallback for non-WebSocket clients):
   ```json
   {"pixels": [[r,g,b], ...], "dimensions": [w, h], "timestamp": 1234567890}
   ```
2. Existing SVG preview endpoints remain for backward compatibility.

**CSS additions**: Fullscreen canvas styling, FPS counter badge, preview layout improvements.

**Dependencies**: Phase 3A (WebSocket) for real-time frames. Without it, falls back to polling the JSON frame endpoint.

---

### 3C. Visual Route Builder

**Goal**: Node-graph view with drag-to-connect.

**New JS module**: `static/js/route-graph.js` (estimated 600-800 lines):

1. **Canvas-based node graph renderer**:
   - Sources (physical + virtual) rendered as boxes on the left side.
   - Sinks rendered as boxes on the right side.
   - Routes rendered as curved bezier lines (cables) connecting source boxes to sink boxes.
   - Cable colors: green for active/connected, yellow for connecting, gray for disabled, red for error.
   - Frame count labels on cables.
   - Boxes show device name, online status (dot), and dimension info.

2. **Interaction**:
   - Click a source box output port and drag to a sink box input port to create a route (opens a simplified create-route modal for name and transform settings).
   - Click a cable to select it, showing an edit panel for transform settings (brightness, scale mode).
   - Right-click a cable for a context menu: Enable/Disable/Delete.
   - Drag boxes to rearrange layout (positions saved in `localStorage`).

3. **Layout algorithm**:
   - Default layout: sources sorted alphabetically on left, sinks on right, with vertical spacing.
   - Auto-layout if positions not saved.

**Template changes** (`routes.html`):
1. Add a view toggle: "List View" (existing table) / "Graph View" (new canvas).
2. Graph view container: `<div id="route-graph-container"><canvas id="routeGraphCanvas"></canvas></div>`.
3. Side panel for editing selected route transform (slides in from right when a cable is clicked).

**Backend changes**: None required. Uses existing route CRUD APIs entirely. The graph is client-side only.

**CSS additions**: Graph container, view toggle button group, side edit panel.

**Dependencies**: None required. Can optionally use WebSocket (3A) for live status updates on cables.

---

## Phase 4: Polish and Progressive Enhancement

### 4A. Unified Device Page

**Goal**: Consolidate Sources, Sinks, Virtual Sources into a single "Devices" page.

**Template changes**:
1. Create `templates/devices.html` — new unified page.
2. Contains a tab bar: "Physical Sources | Sinks | Virtual Sources | Sensors".
3. Each tab renders its respective device grid (reusing existing card layout extracted into Jinja2 `{% include %}` partials or macros).
4. Clicking a device card opens a slide-in detail panel on the right with:
   - Full device details (address, ID, build info).
   - Controls (editable, same as current).
   - Routes this device participates in (fetched from `/api/routes`, filtered client-side).
   - Live preview canvas (for sources/sinks that have one).

**Backend changes** (`app.py`):
1. Add `@app.route("/devices")` page route that passes all device types to the template.
2. Keep existing individual routes (`/sources`, `/sinks`, etc.) as redirects or alternative views for bookmarking.

**Template refactoring**:
- Extract device card rendering into `templates/partials/device_card.html` and `templates/partials/control_editor.html` Jinja2 includes, shared by old individual pages and the new unified page.

**Navbar changes** (`base.html`):
- Reduce to: Dashboard | Devices | Routes | Rules | Paint.
- Keep old URLs working via redirects.

**CSS additions**: Tab bar styling, slide-in detail panel (position fixed right, animated transform).

**Dependencies**: None.

---

### 4B. Mobile and Touch

**Goal**: Bottom nav, swipe gestures, PWA.

**CSS changes** (`style.css`):
1. At `@media (max-width: 768px)`:
   - Hide the top navbar links.
   - Show a bottom fixed navigation bar with 5 icons (Dashboard, Devices, Routes, Rules, Paint).
   - Increase touch target sizes for all buttons to minimum 44x44px.
   - Make control sliders larger (`height: 36px`).

**Template changes** (`base.html`):
1. Add a `<nav class="bottom-nav">` at the end of body, hidden on desktop via CSS.
2. Add PWA manifest link: `<link rel="manifest" href="/manifest.json">`.
3. Add a theme-color meta tag.

**New static files**:
1. `static/manifest.json` — PWA manifest with app name, icons, start_url, display mode.
2. `static/js/service-worker.js` — basic service worker for offline caching of static assets.
3. `static/icons/` — app icons at 192x192 and 512x512.

**JS changes**:
- Register service worker in `app.js`.
- Add touch event handling for paint canvas (touchstart, touchmove, touchend mapped to mouse equivalents).
- Pinch-to-zoom on paint canvas: track two-finger distance to adjust zoom level.

**Backend changes**: Add `/manifest.json` route or serve from static files.

**Dependencies**: None.

---

## File Organization Summary

After all phases, the new/modified file structure:

```
src/ltp_controller/web/
  app.py                          -- Modified: new endpoints for scenes, bulk ops, extended status, WS
  ws_manager.py                   -- New: WebSocket connection manager
  static/
    css/
      style.css                   -- Modified: light theme, accessibility, mobile, new component styles
    js/
      app.js                      -- Modified: theme toggle, WS init, keyboard trap
      ws-client.js                -- New: WebSocket client with auto-reconnect
      list-tools.js               -- New: Search, filter, sort, bulk select
      scenes.js                   -- New: Scene management UI
      led-canvas.js               -- New: Canvas-based LED preview renderer
      route-graph.js              -- New: Visual route builder
      paint.js                    -- New: Extracted from paint.html inline script, with enhancements
    manifest.json                 -- New: PWA manifest
    icons/                        -- New: PWA icons
  templates/
    base.html                     -- Modified: theme toggle, scene dropdown, bottom nav, WS script
    dashboard.html                -- Modified: quick actions, activity feed, mini diagram
    routes.html                   -- Modified: graph view toggle, side panel
    paint.html                    -- Modified: extracted JS, new toolbar buttons
    preview.html                  -- Modified: canvas instead of img, fullscreen
    devices.html                  -- New: unified device page
    partials/
      device_card.html            -- New: extracted reusable device card partial
      control_editor.html         -- New: extracted reusable control editor partial
    sources.html                  -- Modified: search/filter bar
    sinks.html                    -- Modified: search/filter bar
    virtual-sources.html          -- Modified: search/filter bar
    scalar-sources.html           -- Modified: search/filter bar
    rules.html                    -- Modified: search/filter/bulk bar
```

---

## Implementation Sequencing

**Phase 1** (can be done in parallel):
1. 1C (Theming/Accessibility) — foundational CSS work, affects all pages.
2. 1B (Search/Filter/Bulk) — reusable `list-tools.js` used everywhere.
3. 1A (Dashboard) — builds on top of 1B's patterns.

**Phase 2** (independent of each other):
1. 2A (Paint Enhancements) — self-contained.
2. 2B (Configuration Scenes) — self-contained, backend + frontend.

**Phase 3** (sequential dependencies):
1. 3A (WebSocket) — must come first, infrastructure.
2. 3B (Live Preview) — depends on 3A for real-time frames.
3. 3C (Visual Route Builder) — independent of 3A/3B but benefits from WS for live status.

**Phase 4** (independent of each other):
1. 4A (Unified Device Page) — template refactoring.
2. 4B (Mobile/Touch/PWA) — CSS + manifest + touch handling.

---

## Key Architectural Decisions

1. **WebSocket library**: Use `flask-sock` instead of `flask-socketio`. The app already uses asyncio for its core event loop and runs Flask in a daemon thread with `threaded=True`. `flask-sock` is lightweight and has no external async runtime dependency. `flask-socketio` would require `eventlet` or `gevent`, which conflicts with the asyncio loop.

2. **No JS framework or bundler**: Stay with vanilla JS. The codebase is small enough that modules loaded via `<script>` tags (using the module pattern or IIFEs) are sufficient. Adding React/Vue would be over-engineering for this admin-style UI. Revisit if JS grows beyond ~3000 lines total.

3. **JS module pattern**: Each new `.js` file exposes its API on a namespace object (e.g., `window.LtpListTools`, `window.LtpScenes`, `window.LtpWS`). Templates call `LtpListTools.init(...)` etc. in their inline script blocks.

4. **Extract inline JS from templates gradually**: Start with `paint.html` (most complex at ~450 lines). Others can keep inline JS for now since they are shorter and self-contained.

5. **Scene storage**: Scenes stored in the existing config YAML file under a `scenes:` key, not a separate database. Keeps the single-file config approach consistent.

6. **Preview rendering**: Move from SVG (generated server-side per request) to Canvas (drawn client-side from frame data). The SVG approach generates a new response on every poll. Canvas + WebSocket eliminates this server load entirely.
