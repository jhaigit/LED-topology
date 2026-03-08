# Web UI Improvement Proposal

## 1. Real-Time Updates via WebSocket

**Problem:** The UI polls every 5 seconds for updates (device status, previews, sensor values). This adds latency, wastes bandwidth when nothing changes, and misses transient events.

**Proposal:** Add a WebSocket endpoint (Flask-SocketIO or a lightweight `asyncio` websocket) that pushes:
- Device online/offline transitions instantly
- Route frame data for live preview
- Scalar source samples as they arrive
- Rule trigger events
- Control value changes from other clients

**Impact:** Preview and sensor pages become truly live. Dashboard status dots update instantly. Multiple browser tabs stay in sync.

---

## 2. Live LED Preview Overhaul

**Problem:** The SVG preview refreshes on a timer, shows static snapshots, and has fixed sizing (10×10px LEDs, 800px max width).

**Proposal:**
- Render previews on a `<canvas>` element for better performance at high pixel counts
- Use WebSocket frame data for smooth, real-time animation (target ~15-30 fps)
- Auto-scale LED size based on pixel count and viewport
- Add a **fullscreen mode** for projecting preview on a monitor
- Support custom LED layout maps (import XY coordinates from a JSON file) so non-grid arrangements (circles, zigzags, art installations) render accurately

---

## 3. Unified Device Page with Tabbed Detail View

**Problem:** Sources, Sinks, and Virtual Sources are three separate pages with duplicated card layouts and similar UX patterns. Navigating between them to understand the full system is tedious.

**Proposal:** Consolidate into a single **Devices** page:
- Sidebar or tab bar: `Physical Sources | Sinks | Virtual Sources | Sensors`
- Clicking a device card opens a **detail panel** (slide-in or expandable) showing:
  - Connection info & build details
  - Controls (editable)
  - Routes this device participates in
  - Live preview (for sources/sinks)
- Reduces navbar items from 8 to 5: `Dashboard | Devices | Routes | Rules | Paint`

---

## 4. Visual Route Builder

**Problem:** Creating routes requires filling out a form modal with dropdowns. There's no visual feedback on how sources connect to sinks until after creation.

**Proposal:** Add a **node-graph view** to the Routes page:
- Sources on the left, sinks on the right, drawn as boxes
- Drag a cable from source to sink to create a route
- Click a cable to edit transform settings (scale, brightness, gamma)
- Color-code cables by status (active=green, disabled=gray, error=red)
- Show live frame counts on each cable
- Keep the existing list view as an alternative (toggle between list/graph)

---

## 5. Dashboard Improvements

**Problem:** The dashboard shows basic counts (sources online, active routes) but doesn't give an at-a-glance system overview.

**Proposal:**
- **System diagram:** Mini node graph showing all active routes (simplified version of #4)
- **Activity feed:** Recent events (device connected, rule triggered, route error) with timestamps
- **Performance metrics:** Frames/sec per route, latency, dropped frames
- **Quick actions:** Start/stop all virtual sources, enable/disable all routes, one-click save config

---

## 6. Paint Tool Enhancements

**Problem:** The paint tool works but has a basic toolset. For matrix displays, 2D editing is limited.

**Proposal:**
- **Undo/redo stack** (Ctrl+Z / Ctrl+Shift+Z)
- **Eyedropper tool** to pick colors from existing pixels
- **Animation timeline:** Paint multiple frames, set frame rate, loop/bounce, push as animated sequence
- **Copy/paste regions** for matrix layouts
- **Keyboard shortcuts** for tool switching (B=brush, L=line, F=fill, T=text, etc.)
- **Zoom and pan** for large matrices

---

## 7. Mobile & Touch Experience

**Problem:** The responsive breakpoint at 768px collapses to single column, but there's no touch-optimized interaction.

**Proposal:**
- Bottom navigation bar on mobile (thumb-reachable) instead of top navbar
- Swipe gestures on device cards (swipe to enable/disable, long-press for details)
- Touch-friendly sliders for controls (larger hit targets)
- Pinch-to-zoom on paint canvas and preview
- Progressive Web App (PWA) manifest for "Add to Home Screen"

---

## 8. Search, Filter & Bulk Operations

**Problem:** With many devices, virtual sources, or rules, the flat list becomes hard to manage. No way to act on multiple items at once.

**Proposal:**
- **Search bar** on each list page (filter by name, type, status)
- **Tags/labels** on devices and routes for grouping (e.g., "living room", "stage left")
- **Multi-select** with checkboxes for bulk enable/disable/delete
- **Sort options** (by name, status, last active, creation date)

---

## 9. Theming & Accessibility

**Problem:** Single dark theme. No accessibility considerations documented.

**Proposal:**
- **Light theme** option (toggle in navbar or system-preference auto-detect)
- Theme stored in `localStorage`
- Ensure sufficient color contrast ratios (WCAG AA)
- Add `aria-label` attributes to icon-only buttons
- Keyboard navigation support (tab order, focus indicators, Enter to activate)
- Screen reader announcements for toast notifications

---

## 10. Configuration Scenes

**Problem:** Save Config dumps everything to one YAML file. No way to save and recall different setups (e.g., "party mode" vs. "ambient" vs. "demo").

**Proposal:**
- **Named scenes** that snapshot: active routes + transforms, virtual source selections + control values, rule enable states
- Quick-switch between scenes from the dashboard or navbar
- Export/import individual scenes as YAML files
- Optional: timed scene transitions (crossfade between two scene states)

---

## Priority Ranking

Most items are independent and can be tackled in any order. WebSocket is only
a prerequisite for smooth live preview animation (item 2); all other items
work with the existing REST API and polling.

| Priority | Item | Effort | Value |
|----------|------|--------|-------|
| 1 | Dashboard improvements | Low | Medium — better first impression, no new infra needed |
| 2 | Search, filter & bulk ops | Low | Medium — quality of life at scale |
| 3 | Paint tool enhancements | Medium | Medium — undo alone is high value |
| 4 | Configuration scenes | Medium | High — key workflow improvement |
| 5 | Live preview overhaul | Medium | High — most visually impactful (needs WebSocket) |
| 6 | WebSocket real-time updates | Medium | High — required by live preview, nice-to-have elsewhere |
| 7 | Visual route builder | High | Medium — impressive but complex |
| 8 | Unified device page | Medium | Medium — reduces navigation |
| 9 | Mobile & touch | Medium | Low-Medium — depends on use case |
| 10 | Theming & accessibility | Low | Low-Medium — good practice |

Items 1-4 are quick wins that need no infrastructure changes. Items 5-6 are
the main architectural addition. Items 7-10 are polish and can be tackled
incrementally.
