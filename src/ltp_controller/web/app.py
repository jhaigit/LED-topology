"""Flask web application for LTP Controller."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from ltp_controller.controller import Controller
from ltp_controller.router import Route, RouteMode, RouteTransform, RoutingEngine

logger = logging.getLogger(__name__)


def create_app(controller: Controller, router: RoutingEngine) -> Flask:
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

    # Helper to run async code
    def run_async(coro: Any) -> Any:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(coro)

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

    # ==================== API: Routes ====================

    @app.route("/api/routes", methods=["GET", "POST"])
    def api_routes() -> Any:
        """List or create routes."""
        if request.method == "GET":
            return jsonify([r.to_dict() for r in router.routes])

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

        return jsonify(route.to_dict()), 201

    @app.route("/api/routes/<route_id>", methods=["GET", "PUT", "DELETE"])
    def api_route(route_id: str) -> Any:
        """Get, update, or delete a route."""
        route = router.get_route(route_id)
        if not route:
            return jsonify({"error": "Route not found"}), 404

        if request.method == "GET":
            return jsonify(route.to_dict())

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

    return app
