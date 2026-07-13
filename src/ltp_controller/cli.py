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
from ltp_controller.input_manager import InputEventManager
from ltp_controller.router import RouteMode, RouteTransform, RoutingEngine
from ltp_controller.rule_engine import RuleEngine
from ltp_controller.rules import Action, ActionType, ComparisonOp, Trigger, TriggerType
from ltp_controller.scalar_sources import ScalarSourceManager, SCALAR_SOURCE_TYPES
from ltp_controller.sequence import SequenceManager
from ltp_controller.sink_connection_pool import SinkConnectionPool
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


def resolve_web_security(
    args: argparse.Namespace, web_config: dict[str, Any], web_host: str
) -> tuple[Any, Any, str]:
    """Apply the startup security matrix (implementation plan §Transport modes).

    Returns (WebSecuritySettings, ssl_context-or-None, scheme). Raises
    SecurityConfigError for combinations the operator has not explicitly
    accepted.
    """
    from ltp_controller.security import (
        AuthManager,
        WebSecuritySettings,
        default_config_dir,
        ensure_secret_key,
        ensure_self_signed_cert,
        resolve_transport,
    )

    auth_config = web_config.get("auth", {})
    # Auth defaults on when the block carries any credentials; an absent or
    # empty block means auth off (backward compatible — the transport matrix
    # decides whether that is acceptable for this bind address).
    has_credentials = bool(
        auth_config.get("users") or auth_config.get("tokens") or auth_config.get("password_hash")
    )
    auth_enabled = auth_config.get("enabled", has_credentials)

    tls_config = web_config.get("tls", {})
    tls_mode = args.tls or tls_config.get("mode", "auto")
    allow_insecure = args.insecure_http or web_config.get("allow_insecure_http", False)

    decision = resolve_transport(
        host=web_host,
        tls_mode=tls_mode,
        trust_proxy=tls_config.get("trust_proxy", False),
        allow_insecure_http=allow_insecure,
        auth_enabled=auth_enabled,
    )
    for warning in decision.warnings:
        logger.warning(warning)

    auth = AuthManager.from_config(auth_config) if auth_enabled else None

    ssl_context = None
    if decision.use_tls:
        config_dir = default_config_dir()
        cert = Path(
            args.tls_cert or tls_config.get("cert", config_dir / "web-cert.pem")
        ).expanduser()
        key = Path(args.tls_key or tls_config.get("key", config_dir / "web-key.pem")).expanduser()
        ensure_self_signed_cert(cert, key)
        ssl_context = (str(cert), str(key))

    # Reading system state is open by default (no login to view); set
    # web.public_read: false to require a login even to look. Mutations and key
    # management always require the appropriate role regardless.
    public_read = bool(web_config.get("public_read", True))

    settings = WebSecuritySettings(
        auth=auth,
        secret_key=ensure_secret_key() if auth else None,
        secure_cookies=decision.secure_cookies,
        insecure_transport=decision.insecure_transport,
        trust_proxy=decision.trust_proxy,
        public_read=public_read,
    )
    return settings, ssl_context, decision.scheme


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="LTP Controller - Discovery and routing controller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--config",
        "-c",
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
        default=None,
        help="Web interface port (default: 8080)",
    )
    parser.add_argument(
        "--web-host",
        type=str,
        default=None,
        help="Web interface host (default: 127.0.0.1; set explicitly to expose)",
    )
    parser.add_argument(
        "--insecure-http",
        action="store_true",
        help="Allow plain HTTP (and/or no auth) on a network-reachable address. "
        "Credentials will cross the network in cleartext.",
    )
    parser.add_argument(
        "--tls",
        choices=["auto", "on", "off"],
        default=None,
        help="TLS mode for the web interface (default: auto = on when host is " "non-loopback)",
    )
    parser.add_argument(
        "--tls-cert",
        type=str,
        default=None,
        help="Path to TLS certificate (default: auto-generated self-signed)",
    )
    parser.add_argument(
        "--tls-key",
        type=str,
        default=None,
        help="Path to TLS private key",
    )
    parser.add_argument(
        "--hash-password",
        action="store_true",
        help="Prompt for a password, print its hash for web.auth, and exit",
    )
    parser.add_argument(
        "--generate-token",
        action="store_true",
        help="Generate an API bearer token, print it with its hash for "
        "web.auth.tokens, and exit",
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
    connection_pool: SinkConnectionPool,
    scalar_source_manager: ScalarSourceManager | None = None,
    input_manager: InputEventManager | None = None,
    rule_engine: RuleEngine | None = None,
    sequence_manager: SequenceManager | None = None,
    web_enabled: bool = True,
    web_host: str = "127.0.0.1",
    web_port: int = 8080,
    config_path: str | None = None,
    web_security: Any = None,
    web_ssl_context: Any = None,
    web_scheme: str = "http",
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

        # Start connection pool (after controller so it can register for sink discovery)
        await connection_pool.start()

        # Start routing engine
        await router.start()

        # Start virtual source manager
        virtual_source_manager.start()

        # Start scalar sources
        if scalar_source_manager:
            await scalar_source_manager.start_all()

        # Start input event manager
        if input_manager:
            await input_manager.start()

        # Start rule engine
        if rule_engine:
            rule_engine.set_event_loop(loop)
            rule_engine.start()

        # Start web interface in a separate thread
        web_thread = None
        if web_enabled:
            from libltp.identity import Identity
            from ltp_controller.fleet_manager import FleetStore
            from ltp_controller.web import create_app

            # Fleet enrollment (Phase 5.1): the controller's static identity and
            # its pinned-fleet trust store, both persisted 0600 under ~/.config/ltp.
            fleet_identity = Identity.load_or_create(name="controller-identity")
            fleet_store = FleetStore()
            fleet_store.load()

            # Pass the event loop so Flask can schedule async work on it
            app = create_app(
                controller,
                router,
                sink_controller,
                virtual_source_manager=virtual_source_manager,
                scalar_source_manager=scalar_source_manager,
                input_manager=input_manager,
                rule_engine=rule_engine,
                sequence_manager=sequence_manager,
                event_loop=loop,
                config_path=config_path,
                web_security=web_security,
                keystore=connection_pool.keystore,
                fleet_store=fleet_store,
                fleet_identity=fleet_identity,
            )

            def run_web() -> None:
                app.run(
                    host=web_host,
                    port=web_port,
                    threaded=True,
                    use_reloader=False,
                    ssl_context=web_ssl_context,
                )

            web_thread = threading.Thread(target=run_web, daemon=True)
            web_thread.start()
            logger.info(f"Web interface available at {web_scheme}://{web_host}:{web_port}")

        logger.info("Controller running. Press Ctrl+C to stop.")

        # Wait for stop signal
        await stop_event.wait()

    finally:
        # Cleanup
        if sequence_manager:
            await sequence_manager.stop_all()
        if rule_engine:
            rule_engine.stop()
        if input_manager:
            await input_manager.stop()
        if scalar_source_manager:
            await scalar_source_manager.stop_all()
        virtual_source_manager.stop()
        await sink_controller.cleanup_all()
        await router.stop()
        await connection_pool.stop()
        await controller.stop()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Credential utilities: print and exit, never log the secret.
    if args.hash_password:
        import getpass

        from ltp_controller.security import hash_password

        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
        print(hash_password(password))
        return 0

    if args.generate_token:
        from ltp_controller.security import generate_token

        token, token_hash = generate_token()
        print(f"Token (give to the client, shown only once): {token}")
        print(f"Hash (put in web.auth.tokens):               {token_hash}")
        return 0

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

    # Get web configuration. Default bind is loopback-only (fail closed);
    # exposing to the network requires TLS+auth or an explicit
    # allow_insecure_http acknowledgment — see resolve_web_security().
    web_config = config.get("web", {})
    web_enabled = not args.no_web and not args.cli and web_config.get("enabled", True)
    web_host = args.web_host or web_config.get("host", "127.0.0.1")
    web_port = args.web_port or web_config.get("port", 8080)

    web_security = None
    web_ssl_context = None
    web_scheme = "http"
    if web_enabled:
        try:
            web_security, web_ssl_context, web_scheme = resolve_web_security(
                args, web_config, web_host
            )
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Create controller
    controller = Controller(
        name=name,
        display_name=display_name,
        description=description,
        device_id=device_id,
        health_check_interval=config.get("discovery", {}).get("health_check_interval", 10.0),
    )

    # Load the device keystore (Layer 2 PSKs) and wire it into the pool so
    # sinks that advertise auth get claimed on connect.
    from ltp_controller.keystore import KeyStore

    keystore = KeyStore()
    keystore.load()

    # Create shared connection pool for sinks
    connection_pool = SinkConnectionPool(controller, keystore=keystore)

    # Wire pool to controller
    controller.set_connection_pool(connection_pool)

    # Load sink groups
    sg_config = config.get("sink_groups", [])
    if sg_config:
        controller.sink_group_manager.load_from_config(sg_config)
        logger.info(f"Loaded {len(sg_config)} sink groups from config")

    # Create virtual source manager
    virtual_source_manager = VirtualSourceManager()

    # Load pre-configured virtual sources
    vs_config = config.get("virtual_sources", [])
    if vs_config:
        virtual_source_manager.load_from_config(vs_config)
        logger.info(f"Loaded {len(vs_config)} virtual sources from config")

    # Create routing engine with virtual source manager and pool
    router = RoutingEngine(controller, virtual_source_manager, connection_pool)

    # Create sink controller for direct fills (with pool)
    sink_controller = SinkController(controller, connection_pool)

    # Create input event manager (with pool)
    input_manager = InputEventManager(controller, connection_pool)

    # Create sequence manager
    sequence_manager = SequenceManager()

    # Load pre-configured sequences
    seq_config = config.get("sequences", [])
    if seq_config:
        sequence_manager.load_from_config(seq_config)
        logger.info(f"Loaded {len(seq_config)} sequences from config")

    # Create rule engine (needs to be wired after all managers exist)
    rule_engine = RuleEngine(
        controller=controller,
        router=router,
        virtual_source_manager=virtual_source_manager,
        input_manager=input_manager,
        sink_controller=sink_controller,
        sequence_manager=sequence_manager,
    )

    # Wire sequence manager to use rule engine's action executor
    sequence_manager.set_action_executor(rule_engine._execute_action)

    # Load pre-configured rules
    rules_config = config.get("rules", [])
    if rules_config:
        rule_engine.load_from_config(rules_config)
        logger.info(f"Loaded {len(rules_config)} rules from config")

    # Create scalar source manager
    scalar_source_manager = ScalarSourceManager()

    # Load pre-configured scalar sources
    ss_config = config.get("scalar_sources", [])
    for ss_data in ss_config:
        try:
            source_type = ss_data.get("type")
            if source_type not in SCALAR_SOURCE_TYPES:
                logger.error(f"Unknown scalar source type: {source_type}")
                continue

            from ltp_controller.scalar_sources import ScalarSourceConfig

            ss_config_obj = ScalarSourceConfig(
                name=ss_data.get("name", f"Scalar Source"),
                description=ss_data.get("description", ""),
                sample_rate=ss_data.get("sample_rate", 1.0),
                enabled=ss_data.get("enabled", True),
            )
            source_class = SCALAR_SOURCE_TYPES[source_type]
            source = source_class(ss_config_obj)
            scalar_source_manager.add(source)
            logger.info(f"Created scalar source: {ss_config_obj.name}")
        except Exception as e:
            logger.error(f"Failed to create scalar source: {e}")

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
                connection_pool=connection_pool,
                scalar_source_manager=scalar_source_manager,
                input_manager=input_manager,
                rule_engine=rule_engine,
                sequence_manager=sequence_manager,
                web_enabled=web_enabled,
                web_host=web_host,
                web_port=web_port,
                config_path=args.config,
                web_security=web_security,
                web_ssl_context=web_ssl_context,
                web_scheme=web_scheme,
            )
        )
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
