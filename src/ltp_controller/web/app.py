"""Flask web application for LTP Controller."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from ltp_controller.controller import Controller
from ltp_controller.input_manager import InputEventManager
from ltp_controller.router import Route, RouteMode, RouteTransform, RoutingEngine
from ltp_controller.rule_engine import RuleEngine
from ltp_controller.rules import Action, ActionType, ComparisonOp, Trigger, TriggerType
from ltp_controller.scalar_sources import ScalarSourceManager, SCALAR_SOURCE_TYPES
from ltp_controller.sink_control import SinkController
from ltp_controller.virtual_sources import VirtualSourceManager, VIRTUAL_SOURCE_TYPES

logger = logging.getLogger(__name__)


def create_app(
    controller: Controller,
    router: RoutingEngine,
    sink_controller: SinkController | None = None,
    virtual_source_manager: VirtualSourceManager | None = None,
    scalar_source_manager: ScalarSourceManager | None = None,
    input_manager: InputEventManager | None = None,
    rule_engine: RuleEngine | None = None,
    event_loop: asyncio.AbstractEventLoop | None = None,
    config_path: str | None = None,
) -> Flask:
    """Create and configure the Flask application."""
    template_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"

    app = Flask(
        __name__,
        template_folder=str(template_dir),
        static_folder=str(static_dir),
    )

    # Store references
    app.config["controller"] = controller
    app.config["router"] = router
    app.config["sink_controller"] = sink_controller
    app.config["virtual_source_manager"] = virtual_source_manager
    app.config["scalar_source_manager"] = scalar_source_manager
    app.config["input_manager"] = input_manager
    app.config["rule_engine"] = rule_engine
    app.config["event_loop"] = event_loop
    app.config["config_path"] = config_path
    app.config["scenes"] = {}  # Scene storage: {id: scene_dict}

    # Helper to run async code from sync Flask handlers
    def run_async(coro: Any) -> Any:
        loop = app.config.get("event_loop")

        if loop and loop.is_running():
            # Schedule on the controller's event loop
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=10)
        else:
            # Fallback to creating a new loop
            return asyncio.run(coro)

    # ==================== Pages ====================

    @app.route("/")
    def dashboard() -> str:
        """Dashboard page."""
        return render_template(
            "dashboard.html",
            sources=controller.sources,
            sinks=controller.sinks,
            routes=router.routes,
            virtual_sources=virtual_source_manager.sources if virtual_source_manager else [],
            sources_json=[s.to_dict() for s in controller.sources],
            sinks_json=[s.to_dict() for s in controller.sinks],
            routes_json=[r.to_dict() for r in router.routes],
            vs_json=[vs.to_dict() for vs in (virtual_source_manager.sources if virtual_source_manager else [])],
        )

    @app.route("/sources")
    def sources_page() -> str:
        """Sources management page."""
        return render_template("sources.html", sources=controller.sources)

    @app.route("/sinks")
    def sinks_page() -> str:
        """Sinks management page."""
        return render_template("sinks.html", sinks=controller.sinks)

    @app.route("/routes")
    def routes_page() -> str:
        """Routes management page."""
        return render_template(
            "routes.html",
            routes=router.routes,
            sources=controller.sources,
            sinks=controller.sinks,
            virtual_sources=virtual_source_manager.sources if virtual_source_manager else [],
        )

    @app.route("/virtual-sources")
    def virtual_sources_page() -> str:
        """Virtual sources management page."""
        return render_template(
            "virtual_sources.html",
            virtual_sources=virtual_source_manager.sources if virtual_source_manager else [],
        )

    @app.route("/paint/<sink_id>")
    def paint_page(sink_id: str) -> str:
        """Paint interface for a sink."""
        sink = controller.get_sink(sink_id)
        if not sink:
            return "Sink not found", 404
        return render_template("paint.html", sink=sink)

    # ==================== API: Sources ====================

    @app.route("/api/sources")
    def api_sources() -> Any:
        """List all sources."""
        return jsonify([s.to_dict() for s in controller.sources])

    @app.route("/api/sources/<source_id>")
    def api_source(source_id: str) -> Any:
        """Get source details."""
        source = controller.get_source(source_id)
        if not source:
            return jsonify({"error": "Source not found"}), 404
        return jsonify(source.to_dict())

    @app.route("/api/sources/<source_id>/controls", methods=["GET", "PUT"])
    def api_source_controls(source_id: str) -> Any:
        """Get or set source controls."""
        source = controller.get_source(source_id)
        if not source:
            return jsonify({"error": "Source not found"}), 404

        if request.method == "GET":
            return jsonify(source.control_values)

        # PUT
        values = request.get_json()
        if not values:
            return jsonify({"error": "No values provided"}), 400

        results = {}
        for control_id, value in values.items():
            success = run_async(controller.set_device_control(source, control_id, value))
            results[control_id] = "ok" if success else "error"

        return jsonify({"status": "ok", "results": results})

    @app.route("/api/sources/<source_id>/refresh", methods=["POST"])
    def api_source_refresh(source_id: str) -> Any:
        """Refresh source info."""
        source = controller.get_source(source_id)
        if not source:
            return jsonify({"error": "Source not found"}), 404

        run_async(controller.refresh_device(source))
        return jsonify({"status": "ok"})

    # ==================== API: Sinks ====================

    @app.route("/api/sinks")
    def api_sinks() -> Any:
        """List all sinks."""
        return jsonify([s.to_dict() for s in controller.sinks])

    @app.route("/api/sinks/<sink_id>")
    def api_sink(sink_id: str) -> Any:
        """Get sink details."""
        sink = controller.get_sink(sink_id)
        if not sink:
            return jsonify({"error": "Sink not found"}), 404
        return jsonify(sink.to_dict())

    @app.route("/api/sinks/<sink_id>/controls", methods=["GET", "PUT"])
    def api_sink_controls(sink_id: str) -> Any:
        """Get or set sink controls."""
        sink = controller.get_sink(sink_id)
        if not sink:
            return jsonify({"error": "Sink not found"}), 404

        if request.method == "GET":
            return jsonify(sink.control_values)

        # PUT
        values = request.get_json()
        if not values:
            return jsonify({"error": "No values provided"}), 400

        results = {}
        for control_id, value in values.items():
            try:
                success = run_async(controller.set_device_control(sink, control_id, value))
                results[control_id] = "ok" if success else "error"
            except (TimeoutError, Exception) as e:
                results[control_id] = "timeout" if isinstance(e, TimeoutError) else "error"

        has_errors = any(v != "ok" for v in results.values())
        status_code = 504 if all(v == "timeout" for v in results.values()) else 200
        return jsonify({"status": "partial" if has_errors else "ok", "results": results}), status_code

    @app.route("/api/sinks/<sink_id>/refresh", methods=["POST"])
    def api_sink_refresh(sink_id: str) -> Any:
        """Refresh sink info."""
        sink = controller.get_sink(sink_id)
        if not sink:
            return jsonify({"error": "Sink not found"}), 404

        try:
            run_async(controller.refresh_device(sink))
            return jsonify({"status": "ok"})
        except TimeoutError:
            return jsonify({"error": "Device did not respond in time"}), 504

    @app.route("/api/sinks/purge", methods=["POST"])
    def api_sinks_purge() -> Any:
        """Remove all offline sinks."""
        purged_ids = [s.id for s in controller.sinks if not s.online]
        count = controller.purge_offline_sinks()
        # Delete routes that used purged sinks
        for route in list(router.routes):
            if route.sink_id in purged_ids:
                run_async(router.delete_route(route.id))
        return jsonify({"status": "ok", "removed": count})

    @app.route("/api/sources/purge", methods=["POST"])
    def api_sources_purge() -> Any:
        """Remove all offline sources."""
        purged_ids = [s.id for s in controller.sources if not s.online]
        count = controller.purge_offline_sources()
        # Delete routes that used purged sources
        for route in list(router.routes):
            if route.source_id in purged_ids:
                run_async(router.delete_route(route.id))
        return jsonify({"status": "ok", "removed": count})

    # ==================== API: Sink Fill ====================

    @app.route("/api/sinks/<sink_id>/fill", methods=["POST"])
    def api_sink_fill(sink_id: str) -> Any:
        """Fill sink with color/pattern.

        Body formats:
        - {"type": "solid", "color": [255, 0, 0]}
        - {"type": "gradient", "colors": [[255,0,0], [0,0,255]]}
        - {"type": "sections", "sections": [{"start": 0, "end": 30, "color": [255,0,0]}]}
        """
        if not sink_controller:
            return jsonify({"error": "Sink controller not initialized"}), 503

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        fill_type = data.get("type", "solid")

        if fill_type == "solid":
            color = data.get("color", [255, 255, 255])
            if len(color) < 3:
                return jsonify({"error": "Color must have 3 components (RGB)"}), 400
            result = run_async(sink_controller.fill_solid(sink_id, tuple(color[:3])))

        elif fill_type == "gradient":
            colors = data.get("colors", [])
            if len(colors) < 2:
                return jsonify({"error": "Gradient requires at least 2 colors"}), 400
            color_tuples = [tuple(c[:3]) for c in colors if len(c) >= 3]
            result = run_async(sink_controller.fill_gradient(sink_id, color_tuples))

        elif fill_type == "sections":
            sections = data.get("sections", [])
            if not sections:
                return jsonify({"error": "No sections provided"}), 400
            background = tuple(data.get("background", [0, 0, 0])[:3])
            result = run_async(sink_controller.fill_sections(sink_id, sections, background))

        else:
            return jsonify({"error": f"Unknown fill type: {fill_type}"}), 400

        if result.get("status") == "error":
            return jsonify(result), 400

        return jsonify(result)

    @app.route("/api/sinks/<sink_id>/clear", methods=["POST"])
    def api_sink_clear(sink_id: str) -> Any:
        """Clear sink (fill with black)."""
        if not sink_controller:
            return jsonify({"error": "Sink controller not initialized"}), 503

        result = run_async(sink_controller.clear(sink_id))

        if result.get("status") == "error":
            return jsonify(result), 400

        return jsonify(result)

    # ==================== API: Sink Paint (Direct Pixel Manipulation) ====================

    @app.route("/api/sinks/<sink_id>/paint", methods=["POST"])
    def api_sink_paint(sink_id: str) -> Any:
        """Paint pixels directly on a sink.

        Body formats:
        - {"pixels": {0: [255,0,0], 5: [0,255,0]}}  # Sparse pixel map
        - {"x": 5, "y": 2, "color": [255,0,0]}  # Single pixel by coordinate
        - {"index": 10, "color": [255,0,0]}  # Single pixel by index
        - {"range": [0, 10], "color": [255,0,0]}  # Fill a range
        """
        if not sink_controller:
            return jsonify({"error": "Sink controller not initialized"}), 503

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        result = run_async(sink_controller.paint_pixels(sink_id, data))

        if result.get("status") == "error":
            return jsonify(result), 400

        return jsonify(result)

    @app.route("/api/sinks/<sink_id>/paint/text", methods=["POST"])
    def api_sink_paint_text(sink_id: str) -> Any:
        """Paint text on a sink.

        Request body (JSON):
        {
            "text": "Hello",           // Text to display (required)
            "color": "#FFFFFF",        // Text color (hex or [r,g,b])
            "background": "#000000",   // Background color
            "font": "5x7",             // Font: "5x7", "4x6", "3x5"
            "x": 0,                    // X offset
            "y": 0,                    // Y offset
            "align": "center",         // "left", "center", "right"
            "vertical_align": "middle", // "top", "middle", "bottom"
            "clear": true              // Clear before drawing
        }
        """
        if not sink_controller:
            return jsonify({"error": "Sink controller not initialized"}), 503

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        if "text" not in data:
            return jsonify({"error": "No text specified"}), 400

        result = run_async(sink_controller.paint_text(sink_id, data))

        if result.get("status") == "error":
            return jsonify(result), 400

        return jsonify(result)

    @app.route("/api/sinks/<sink_id>/paint/image", methods=["POST"])
    def api_sink_paint_image(sink_id: str) -> Any:
        """Paint an uploaded image on a sink.

        Accepts multipart form data with:
        - file: Image file (png, jpg, jpeg, bmp, webp, gif)
        - fit: Fit mode - "contain" (default), "cover", "stretch"
        """
        if not sink_controller:
            return jsonify({"error": "Sink controller not initialized"}), 503

        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "No file selected"}), 400

        # Validate extension
        allowed_ext = {"png", "jpg", "jpeg", "bmp", "webp", "gif"}
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in allowed_ext:
            return jsonify({"error": f"Unsupported format: .{ext}"}), 400

        # Read file data (limit 10MB)
        image_data = file.read()
        if len(image_data) > 10 * 1024 * 1024:
            return jsonify({"error": "File too large (max 10MB)"}), 400

        options = {
            "fit": request.form.get("fit", "contain"),
        }

        result = run_async(sink_controller.paint_image(sink_id, image_data, options))

        if result.get("status") == "error":
            return jsonify(result), 400

        return jsonify(result)

    @app.route("/api/sinks/<sink_id>/paint/buffer")
    def api_sink_paint_buffer(sink_id: str) -> Any:
        """Get the current paint buffer for a sink as JSON.

        Returns pixel data that can be used to sync client state.

        Response:
        {
            "pixels": [[r,g,b], [r,g,b], ...],  // Linear array of RGB values
            "dimensions": [width, height],
            "pixel_count": N
        }
        """
        if not sink_controller:
            return jsonify({"error": "Sink controller not initialized"}), 503

        sink = controller.get_sink(sink_id)
        if not sink:
            return jsonify({"error": "Sink not found"}), 404

        # Get dimensions
        dimensions = sink_controller._get_dimensions(sink)
        if len(dimensions) >= 2:
            width, height = dimensions[0], dimensions[1]
        else:
            width = dimensions[0]
            height = 1

        pixel_count = width * height

        # Get paint buffer if exists
        buffer = sink_controller._paint_buffers.get(sink_id)
        if buffer is not None and len(buffer) == pixel_count:
            # Buffer size matches, use it
            pixels = buffer.tolist()
        elif buffer is not None:
            # Buffer size mismatch - return fresh black buffer and clear old one
            # The mismatch will be handled when painting next
            del sink_controller._paint_buffers[sink_id]
            pixels = [[0, 0, 0] for _ in range(pixel_count)]
        else:
            # Return empty/black buffer
            pixels = [[0, 0, 0] for _ in range(pixel_count)]

        return jsonify({
            "pixels": pixels,
            "dimensions": [width, height],
            "pixel_count": pixel_count,
        })

    @app.route("/api/sinks/<sink_id>/paint/info")
    def api_sink_paint_info(sink_id: str) -> Any:
        """Get sink info for painting (dimensions, pixel count, fonts)."""
        if not sink_controller:
            # Fall back to basic info from controller
            sink = controller.get_sink(sink_id)
            if not sink:
                return jsonify({"error": "Sink not found"}), 404

            props = sink.device.properties
            pixels = int(props.get("pixels", 60))
            dims = [int(d) for d in props.get("dim", str(pixels)).split("x")]
            sink_type = props.get("type", "string")

            return jsonify({
                "id": sink_id,
                "name": sink.name,
                "pixels": pixels,
                "dimensions": dims,
                "type": sink_type,
                "is_matrix": len(dims) > 1,
            })

        # Use sink controller for enhanced info
        result = run_async(sink_controller.get_paint_info(sink_id))
        if result.get("status") == "error":
            return jsonify(result), 404

        return jsonify(result)

    # ==================== API: All Sources (Physical + Virtual) ====================

    @app.route("/api/all-sources")
    def api_all_sources() -> Any:
        """List all sources (physical and virtual) for route selection."""
        sources = []

        # Add physical sources
        for s in controller.sources:
            sources.append({
                "id": s.device.id,
                "name": s.name,
                "type": "physical",
                "online": s.online,
                "properties": s.device.properties,
            })

        # Add virtual sources
        if virtual_source_manager:
            for vs in virtual_source_manager.sources:
                sources.append({
                    "id": vs.id,
                    "name": vs.name,
                    "type": "virtual",
                    "online": True,  # Virtual sources are always "online"
                    "source_type": vs.source_type,
                    "running": vs.is_running,
                })

        return jsonify(sources)

    # ==================== API: Routes ====================

    def _enrich_route(route_dict: dict) -> dict:
        """Add source and sink names to route data."""
        source_id = route_dict["source_id"]
        source = controller.get_source(source_id)

        # Check if it's a virtual source
        if not source and virtual_source_manager:
            vs = virtual_source_manager.get(source_id)
            if vs:
                route_dict["source_name"] = vs.name
                route_dict["source_type"] = "virtual"
            else:
                route_dict["source_name"] = "Unknown"
                route_dict["source_type"] = "unknown"
        else:
            route_dict["source_name"] = source.name if source else "Unknown"
            route_dict["source_type"] = "physical"

        sink = controller.get_sink(route_dict["sink_id"])
        route_dict["sink_name"] = sink.name if sink else "Unknown"
        return route_dict

    @app.route("/api/routes", methods=["GET", "POST"])
    def api_routes() -> Any:
        """List or create routes."""
        if request.method == "GET":
            return jsonify([_enrich_route(r.to_dict()) for r in router.routes])

        # POST - create new route
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        required = ["name", "source_id", "sink_id"]
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400

        transform = None
        if "transform" in data:
            transform = RouteTransform.from_dict(data["transform"])

        route = router.create_route(
            name=data["name"],
            source_id=data["source_id"],
            sink_id=data["sink_id"],
            mode=RouteMode(data.get("mode", "proxy")),
            transform=transform,
            enabled=data.get("enabled", True),
        )

        if route is None:
            return jsonify({"error": "Route already exists for this source/sink pair"}), 409

        return jsonify(route.to_dict()), 201

    @app.route("/api/routes/<route_id>", methods=["GET", "PUT", "DELETE"])
    def api_route(route_id: str) -> Any:
        """Get, update, or delete a route."""
        route = router.get_route(route_id)
        if not route:
            return jsonify({"error": "Route not found"}), 404

        if request.method == "GET":
            return jsonify(_enrich_route(route.to_dict()))

        if request.method == "DELETE":
            run_async(router.delete_route(route_id))
            return jsonify({"status": "ok"})

        # PUT
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        transform = None
        if "transform" in data:
            transform = RouteTransform.from_dict(data["transform"])

        mode = RouteMode(data["mode"]) if "mode" in data else None

        route = router.update_route(
            route_id,
            name=data.get("name"),
            enabled=data.get("enabled"),
            transform=transform,
            mode=mode,
        )

        return jsonify(route.to_dict())

    @app.route("/api/routes/<route_id>/enable", methods=["POST"])
    def api_route_enable(route_id: str) -> Any:
        """Enable a route."""
        success = run_async(router.enable_route(route_id))
        if not success:
            return jsonify({"error": "Route not found"}), 404
        return jsonify({"status": "ok"})

    @app.route("/api/routes/<route_id>/disable", methods=["POST"])
    def api_route_disable(route_id: str) -> Any:
        """Disable a route."""
        success = run_async(router.disable_route(route_id))
        if not success:
            return jsonify({"error": "Route not found"}), 404
        return jsonify({"status": "ok"})

    @app.route("/api/routes/bulk-action", methods=["POST"])
    def api_routes_bulk_action() -> Any:
        """Bulk enable/disable/delete routes."""
        data = request.json
        action = data.get("action")
        ids = data.get("ids", [])

        if action not in ("enable", "disable", "delete"):
            return jsonify({"error": "Invalid action"}), 400

        results = {}
        for route_id in ids:
            try:
                route = router.get_route(route_id)
                if not route:
                    results[route_id] = "not found"
                    continue
                if action == "enable":
                    run_async(router.enable_route(route_id))
                elif action == "disable":
                    run_async(router.disable_route(route_id))
                elif action == "delete":
                    run_async(router.delete_route(route_id))
                results[route_id] = "ok"
            except Exception as e:
                results[route_id] = str(e)

        return jsonify({"status": "ok", "results": results})

    # ==================== API: System ====================

    @app.route("/api/status")
    def api_status() -> Any:
        """Get system status."""
        return jsonify(
            {
                "sources": {
                    "total": len(controller.sources),
                    "online": len(controller.online_sources),
                },
                "sinks": {
                    "total": len(controller.sinks),
                    "online": len(controller.online_sinks),
                },
                "routes": {
                    "total": len(router.routes),
                    "active": len(router.active_routes),
                },
            }
        )

    @app.route("/api/discovery/refresh", methods=["POST"])
    def api_discovery_refresh() -> Any:
        """Force refresh mDNS service discovery."""
        run_async(controller.refresh_discovery())
        return jsonify({"status": "ok", "message": "Discovery refresh triggered"})

    # ==================== API: Configuration ====================

    @app.route("/api/config/export")
    def api_config_export() -> Any:
        """Export current configuration as YAML."""
        import yaml

        config = {
            "virtual_sources": virtual_source_manager.to_config() if virtual_source_manager else [],
            "routes": [],
            "rules": rule_engine.to_config() if rule_engine else [],
        }

        # Export routes
        for route in router.routes:
            route_data = {
                "name": route.name,
                "source": route.source_id,
                "sink": route.sink_id,
                "mode": route.mode.value,
                "enabled": route.enabled,
            }
            if route.transform:
                route_data["transform"] = route.transform.to_dict()
            config["routes"].append(route_data)

        yaml_content = yaml.dump(config, default_flow_style=False, sort_keys=False)
        return yaml_content, 200, {"Content-Type": "text/yaml"}

    @app.route("/api/config/virtual-sources/export")
    def api_config_vs_export() -> Any:
        """Export virtual sources configuration."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503
        return jsonify(virtual_source_manager.to_config())

    @app.route("/api/config/rules/export")
    def api_config_rules_export() -> Any:
        """Export rules configuration."""
        if not rule_engine:
            return jsonify({"error": "Rule engine not available"}), 503
        return jsonify(rule_engine.to_config())

    # ==================== Scenes API ====================

    @app.route("/api/scenes", methods=["GET"])
    def api_scenes_list():
        """List all saved scenes."""
        scenes = app.config["scenes"]
        return jsonify([
            {"id": s_id, "name": s["name"], "description": s.get("description", "")}
            for s_id, s in scenes.items()
        ])

    @app.route("/api/scenes", methods=["POST"])
    def api_scenes_create():
        """Create a new scene from current state."""
        import uuid
        import time
        data = request.json or {}
        name = data.get("name", "Untitled Scene")
        description = data.get("description", "")

        # Snapshot current state
        route_states = []
        for route in router.routes:
            route_state = {
                "route_id": route.id,
                "enabled": route.enabled,
            }
            if route.transform:
                route_state["transform"] = {
                    "brightness": route.transform.brightness,
                    "gamma": route.transform.gamma,
                    "scale_mode": route.transform.scale_mode.value if hasattr(route.transform.scale_mode, 'value') else str(route.transform.scale_mode),
                    "mirror": route.transform.mirror,
                }
            route_states.append(route_state)

        vs_states = []
        if virtual_source_manager:
            for vs in virtual_source_manager.sources:
                vs_state = {
                    "id": vs.id,
                    "is_running": vs.is_running,
                }
                if hasattr(vs, 'controls') and vs.controls:
                    vs_state["control_values"] = {
                        ctrl.id: vs.controls.get_value(ctrl.id)
                        for ctrl in vs.controls.to_list()
                    }
                vs_states.append(vs_state)

        rule_states = []
        if rule_engine:
            for rule in rule_engine.rules:
                rule_states.append({
                    "id": rule.id,
                    "enabled": rule.enabled,
                })

        scene_id = str(uuid.uuid4())[:8]
        scene = {
            "name": name,
            "description": description,
            "created_at": time.time(),
            "routes": route_states,
            "virtual_sources": vs_states,
            "rules": rule_states,
        }
        app.config["scenes"][scene_id] = scene
        return jsonify({"status": "ok", "id": scene_id, "name": name})

    @app.route("/api/scenes/<scene_id>", methods=["DELETE"])
    def api_scenes_delete(scene_id: str):
        """Delete a scene."""
        if scene_id not in app.config["scenes"]:
            return jsonify({"error": "Scene not found"}), 404
        del app.config["scenes"][scene_id]
        return jsonify({"status": "ok"})

    @app.route("/api/scenes/<scene_id>/activate", methods=["POST"])
    def api_scenes_activate(scene_id: str):
        """Activate a scene: apply its saved state."""
        scene = app.config["scenes"].get(scene_id)
        if not scene:
            return jsonify({"error": "Scene not found"}), 404

        applied = {"routes": 0, "virtual_sources": 0, "rules": 0}

        # Apply route states
        for rs in scene.get("routes", []):
            route = None
            for r in router.routes:
                if r.id == rs["route_id"]:
                    route = r
                    break
            if not route:
                continue
            try:
                if rs["enabled"] and not route.enabled:
                    run_async(router.enable_route(route.id))
                elif not rs["enabled"] and route.enabled:
                    run_async(router.disable_route(route.id))
                # Apply transform if present
                if "transform" in rs and route.transform:
                    route.transform.brightness = rs["transform"].get("brightness", route.transform.brightness)
                    route.transform.gamma = rs["transform"].get("gamma", route.transform.gamma)
                    route.transform.mirror = rs["transform"].get("mirror", route.transform.mirror)
                applied["routes"] += 1
            except Exception as e:
                logger.warning("Failed to apply route state for %s: %s", rs["route_id"], e)

        # Apply virtual source states
        if virtual_source_manager:
            for vs_state in scene.get("virtual_sources", []):
                vs = None
                for v in virtual_source_manager.sources:
                    if v.id == vs_state["id"]:
                        vs = v
                        break
                if not vs:
                    continue
                try:
                    if vs_state.get("is_running") and not vs.is_running:
                        run_async(virtual_source_manager.start_source(vs.id))
                    elif not vs_state.get("is_running") and vs.is_running:
                        run_async(virtual_source_manager.stop_source(vs.id))
                    if "control_values" in vs_state and hasattr(vs, 'controls'):
                        for ctrl_id, value in vs_state["control_values"].items():
                            vs.controls.set_value(ctrl_id, value)
                    applied["virtual_sources"] += 1
                except Exception as e:
                    logger.warning("Failed to apply VS state for %s: %s", vs_state["id"], e)

        # Apply rule states
        if rule_engine:
            for rule_state in scene.get("rules", []):
                try:
                    if rule_state["enabled"]:
                        rule_engine.enable_rule(rule_state["id"])
                    else:
                        rule_engine.disable_rule(rule_state["id"])
                    applied["rules"] += 1
                except Exception as e:
                    logger.warning("Failed to apply rule state for %s: %s", rule_state["id"], e)

        return jsonify({"status": "ok", "applied": applied})

    @app.route("/api/config/save", methods=["POST"])
    def api_config_save() -> Any:
        """Save current configuration to file.

        Request body: {"path": "/path/to/config.yaml"} or empty for default
        Uses the config file path from startup if not specified.
        Preserves existing config settings (device, logging, web, etc.)
        and only updates routes, rules, and virtual_sources sections.
        """
        import yaml

        data = request.get_json() or {}
        save_path = data.get("path") or app.config.get("config_path")

        if not save_path:
            return jsonify({"error": "Config path not specified and no default config file"}), 400

        # Load existing config to preserve other settings
        existing_config: dict[str, Any] = {}
        try:
            with open(save_path) as f:
                existing_config = yaml.safe_load(f) or {}
        except FileNotFoundError:
            pass  # New file, start fresh
        except Exception as e:
            logger.warning(f"Could not read existing config: {e}")

        # Update only the routes, rules, and virtual_sources sections
        existing_config["virtual_sources"] = virtual_source_manager.to_config() if virtual_source_manager else []
        existing_config["rules"] = rule_engine.to_config() if rule_engine else []

        # Export routes
        existing_config["routes"] = []
        for route in router.routes:
            route_data = {
                "name": route.name,
                "source": route.source_id,
                "sink": route.sink_id,
                "mode": route.mode.value,
                "enabled": route.enabled,
            }
            if route.transform:
                route_data["transform"] = route.transform.to_dict()
            existing_config["routes"].append(route_data)

        try:
            with open(save_path, "w") as f:
                yaml.dump(existing_config, f, default_flow_style=False, sort_keys=False)
            logger.info(f"Configuration saved to {save_path}")
            return jsonify({"status": "ok", "path": save_path})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ==================== API: Preview ====================

    @app.route("/api/routes/<route_id>/preview")
    def api_route_preview(route_id: str) -> Any:
        """Get LED preview for a route as SVG."""
        route = router.get_route(route_id)
        if not route:
            return jsonify({"error": "Route not found"}), 404

        # Determine dimensions from source (fall back to pixel count)
        dims = route._source_dims
        is_2d = dims is not None and len(dims) >= 2

        # Get the last frame data if available
        if route._last_frame is None:
            if is_2d:
                return _generate_led_svg_2d([], dims[0], dims[1])
            return _generate_led_svg([], 60)

        pixels = route._last_frame
        if is_2d:
            return _generate_led_svg_2d(pixels, dims[0], dims[1])
        return _generate_led_svg(pixels, len(pixels))

    def _generate_led_svg(pixels: list, count: int) -> Any:
        """Generate an SVG visualization of LED pixels."""
        from flask import Response

        led_width = 12
        led_height = 20
        spacing = 2
        total_width = count * (led_width + spacing)

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_width} {led_height + 4}" '
            f'width="{min(total_width, 800)}" height="{led_height + 4}">'
        ]

        for i in range(count):
            if i < len(pixels) and len(pixels[i]) >= 3:
                r, g, b = int(pixels[i][0]), int(pixels[i][1]), int(pixels[i][2])
                color = f"rgb({r},{g},{b})"
            else:
                color = "#333"

            x = i * (led_width + spacing)
            svg_parts.append(
                f'<rect x="{x}" y="2" width="{led_width}" height="{led_height}" '
                f'rx="2" fill="{color}" stroke="#555" stroke-width="1"/>'
            )

        svg_parts.append("</svg>")
        svg_content = "".join(svg_parts)

        return Response(svg_content, mimetype="image/svg+xml")

    @app.route("/api/routes/<route_id>/frame")
    def api_route_frame(route_id: str) -> Any:
        """Get last frame data as JSON (for canvas rendering)."""
        route = router.get_route(route_id)
        if not route:
            return jsonify({"error": "Route not found"}), 404

        dims = route._source_dims or [0]
        pixels = route._last_frame or []

        return jsonify({
            "pixels": [[int(c) for c in p] for p in pixels] if pixels else [],
            "dimensions": list(dims),
        })

    @app.route("/api/sinks/<sink_id>/preview")
    def api_sink_preview(sink_id: str) -> Any:
        """Get LED preview for a sink's paint buffer as SVG."""
        sink = controller.get_sink(sink_id)
        if not sink:
            return jsonify({"error": "Sink not found"}), 404

        # Get dimensions
        dimensions = sink_controller._get_dimensions(sink)
        if len(dimensions) >= 2:
            width, height = dimensions[0], dimensions[1]
        else:
            width = dimensions[0]
            height = 1

        # Get paint buffer if exists and size matches
        pixel_count = width * height
        buffer = sink_controller._paint_buffers.get(sink_id)
        if buffer is not None and len(buffer) == pixel_count:
            pixels = buffer
        else:
            # Return empty preview if no buffer or size mismatch
            pixels = []

        return _generate_led_svg_2d(pixels, width, height)

    def _generate_led_svg_2d(pixels: list, width: int, height: int) -> Any:
        """Generate an SVG visualization of LED pixels in 2D grid."""
        from flask import Response

        led_size = 10
        spacing = 1
        total_width = width * (led_size + spacing)
        total_height = height * (led_size + spacing)

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_width} {total_height}" '
            f'width="{min(total_width, 800)}" height="{min(total_height, 400)}" '
            f'style="background: #111;">'
        ]

        for y in range(height):
            for x in range(width):
                idx = y * width + x
                if idx < len(pixels) and len(pixels[idx]) >= 3:
                    r, g, b = int(pixels[idx][0]), int(pixels[idx][1]), int(pixels[idx][2])
                    color = f"rgb({r},{g},{b})"
                else:
                    color = "#222"

                px = x * (led_size + spacing)
                py = y * (led_size + spacing)
                svg_parts.append(
                    f'<rect x="{px}" y="{py}" width="{led_size}" height="{led_size}" '
                    f'fill="{color}"/>'
                )

        svg_parts.append("</svg>")
        svg_content = "".join(svg_parts)

        return Response(svg_content, mimetype="image/svg+xml")

    @app.route("/preview")
    def preview_page() -> str:
        """Live LED preview page."""
        return render_template(
            "preview.html",
            routes=router.routes,
            sources=controller.sources,
            sinks=controller.sinks,
            virtual_sources=virtual_source_manager.to_list() if virtual_source_manager else [],
        )

    # ==================== API: Virtual Sources ====================

    @app.route("/api/virtual-sources")
    def api_virtual_sources_list() -> Any:
        """List all virtual sources."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503
        return jsonify(virtual_source_manager.to_list())

    @app.route("/api/virtual-sources/types")
    def api_virtual_source_types() -> Any:
        """List available virtual source types."""
        # Categorization mapping
        matrix_patterns = {"grid", "corners", "sweep", "checkerboard", "pixel_index", "coordinates", "test_card"}
        media_sources = {"image"}
        visualizers = {"bar_graph", "multi_bar", "vu_meter"}
        monitors = {"system_monitor", "cpu_cores"}

        types = []
        for type_name, type_class in VIRTUAL_SOURCE_TYPES.items():
            if type_name in matrix_patterns:
                category = "matrix_pattern"
            elif type_name in media_sources:
                category = "media"
            elif type_name in visualizers:
                category = "visualizer"
            elif type_name in monitors:
                category = "monitor"
            else:
                category = "pattern"

            # Get description from docstring if available
            description = ""
            if type_class.__doc__:
                description = type_class.__doc__.split("\n")[0].strip()

            types.append({
                "type": type_name,
                "name": type_name.replace("_", " ").title(),
                "category": category,
                "description": description,
            })
        return jsonify(types)

    @app.route("/api/virtual-sources", methods=["POST"])
    def api_virtual_sources_create() -> Any:
        """Create a new virtual source."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        source_type = data.get("type")
        if not source_type:
            return jsonify({"error": "Missing 'type' field"}), 400

        if source_type not in VIRTUAL_SOURCE_TYPES:
            return jsonify({"error": f"Unknown source type: {source_type}"}), 400

        source = virtual_source_manager.create(
            source_type=source_type,
            name=data.get("name"),
            output_dimensions=data.get("output_dimensions", [60]),
            frame_rate=data.get("frame_rate", 30.0),
            adaptive_dimensions=data.get("adaptive_dimensions", False),
            enabled=data.get("enabled", True),
            control_values=data.get("control_values", {}),
        )

        if source:
            return jsonify(source.to_dict()), 201
        return jsonify({"error": "Failed to create virtual source"}), 500

    @app.route("/api/virtual-sources/<source_id>")
    def api_virtual_source_get(source_id: str) -> Any:
        """Get a virtual source."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        source = virtual_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Virtual source not found"}), 404
        return jsonify(source.to_dict())

    @app.route("/api/virtual-sources/<source_id>", methods=["PUT"])
    def api_virtual_source_update(source_id: str) -> Any:
        """Update a virtual source."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        source = virtual_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Virtual source not found"}), 404

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Update name if provided
        if "name" in data:
            source.config.name = data["name"]

        # Update dimensions (requires restart if running)
        needs_restart = False
        if "output_dimensions" in data:
            source.config.output_dimensions = data["output_dimensions"]
            needs_restart = True

        # Update frame rate
        if "frame_rate" in data:
            source.config.frame_rate = float(data["frame_rate"])
            needs_restart = True

        # Update enabled state
        if "enabled" in data:
            source.config.enabled = data["enabled"]
            if data["enabled"] and not source.is_running:
                source.start()
            elif not data["enabled"] and source.is_running:
                source.stop()
                needs_restart = False

        # Update control values
        if "control_values" in data:
            for control_id, value in data["control_values"].items():
                source.set_control(control_id, value)

        # Restart if dimensions or frame rate changed while running
        if needs_restart and source.is_running:
            source.stop()
            source.start()

        # Restart any routes using this VS so they pick up new dimensions
        if needs_restart:
            for route in router.routes:
                if route.source_id == source_id and route.enabled:
                    router._pending_restarts.add(route.id)

        return jsonify(source.to_dict())

    @app.route("/api/virtual-sources/<source_id>", methods=["DELETE"])
    def api_virtual_source_delete(source_id: str) -> Any:
        """Delete a virtual source."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        if virtual_source_manager.remove(source_id):
            # Delete routes that used this source
            for route in list(router.routes):
                if route.source_id == source_id:
                    run_async(router.delete_route(route.id))
            return jsonify({"status": "ok"})
        return jsonify({"error": "Virtual source not found"}), 404

    @app.route("/api/virtual-sources/<source_id>/controls", methods=["GET", "PUT"])
    def api_virtual_source_controls(source_id: str) -> Any:
        """Get or set virtual source controls."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        source = virtual_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Virtual source not found"}), 404

        if request.method == "GET":
            return jsonify(source.controls.get_values())

        # PUT
        values = request.get_json()
        if not values:
            return jsonify({"error": "No values provided"}), 400

        results = {}
        for control_id, value in values.items():
            success = source.set_control(control_id, value)
            results[control_id] = "ok" if success else "error"

        return jsonify({"status": "ok", "results": results})

    @app.route("/api/virtual-sources/<source_id>/data", methods=["POST"])
    def api_virtual_source_data(source_id: str) -> Any:
        """Push data to a virtual source (for visualizers)."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        source = virtual_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Virtual source not found"}), 404

        data = request.get_json()
        if data is None:
            return jsonify({"error": "No data provided"}), 400

        source.set_data(data)
        return jsonify({"status": "ok"})

    @app.route("/api/virtual-sources/<source_id>/image", methods=["POST"])
    def api_virtual_source_image(source_id: str) -> Any:
        """Upload an image to an image virtual source.

        Accepts multipart form data with:
        - file: Image file (png, jpg, jpeg, bmp, webp, gif)
        Or JSON with:
        - path: Filesystem path to an image file
        """
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        source = virtual_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Virtual source not found"}), 404

        from ltp_controller.virtual_sources.image_source import ImageSource

        if not isinstance(source, ImageSource):
            return jsonify({"error": "Source is not an image source"}), 400

        # Handle file upload
        if "file" in request.files:
            file = request.files["file"]
            if not file.filename:
                return jsonify({"error": "No file selected"}), 400

            image_data = file.read()
            if source.load_image_bytes(image_data):
                return jsonify({
                    "status": "ok",
                    "image": source.to_dict().get("image_info", {}),
                })
            return jsonify({"error": "Failed to load image"}), 400

        # Handle JSON path
        data = request.get_json(silent=True)
        if data and "path" in data:
            if source._load_image_file(data["path"]):
                return jsonify({
                    "status": "ok",
                    "image": source.to_dict().get("image_info", {}),
                })
            return jsonify({"error": f"Failed to load image from path: {data['path']}"}), 400

        return jsonify({"error": "No file or path provided"}), 400

    @app.route("/api/virtual-sources/<source_id>/start", methods=["POST"])
    def api_virtual_source_start(source_id: str) -> Any:
        """Start a virtual source."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        source = virtual_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Virtual source not found"}), 404

        source.start()
        return jsonify({"status": "ok"})

    @app.route("/api/virtual-sources/<source_id>/stop", methods=["POST"])
    def api_virtual_source_stop(source_id: str) -> Any:
        """Stop a virtual source."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        source = virtual_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Virtual source not found"}), 404

        source.stop()
        return jsonify({"status": "ok"})

    @app.route("/api/virtual-sources/<source_id>/preview")
    def api_virtual_source_preview(source_id: str) -> Any:
        """Get preview of virtual source output as SVG."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        source = virtual_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Virtual source not found"}), 404

        # Get dimensions
        dims = source.config.output_dimensions
        if len(dims) >= 2:
            width, height = dims[0], dims[1]
        else:
            width = dims[0]
            height = 1

        # Render a frame with all transforms (speed, reverse, brightness, mirror)
        num_pixels = width * height
        try:
            frame = source.render_frame(num_pixels)
            pixels = frame.tolist() if hasattr(frame, 'tolist') else list(frame)
        except Exception:
            # Return empty preview on error
            pixels = []

        return _generate_led_svg_2d(pixels, width, height)

    @app.route("/api/virtual-sources/<source_id>/source-image")
    def api_virtual_source_image_get(source_id: str) -> Any:
        """Get the full source image as JPEG for image sources.

        Returns the loaded image scaled to a reasonable preview size.
        Also returns viewport info as custom headers.
        """
        from flask import Response

        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        source = virtual_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Virtual source not found"}), 404

        from ltp_controller.virtual_sources.image_source import ImageSource

        if not isinstance(source, ImageSource) or source._image is None:
            # Return a 1x1 transparent pixel
            return Response(
                b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
                b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00'
                b'\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00'
                b'\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82',
                mimetype="image/png",
            )

        import io
        from PIL import Image as PILImage

        # Scale image to reasonable preview size (max 400px wide)
        img = PILImage.fromarray(source._image)
        max_preview = 400
        if img.width > max_preview:
            scale = max_preview / img.width
            img = img.resize(
                (max_preview, max(1, int(img.height * scale))),
                PILImage.LANCZOS,
            )

        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        buf.seek(0)

        resp = Response(buf.read(), mimetype="image/jpeg")
        # Pass image and viewport info as headers for the JS overlay
        resp.headers["X-Image-Width"] = str(source._image_width)
        resp.headers["X-Image-Height"] = str(source._image_height)
        resp.headers["X-Preview-Width"] = str(img.width)
        resp.headers["X-Preview-Height"] = str(img.height)
        return resp

    @app.route("/api/virtual-sources/<source_id>/viewport")
    def api_virtual_source_viewport(source_id: str) -> Any:
        """Get current viewport rectangle in image coordinates."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        source = virtual_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Virtual source not found"}), 404

        from ltp_controller.virtual_sources.image_source import ImageSource

        if not isinstance(source, ImageSource) or source._image is None:
            return jsonify({"error": "No image loaded"}), 400

        img_w = source._image_width
        img_h = source._image_height
        zoom = source.get_control("zoom")
        vp_w = img_w / zoom
        vp_h = img_h / zoom

        pan_mode = source.get_control("pan_mode")
        if pan_mode != "none" and source.is_running:
            t = source.get_time_elapsed()
            vp_x, vp_y = source._calc_pan_position(
                t, pan_mode, img_w, img_h, vp_w, vp_h
            )
        else:
            max_x = max(0, img_w - vp_w)
            max_y = max(0, img_h - vp_h)
            vp_x = source.get_control("viewport_x") * max_x
            vp_y = source.get_control("viewport_y") * max_y

        return jsonify({
            "image_width": img_w,
            "image_height": img_h,
            "viewport_x": vp_x,
            "viewport_y": vp_y,
            "viewport_width": vp_w,
            "viewport_height": vp_h,
            "zoom": zoom,
            "pan_mode": pan_mode,
        })

    # ==================== Page: Scalar Sources ====================

    @app.route("/scalar-sources")
    def scalar_sources_page() -> str:
        """Scalar sources (sensors) management page."""
        return render_template(
            "scalar_sources.html",
            scalar_sources=scalar_source_manager.all() if scalar_source_manager else [],
        )

    # ==================== API: Scalar Sources ====================

    @app.route("/api/scalar-sources")
    def api_scalar_sources_list() -> Any:
        """List all scalar sources."""
        if not scalar_source_manager:
            return jsonify({"error": "Scalar sources not available"}), 503
        return jsonify(scalar_source_manager.to_list())

    @app.route("/api/scalar-sources/types")
    def api_scalar_source_types() -> Any:
        """List available scalar source types."""
        types = []
        for type_name, type_class in SCALAR_SOURCE_TYPES.items():
            types.append({
                "type": type_name,
                "name": type_name.replace("_", " ").title(),
                "description": type_class.__doc__.split("\n")[0] if type_class.__doc__ else "",
            })
        return jsonify(types)

    @app.route("/api/scalar-sources", methods=["POST"])
    def api_scalar_sources_create() -> Any:
        """Create a new scalar source."""
        if not scalar_source_manager:
            return jsonify({"error": "Scalar sources not available"}), 503

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        source_type = data.get("type")
        if not source_type:
            return jsonify({"error": "Missing 'type' field"}), 400

        if source_type not in SCALAR_SOURCE_TYPES:
            return jsonify({"error": f"Unknown source type: {source_type}"}), 400

        from ltp_controller.scalar_sources import ScalarSourceConfig

        config = ScalarSourceConfig(
            name=data.get("name", f"{source_type.replace('_', ' ').title()} Source"),
            description=data.get("description", ""),
            sample_rate=data.get("sample_rate", 1.0),
            enabled=data.get("enabled", True),
        )

        source_class = SCALAR_SOURCE_TYPES[source_type]
        source = source_class(config)
        scalar_source_manager.add(source)

        # Start if enabled
        if config.enabled:
            run_async(source.start())

        return jsonify(source.to_dict()), 201

    @app.route("/api/scalar-sources/<source_id>")
    def api_scalar_source_get(source_id: str) -> Any:
        """Get a scalar source."""
        if not scalar_source_manager:
            return jsonify({"error": "Scalar sources not available"}), 503

        source = scalar_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Scalar source not found"}), 404
        return jsonify(source.to_dict())

    @app.route("/api/scalar-sources/<source_id>", methods=["DELETE"])
    def api_scalar_source_delete(source_id: str) -> Any:
        """Delete a scalar source."""
        if not scalar_source_manager:
            return jsonify({"error": "Scalar sources not available"}), 503

        source = scalar_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Scalar source not found"}), 404

        # Stop if running
        if source.is_running:
            run_async(source.stop())

        scalar_source_manager.remove(source_id)
        return jsonify({"status": "ok"})

    @app.route("/api/scalar-sources/<source_id>/controls", methods=["GET", "PUT"])
    def api_scalar_source_controls(source_id: str) -> Any:
        """Get or set scalar source controls."""
        if not scalar_source_manager:
            return jsonify({"error": "Scalar sources not available"}), 503

        source = scalar_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Scalar source not found"}), 404

        if request.method == "GET":
            return jsonify(source.controls.get_values())

        # PUT
        values = request.get_json()
        if not values:
            return jsonify({"error": "No values provided"}), 400

        results = {}
        for control_id, value in values.items():
            try:
                source.set_control(control_id, value)
                results[control_id] = "ok"
            except Exception:
                results[control_id] = "error"

        return jsonify({"status": "ok", "results": results})

    @app.route("/api/scalar-sources/<source_id>/sample")
    def api_scalar_source_sample(source_id: str) -> Any:
        """Get current sample from scalar source."""
        if not scalar_source_manager:
            return jsonify({"error": "Scalar sources not available"}), 503

        source = scalar_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Scalar source not found"}), 404

        sample = source.sample()
        return jsonify({
            "values": sample.tolist(),
            "channels": [ch.model_dump() for ch in source.channels],
            "channel_arrays": [arr.model_dump() for arr in source.channel_arrays],
        })

    @app.route("/api/scalar-sources/<source_id>/start", methods=["POST"])
    def api_scalar_source_start(source_id: str) -> Any:
        """Start a scalar source."""
        if not scalar_source_manager:
            return jsonify({"error": "Scalar sources not available"}), 503

        source = scalar_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Scalar source not found"}), 404

        run_async(source.start())
        return jsonify({"status": "ok"})

    @app.route("/api/scalar-sources/<source_id>/stop", methods=["POST"])
    def api_scalar_source_stop(source_id: str) -> Any:
        """Stop a scalar source."""
        if not scalar_source_manager:
            return jsonify({"error": "Scalar sources not available"}), 503

        source = scalar_source_manager.get(source_id)
        if not source:
            return jsonify({"error": "Scalar source not found"}), 404

        run_async(source.stop())
        return jsonify({"status": "ok"})

    # ==================== Page: Rules ====================

    @app.route("/rules")
    def rules_page() -> str:
        """Rules management page."""
        # Serialize objects to dicts for JSON in template
        sinks_data = [{"id": s.id, "name": s.name} for s in controller.sinks]
        routes_data = [{"id": r.id, "name": r.name} for r in router.routes]
        vs_data = [{"id": vs.id, "name": vs.name} for vs in (virtual_source_manager.sources if virtual_source_manager else [])]

        return render_template(
            "rules.html",
            rules=rule_engine.rules if rule_engine else [],
            sinks=sinks_data,
            routes=routes_data,
            virtual_sources=vs_data,
        )

    # ==================== API: Inputs ====================

    @app.route("/api/inputs")
    def api_inputs() -> Any:
        """Get all inputs grouped by sink."""
        if not input_manager:
            return jsonify({"error": "Input manager not available"}), 503

        result = {}
        for sink_id, inputs in input_manager.get_all_inputs().items():
            # Get sink name for display
            sink = controller.get_sink(sink_id)
            sink_name = sink.name if sink else sink_id

            result[sink_id] = {
                "sink_id": sink_id,
                "sink_name": sink_name,
                "connected": input_manager.is_connected_to_sink(sink_id),
                "inputs": [inp.to_dict() for inp in inputs],
            }

        return jsonify(result)

    @app.route("/api/sinks/<sink_id>/inputs")
    def api_sink_inputs(sink_id: str) -> Any:
        """Get inputs for a specific sink."""
        if not input_manager:
            return jsonify({"error": "Input manager not available"}), 503

        sink = controller.get_sink(sink_id)
        if not sink:
            return jsonify({"error": "Sink not found"}), 404

        inputs = input_manager.get_inputs_for_sink(sink_id)
        return jsonify({
            "sink_id": sink_id,
            "sink_name": sink.name,
            "connected": input_manager.is_connected_to_sink(sink_id),
            "inputs": [inp.to_dict() for inp in inputs],
        })

    @app.route("/api/sinks/<sink_id>/available-controls")
    def api_sink_available_controls(sink_id: str) -> Any:
        """Get available controls for a sink (for rule creation).

        Returns the list of controls exposed by the sink, including both
        local controls and hardware controls.
        """
        sink = controller.get_sink(sink_id)
        if not sink:
            return jsonify({"error": "Sink not found"}), 404

        controls = sink.controls or []
        return jsonify({
            "sink_id": sink_id,
            "sink_name": sink.name,
            "controls": controls,
        })

    # ==================== API: Rules ====================

    @app.route("/api/rules")
    def api_rules_list() -> Any:
        """List all rules."""
        if not rule_engine:
            return jsonify({"error": "Rule engine not available"}), 503
        return jsonify([r.to_dict() for r in rule_engine.rules])

    @app.route("/api/rules", methods=["POST"])
    def api_rules_create() -> Any:
        """Create a new rule."""
        if not rule_engine:
            return jsonify({"error": "Rule engine not available"}), 503

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Validate required fields
        if "name" not in data:
            return jsonify({"error": "Missing 'name' field"}), 400
        if "trigger" not in data:
            return jsonify({"error": "Missing 'trigger' field"}), 400
        if "actions" not in data or not data["actions"]:
            return jsonify({"error": "Missing or empty 'actions' field"}), 400

        try:
            # Parse trigger
            trigger_data = data["trigger"]
            trigger = Trigger(
                type=TriggerType(trigger_data.get("type", "input_change")),
                sink_id=trigger_data["sink_id"],
                input_id=trigger_data["input_id"],
                comparison=ComparisonOp(trigger_data.get("comparison", "changed_to")),
                value=trigger_data.get("value", True),
            )

            # Parse actions
            actions = []
            for action_data in data["actions"]:
                actions.append(Action(
                    type=ActionType(action_data["type"]),
                    target_id=action_data["target_id"],
                    control_id=action_data.get("control_id"),
                    value=action_data.get("value"),
                ))

            # Create rule
            rule = rule_engine.create_rule(
                name=data["name"],
                trigger=trigger,
                actions=actions,
                enabled=data.get("enabled", True),
            )

            return jsonify(rule.to_dict()), 201

        except (KeyError, ValueError) as e:
            return jsonify({"error": f"Invalid data: {e}"}), 400

    @app.route("/api/rules/<rule_id>")
    def api_rule_get(rule_id: str) -> Any:
        """Get a specific rule."""
        if not rule_engine:
            return jsonify({"error": "Rule engine not available"}), 503

        rule = rule_engine.get_rule(rule_id)
        if not rule:
            return jsonify({"error": "Rule not found"}), 404

        return jsonify(rule.to_dict())

    @app.route("/api/rules/<rule_id>", methods=["PUT"])
    def api_rule_update(rule_id: str) -> Any:
        """Update a rule."""
        if not rule_engine:
            return jsonify({"error": "Rule engine not available"}), 503

        rule = rule_engine.get_rule(rule_id)
        if not rule:
            return jsonify({"error": "Rule not found"}), 404

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        try:
            # Parse optional trigger
            trigger = None
            if "trigger" in data:
                trigger_data = data["trigger"]
                trigger = Trigger(
                    type=TriggerType(trigger_data.get("type", "input_change")),
                    sink_id=trigger_data["sink_id"],
                    input_id=trigger_data["input_id"],
                    comparison=ComparisonOp(trigger_data.get("comparison", "changed_to")),
                    value=trigger_data.get("value", True),
                )

            # Parse optional actions
            actions = None
            if "actions" in data:
                actions = []
                for action_data in data["actions"]:
                    actions.append(Action(
                        type=ActionType(action_data["type"]),
                        target_id=action_data["target_id"],
                        control_id=action_data.get("control_id"),
                        value=action_data.get("value"),
                    ))

            # Update rule
            updated_rule = rule_engine.update_rule(
                rule_id,
                name=data.get("name"),
                trigger=trigger,
                actions=actions,
                enabled=data.get("enabled"),
            )

            return jsonify(updated_rule.to_dict())

        except (KeyError, ValueError) as e:
            return jsonify({"error": f"Invalid data: {e}"}), 400

    @app.route("/api/rules/<rule_id>", methods=["DELETE"])
    def api_rule_delete(rule_id: str) -> Any:
        """Delete a rule."""
        if not rule_engine:
            return jsonify({"error": "Rule engine not available"}), 503

        if rule_engine.delete_rule(rule_id):
            return jsonify({"status": "ok"})
        return jsonify({"error": "Rule not found"}), 404

    @app.route("/api/rules/<rule_id>/enable", methods=["POST"])
    def api_rule_enable(rule_id: str) -> Any:
        """Enable a rule."""
        if not rule_engine:
            return jsonify({"error": "Rule engine not available"}), 503

        if rule_engine.enable_rule(rule_id):
            return jsonify({"status": "ok"})
        return jsonify({"error": "Rule not found"}), 404

    @app.route("/api/rules/<rule_id>/disable", methods=["POST"])
    def api_rule_disable(rule_id: str) -> Any:
        """Disable a rule."""
        if not rule_engine:
            return jsonify({"error": "Rule engine not available"}), 503

        if rule_engine.disable_rule(rule_id):
            return jsonify({"status": "ok"})
        return jsonify({"error": "Rule not found"}), 404

    @app.route("/api/rules/bulk-action", methods=["POST"])
    def api_rules_bulk_action() -> Any:
        """Bulk enable/disable/delete rules."""
        if not rule_engine:
            return jsonify({"error": "Rule engine not available"}), 503

        data = request.json
        action = data.get("action")
        ids = data.get("ids", [])

        if action not in ("enable", "disable", "delete"):
            return jsonify({"error": "Invalid action"}), 400

        results = {}
        for rule_id in ids:
            try:
                if action == "enable":
                    rule_engine.enable_rule(rule_id)
                    results[rule_id] = "ok"
                elif action == "disable":
                    rule_engine.disable_rule(rule_id)
                    results[rule_id] = "ok"
                elif action == "delete":
                    rule_engine.delete_rule(rule_id)
                    results[rule_id] = "ok"
            except Exception as e:
                results[rule_id] = str(e)

        return jsonify({"status": "ok", "results": results})

    @app.route("/api/rules/action-types")
    def api_rule_action_types() -> Any:
        """List available action types."""
        return jsonify([
            {"type": t.value, "name": t.name.replace("_", " ").title()}
            for t in ActionType
        ])

    @app.route("/api/rules/trigger-types")
    def api_rule_trigger_types() -> Any:
        """List available trigger types."""
        return jsonify([
            {"type": t.value, "name": t.name.replace("_", " ").title()}
            for t in TriggerType
        ])

    @app.route("/api/rules/comparison-ops")
    def api_rule_comparison_ops() -> Any:
        """List available comparison operators."""
        return jsonify([
            {"value": c.value, "name": c.name.replace("_", " ").title()}
            for c in ComparisonOp
        ])

    return app
