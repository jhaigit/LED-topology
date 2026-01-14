"""Flask web application for LTP Controller."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from ltp_controller.controller import Controller
from ltp_controller.router import Route, RouteMode, RouteTransform, RoutingEngine
from ltp_controller.sink_control import SinkController

logger = logging.getLogger(__name__)


def create_app(
    controller: Controller,
    router: RoutingEngine,
    sink_controller: SinkController | None = None,
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

    # ==================== API: Routes ====================

    def _enrich_route(route_dict: dict) -> dict:
        """Add source and sink names to route data."""
        source = controller.get_source(route_dict["source_id"])
        sink = controller.get_sink(route_dict["sink_id"])
        route_dict["source_name"] = source.name if source else "Unknown"
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
        )

    return app
