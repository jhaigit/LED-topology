"""Command line interface for ltp-controller."""

import argparse
import asyncio
import logging
import signal
import sys
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

from ltp_controller.controller import Controller
from ltp_controller.router import RouteMode, RouteTransform, RoutingEngine
from ltp_controller.sink_control import SinkController
from ltp_controller.virtual_sources import VirtualSourceManager

logger = logging.getLogger("ltp_controller")


def setup_logging(level: str = "info", log_file: str | None = None) -> None:
    """Configure logging."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )


def load_config(path: str) -> dict[str, Any]:
    """Load configuration from YAML file."""
    with open(path) as f:
        return yaml.safe_load(f) or {}


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="LTP Controller - Discovery and routing controller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--config", "-c",
        type=str,
        help="Path to configuration file",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="ltp-controller",
        help="Controller service name (default: ltp-controller)",
    )
    parser.add_argument(
        "--display-name",
        type=str,
        default="LTP Controller",
        help="Human-readable display name",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=8080,
        help="Web interface port (default: 8080)",
    )
    parser.add_argument(
        "--web-host",
        type=str,
        default="0.0.0.0",
        help="Web interface host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Disable web interface",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in CLI mode (no web interface)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Logging level (default: info)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        help="Log file path",
    )

    return parser.parse_args()


async def run_controller(
    controller: Controller,
    router: RoutingEngine,
    sink_controller: SinkController,
    virtual_source_manager: VirtualSourceManager,
    web_enabled: bool = True,
    web_host: str = "0.0.0.0",
    web_port: int = 8080,
) -> None:
    """Run the controller."""
    stop_event = asyncio.Event()

    def signal_handler() -> None:
        logger.info("Shutdown requested...")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        # Start controller
        await controller.start()

        # Start routing engine
        await router.start()

        # Start virtual source manager
        virtual_source_manager.start()

        # Start web interface in a separate thread
        web_thread = None
        if web_enabled:
            from ltp_controller.web import create_app

            # Pass the event loop so Flask can schedule async work on it
            app = create_app(
                controller, router, sink_controller,
                virtual_source_manager=virtual_source_manager,
                event_loop=loop,
            )

            def run_web() -> None:
                app.run(host=web_host, port=web_port, threaded=True, use_reloader=False)

            web_thread = threading.Thread(target=run_web, daemon=True)
            web_thread.start()
            logger.info(f"Web interface available at http://{web_host}:{web_port}")

        logger.info("Controller running. Press Ctrl+C to stop.")

        # Wait for stop signal
        await stop_event.wait()

    finally:
        # Cleanup
        virtual_source_manager.stop()
        await sink_controller.cleanup_all()
        await router.stop()
        await controller.stop()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Load config file if specified
    config: dict[str, Any] = {}
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            config = load_config(str(config_path))
            logger.info(f"Loaded config from {config_path}")
        else:
            print(f"Error: Config file not found: {config_path}", file=sys.stderr)
            return 1

    # Setup logging
    log_config = config.get("logging", {})
    setup_logging(
        level=args.log_level or log_config.get("level", "info"),
        log_file=args.log_file or log_config.get("file"),
    )

    # Get device configuration
    device_config = config.get("device", {})
    name = args.name or device_config.get("name", "ltp-controller")
    display_name = args.display_name or device_config.get("display_name", "LTP Controller")
    description = device_config.get("description", "Central routing controller")

    device_id = None
    if device_config.get("id") and device_config["id"] != "auto":
        try:
            device_id = UUID(device_config["id"])
        except ValueError:
            pass

    # Get web configuration
    web_config = config.get("web", {})
    web_enabled = not args.no_web and not args.cli and web_config.get("enabled", True)
    web_host = args.web_host or web_config.get("host", "0.0.0.0")
    web_port = args.web_port or web_config.get("port", 8080)

    # Create controller
    controller = Controller(
        name=name,
        display_name=display_name,
        description=description,
        device_id=device_id,
        health_check_interval=config.get("discovery", {}).get("health_check_interval", 10.0),
    )

    # Create virtual source manager
    virtual_source_manager = VirtualSourceManager()

    # Load pre-configured virtual sources
    vs_config = config.get("virtual_sources", [])
    if vs_config:
        virtual_source_manager.load_from_config(vs_config)
        logger.info(f"Loaded {len(vs_config)} virtual sources from config")

    # Create routing engine with virtual source manager
    router = RoutingEngine(controller, virtual_source_manager)

    # Create sink controller for direct fills
    sink_controller = SinkController(controller)

    # Load pre-configured routes
    routes_config = config.get("routes", [])
    for route_data in routes_config:
        try:
            transform = None
            if "transform" in route_data:
                transform = RouteTransform.from_dict(route_data["transform"])

            router.create_route(
                name=route_data["name"],
                source_id=route_data["source"],
                sink_id=route_data["sink"],
                mode=RouteMode(route_data.get("mode", "proxy")),
                transform=transform,
                enabled=route_data.get("enabled", True),
            )
        except Exception as e:
            logger.error(f"Failed to load route: {e}")

    # Run
    try:
        asyncio.run(
            run_controller(
                controller=controller,
                router=router,
                sink_controller=sink_controller,
                virtual_source_manager=virtual_source_manager,
                web_enabled=web_enabled,
                web_host=web_host,
                web_port=web_port,
            )
        )
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
