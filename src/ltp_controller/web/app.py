"""Flask web application for LTP Controller."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from ltp_controller.controller import Controller
from ltp_controller.router import Route, RouteMode, RouteTransform, RoutingEngine
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
    event_loop: asyncio.AbstractEventLoop | None = None,
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
    app.config["event_loop"] = event_loop

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
            success = run_async(controller.set_device_control(sink, control_id, value))
            results[control_id] = "ok" if success else "error"

        return jsonify({"status": "ok", "results": results})

    @app.route("/api/sinks/<sink_id>/refresh", methods=["POST"])
    def api_sink_refresh(sink_id: str) -> Any:
        """Refresh sink info."""
        sink = controller.get_sink(sink_id)
        if not sink:
            return jsonify({"error": "Sink not found"}), 404

        run_async(controller.refresh_device(sink))
        return jsonify({"status": "ok"})

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

        route = router.update_route(
            route_id,
            name=data.get("name"),
            enabled=data.get("enabled"),
            transform=transform,
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

    @app.route("/api/config/save", methods=["POST"])
    def api_config_save() -> Any:
        """Save current configuration to file.

        Request body: {"path": "/path/to/config.yaml"} or empty for default
        """
        import yaml

        data = request.get_json() or {}
        config_path = data.get("path")

        if not config_path:
            return jsonify({"error": "Config path not specified"}), 400

        config = {
            "virtual_sources": virtual_source_manager.to_config() if virtual_source_manager else [],
            "routes": [],
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

        try:
            with open(config_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            return jsonify({"status": "ok", "path": config_path})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ==================== API: Preview ====================

    @app.route("/api/routes/<route_id>/preview")
    def api_route_preview(route_id: str) -> Any:
        """Get LED preview for a route as SVG."""
        route = router.get_route(route_id)
        if not route:
            return jsonify({"error": "Route not found"}), 404

        # Get the last frame data if available
        if route._last_frame is None:
            # Return empty preview
            return _generate_led_svg([], 60)

        pixels = route._last_frame
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
        types = []
        for type_name, type_class in VIRTUAL_SOURCE_TYPES.items():
            types.append({
                "type": type_name,
                "name": type_name.replace("_", " ").title(),
                "category": "pattern" if "Pattern" in type_class.__name__ else "visualizer",
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

        # Update enabled state
        if "enabled" in data:
            source.config.enabled = data["enabled"]
            if data["enabled"] and not source.is_running:
                source.start()
            elif not data["enabled"] and source.is_running:
                source.stop()

        # Update control values
        if "control_values" in data:
            for control_id, value in data["control_values"].items():
                source.set_control(control_id, value)

        return jsonify(source.to_dict())

    @app.route("/api/virtual-sources/<source_id>", methods=["DELETE"])
    def api_virtual_source_delete(source_id: str) -> Any:
        """Delete a virtual source."""
        if not virtual_source_manager:
            return jsonify({"error": "Virtual sources not available"}), 503

        if virtual_source_manager.remove(source_id):
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

    return app
