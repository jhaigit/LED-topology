"""Routing engine for managing source-to-sink connections."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

import numpy as np

from libltp import (
    ControlClient,
    DataReceiver,
    DataSender,
    DataPacket,
    stream_control,
    stream_setup,
    subscribe,
)
from libltp.types import ColorFormat, Encoding, ScaleMode, StreamAction

from ltp_controller.controller import Controller, DeviceState

logger = logging.getLogger(__name__)


class RouteMode(str, Enum):
    """Route data flow mode."""

    PROXY = "proxy"  # Controller receives and forwards data
    DIRECT = "direct"  # Source sends directly to sink (controller monitors)


class RouteStatus(str, Enum):
    """Route connection status."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class RouteTransform:
    """Transformation to apply to routed data."""

    scale_mode: ScaleMode = ScaleMode.FIT
    brightness: float = 1.0
    gamma: float = 1.0
    mirror_x: bool = False
    mirror_y: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "scale_mode": self.scale_mode.value,
            "brightness": self.brightness,
            "gamma": self.gamma,
            "mirror_x": self.mirror_x,
            "mirror_y": self.mirror_y,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RouteTransform":
        return cls(
            scale_mode=ScaleMode(data.get("scale_mode", "fit")),
            brightness=data.get("brightness", 1.0),
            gamma=data.get("gamma", 1.0),
            mirror_x=data.get("mirror_x", False),
            mirror_y=data.get("mirror_y", False),
        )


@dataclass
class Route:
    """A route between a source and a sink."""

    id: str
    name: str
    source_id: str
    sink_id: str
    enabled: bool = True
    mode: RouteMode = RouteMode.PROXY
    transform: RouteTransform = field(default_factory=RouteTransform)
    status: RouteStatus = RouteStatus.DISCONNECTED
    error_message: str | None = None
    created_at: datetime = field(default_factory=datetime.now)

    # Runtime state (not serialized)
    _source_client: ControlClient | None = field(default=None, repr=False)
    _sink_client: ControlClient | None = field(default=None, repr=False)
    _receiver: DataReceiver | None = field(default=None, repr=False)
    _sender: DataSender | None = field(default=None, repr=False)
    _source_stream_id: str | None = field(default=None, repr=False)
    _sink_stream_id: str | None = field(default=None, repr=False)
    _frames_routed: int = field(default=0, repr=False)
    _last_frame_time: datetime | None = field(default=None, repr=False)
    _last_frame: list | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source_id": self.source_id,
            "sink_id": self.sink_id,
            "enabled": self.enabled,
            "mode": self.mode.value,
            "transform": self.transform.to_dict(),
            "status": self.status.value,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "frames_routed": self._frames_routed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Route":
        transform = RouteTransform()
        if "transform" in data:
            transform = RouteTransform.from_dict(data["transform"])

        return cls(
            id=data.get("id", str(uuid4())),
            name=data["name"],
            source_id=data["source_id"],
            sink_id=data["sink_id"],
            enabled=data.get("enabled", True),
            mode=RouteMode(data.get("mode", "proxy")),
            transform=transform,
        )


class RoutingEngine:
    """Manages routes between sources and sinks."""

    def __init__(self, controller: Controller):
        self.controller = controller
        self._routes: dict[str, Route] = {}
        self._running = False
        self._route_tasks: dict[str, asyncio.Task] = {}
        self._pending_starts: set[str] = set()
        self._pending_stops: set[str] = set()
        self._monitor_task: asyncio.Task | None = None

    @property
    def routes(self) -> list[Route]:
        """Get all routes."""
        return list(self._routes.values())

    @property
    def active_routes(self) -> list[Route]:
        """Get enabled and connected routes."""
        return [
            r
            for r in self._routes.values()
            if r.enabled and r.status == RouteStatus.CONNECTED
        ]

    def route_exists(self, source_id: str, sink_id: str) -> bool:
        """Check if a route already exists for this source/sink pair."""
        for route in self._routes.values():
            if route.source_id == source_id and route.sink_id == sink_id:
                return True
        return False

    def create_route(
        self,
        name: str,
        source_id: str,
        sink_id: str,
        mode: RouteMode = RouteMode.PROXY,
        transform: RouteTransform | None = None,
        enabled: bool = True,
    ) -> Route | None:
        """Create a new route. Returns None if route already exists."""
        # Check for duplicate
        if self.route_exists(source_id, sink_id):
            logger.warning(f"Route already exists: {source_id} -> {sink_id}")
            return None

        route_id = str(uuid4())[:8]

        route = Route(
            id=route_id,
            name=name,
            source_id=source_id,
            sink_id=sink_id,
            mode=mode,
            transform=transform or RouteTransform(),
            enabled=enabled,
        )

        self._routes[route_id] = route
        logger.info(f"Created route: {name} ({source_id} -> {sink_id})")

        # Mark for starting (will be picked up by _check_pending_routes)
        if self._running and enabled:
            self._pending_starts.add(route_id)

        return route

    def get_route(self, route_id: str) -> Route | None:
        """Get a route by ID."""
        return self._routes.get(route_id)

    def update_route(
        self,
        route_id: str,
        name: str | None = None,
        enabled: bool | None = None,
        transform: RouteTransform | None = None,
    ) -> Route | None:
        """Update a route's configuration."""
        route = self._routes.get(route_id)
        if not route:
            return None

        if name is not None:
            route.name = name
        if transform is not None:
            route.transform = transform

        if enabled is not None and enabled != route.enabled:
            route.enabled = enabled
            if self._running:
                if enabled:
                    # Mark for starting
                    self._pending_starts.add(route_id)
                else:
                    # Mark for stopping
                    self._pending_stops.add(route_id)

        return route

    async def delete_route(self, route_id: str) -> bool:
        """Delete a route."""
        route = self._routes.pop(route_id, None)
        if not route:
            return False

        await self._stop_route(route)
        logger.info(f"Deleted route: {route.name}")
        return True

    async def enable_route(self, route_id: str) -> bool:
        """Enable a route."""
        route = self._routes.get(route_id)
        if not route:
            return False

        if not route.enabled:
            route.enabled = True
            if self._running:
                self._pending_starts.add(route_id)
        return True

    async def disable_route(self, route_id: str) -> bool:
        """Disable a route."""
        route = self._routes.get(route_id)
        if not route:
            return False

        if route.enabled:
            route.enabled = False
            if self._running:
                self._pending_stops.add(route_id)
        return True

    async def _run_route(self, route: Route) -> None:
        """Run a route's data flow."""
        route.status = RouteStatus.CONNECTING
        route.error_message = None

        try:
            # Get source and sink devices
            source = self.controller.get_source(route.source_id)
            sink = self.controller.get_sink(route.sink_id)

            if not source:
                raise ValueError(f"Source not found: {route.source_id}")
            if not sink:
                raise ValueError(f"Sink not found: {route.sink_id}")
            if not source.online:
                raise ValueError(f"Source is offline: {source.name}")
            if not sink.online:
                raise ValueError(f"Sink is offline: {sink.name}")

            if route.mode == RouteMode.PROXY:
                await self._run_proxy_route(route, source, sink)
            else:
                await self._run_direct_route(route, source, sink)

        except asyncio.CancelledError:
            logger.info(f"Route {route.name} cancelled")
            route.status = RouteStatus.DISCONNECTED
        except asyncio.TimeoutError:
            logger.error(f"Route {route.name} error: Connection timeout")
            route.status = RouteStatus.ERROR
            route.error_message = "Connection timeout"
        except Exception as e:
            import traceback
            logger.error(f"Route {route.name} error: {type(e).__name__}: {e}")
            logger.debug(traceback.format_exc())
            route.status = RouteStatus.ERROR
            route.error_message = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        finally:
            await self._cleanup_route(route)

    async def _run_proxy_route(
        self, route: Route, source: DeviceState, sink: DeviceState
    ) -> None:
        """Run a proxy route where controller forwards data."""
        logger.info(f"Starting proxy route: {route.name}")

        # Connect to sink
        route._sink_client = ControlClient(sink.host, sink.port)
        await route._sink_client.connect()

        # Set up stream to sink
        sink_dims = self._get_dimensions(sink)
        setup_req = stream_setup(0, ColorFormat.RGB, Encoding.RAW)
        setup_resp = await route._sink_client.request(setup_req)

        if setup_resp.data.get("status") != "ok":
            raise ValueError(f"Sink stream setup failed: {setup_resp.data}")

        sink_udp_port = setup_resp.data["udp_port"]
        route._sink_stream_id = setup_resp.data["stream_id"]

        # Start data sender to sink
        route._sender = DataSender(sink.host, sink_udp_port)
        await route._sender.start()

        # Start stream on sink
        start_req = stream_control(0, route._sink_stream_id, StreamAction.START)
        await route._sink_client.request(start_req)

        # Start data receiver FIRST to get the port for callback
        source_dims = self._get_dimensions(source)
        route._receiver = DataReceiver("0.0.0.0", 0)

        def on_data(packet: DataPacket) -> None:
            self._handle_packet(route, packet, source_dims, sink_dims)

        route._receiver.handler = on_data
        await route._receiver.start()

        receiver_port = route._receiver.actual_port

        # Get our local IP address (the one the source can reach us on)
        local_ip = self._get_local_ip(source.host)

        # Connect to source
        route._source_client = ControlClient(source.host, source.port)
        await route._source_client.connect()

        # Subscribe to source with callback address
        logger.info(f"Subscribing to source with callback {local_ip}:{receiver_port}")
        sub_req = subscribe(
            0, source_dims, "rgb", 30,
            callback_host=local_ip,
            callback_port=receiver_port,
        )
        sub_resp = await route._source_client.request(sub_req)

        if sub_resp.data.get("status") != "ok":
            raise ValueError(f"Source subscribe failed: {sub_resp.data}")

        route._source_stream_id = sub_resp.data["stream_id"]

        route.status = RouteStatus.CONNECTED
        logger.info(
            f"Route {route.name} connected: "
            f"{source.name} -> controller:{receiver_port} -> {sink.name}:{sink_udp_port}"
        )

        # Keep running until cancelled
        while route.enabled:
            await asyncio.sleep(1.0)

    async def _run_direct_route(
        self, route: Route, source: DeviceState, sink: DeviceState
    ) -> None:
        """Run a direct route where source sends directly to sink."""
        logger.info(f"Starting direct route: {route.name}")

        # Connect to sink and set up stream
        route._sink_client = ControlClient(sink.host, sink.port)
        await route._sink_client.connect()

        setup_req = stream_setup(0, ColorFormat.RGB, Encoding.RAW)
        setup_resp = await route._sink_client.request(setup_req)

        if setup_resp.data.get("status") != "ok":
            raise ValueError(f"Sink stream setup failed: {setup_resp.data}")

        sink_udp_port = setup_resp.data["udp_port"]
        route._sink_stream_id = setup_resp.data["stream_id"]

        # Start stream on sink
        start_req = stream_control(0, route._sink_stream_id, StreamAction.START)
        await route._sink_client.request(start_req)

        # Connect to source and tell it to stream to sink
        route._source_client = ControlClient(source.host, source.port)
        await route._source_client.connect()

        # Subscribe with sink's UDP port as target
        sink_dims = self._get_dimensions(sink)
        sub_req = subscribe(0, sink_dims, "rgb", 30)
        sub_resp = await route._source_client.request(sub_req)

        if sub_resp.data.get("status") != "ok":
            raise ValueError(f"Source subscribe failed: {sub_resp.data}")

        route._source_stream_id = sub_resp.data["stream_id"]

        route.status = RouteStatus.CONNECTED
        logger.info(
            f"Direct route {route.name} connected: "
            f"{source.name} -> {sink.name}:{sink_udp_port}"
        )

        # Keep running until cancelled
        while route.enabled:
            await asyncio.sleep(1.0)

    def _handle_packet(
        self,
        route: Route,
        packet: DataPacket,
        source_dims: list[int],
        sink_dims: list[int],
    ) -> None:
        """Handle an incoming packet and forward to sink."""
        try:
            # Apply transforms
            pixels = packet.pixel_data

            # Scale if dimensions differ
            if source_dims != sink_dims:
                pixels = self._scale_pixels(pixels, source_dims, sink_dims, route.transform)

            # Apply brightness
            if route.transform.brightness != 1.0:
                pixels = (pixels * route.transform.brightness).astype(np.uint8)

            # Apply gamma
            if route.transform.gamma != 1.0:
                normalized = pixels / 255.0
                corrected = np.power(normalized, route.transform.gamma)
                pixels = (corrected * 255).astype(np.uint8)

            # Store for preview (convert to list for JSON serialization)
            route._last_frame = pixels.tolist()

            # Send to sink
            if route._sender:
                route._sender.send(pixels, packet.color_format, packet.encoding)
                route._frames_routed += 1
                route._last_frame_time = datetime.now()

        except Exception as e:
            logger.warning(f"Error forwarding packet for route {route.name}: {e}")

    def _scale_pixels(
        self,
        pixels: np.ndarray,
        source_dims: list[int],
        sink_dims: list[int],
        transform: RouteTransform,
    ) -> np.ndarray:
        """Scale pixel data from source to sink dimensions."""
        from libltp import scale_buffer

        source_count = np.prod(source_dims)
        sink_count = np.prod(sink_dims)

        if source_count == sink_count:
            return pixels

        # Simple linear scaling for now
        if len(source_dims) == 1 and len(sink_dims) == 1:
            # Linear to linear
            result = scale_buffer(pixels, sink_dims[0], transform.scale_mode)
            return result

        # For matrices, reshape and scale
        # This is a simplified version
        if pixels.shape[0] == source_count:
            # Already flat, just resize
            from scipy.ndimage import zoom

            factor = sink_count / source_count
            if pixels.ndim == 2:
                result = zoom(pixels, (factor, 1), order=1)
            else:
                result = zoom(pixels, factor, order=1)
            return result[:sink_count].astype(np.uint8)

        return pixels

    def _get_local_ip(self, remote_host: str) -> str:
        """Get local IP address that can reach the remote host."""
        import socket

        try:
            # Create a socket to determine our local IP that routes to the remote host
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0)
            # Doesn't actually connect, just figures out routing
            sock.connect((remote_host, 1))
            local_ip = sock.getsockname()[0]
            sock.close()
            return local_ip
        except Exception:
            # Fallback to getting hostname IP
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return "127.0.0.1"

    def _get_dimensions(self, device: DeviceState) -> list[int]:
        """Get pixel dimensions from device."""
        props = device.device.properties

        # Try dim property (e.g., "60" or "16x16")
        if "dim" in props:
            dim_str = props["dim"]
            return [int(d) for d in dim_str.split("x")]

        # Try pixels property
        if "pixels" in props:
            return [int(props["pixels"])]

        # Try output property (for sources)
        if "output" in props:
            output_str = props["output"]
            return [int(d) for d in output_str.split("x")]

        # Default
        return [60]

    async def _stop_route(self, route: Route) -> None:
        """Stop a route."""
        # Cancel the task if running
        task = self._route_tasks.pop(route.id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await self._cleanup_route(route)

    async def _cleanup_route(self, route: Route) -> None:
        """Clean up route resources."""
        # Stop receiver
        if route._receiver:
            await route._receiver.stop()
            route._receiver = None

        # Stop sender
        if route._sender:
            await route._sender.stop()
            route._sender = None

        # Stop streams and close connections
        if route._sink_client and route._sink_stream_id:
            try:
                stop_req = stream_control(0, route._sink_stream_id, StreamAction.STOP)
                await route._sink_client.request(stop_req, timeout=2.0)
            except Exception:
                pass

        if route._source_client:
            try:
                await route._source_client.close()
            except Exception:
                pass
            route._source_client = None

        if route._sink_client:
            try:
                await route._sink_client.close()
            except Exception:
                pass
            route._sink_client = None

        route._source_stream_id = None
        route._sink_stream_id = None
        route.status = RouteStatus.DISCONNECTED

        logger.info(f"Route {route.name} cleaned up")

    async def _monitor_loop(self) -> None:
        """Monitor for pending route starts/stops from sync context."""
        while self._running:
            # Process pending starts
            while self._pending_starts:
                route_id = self._pending_starts.pop()
                route = self._routes.get(route_id)
                if route and route.enabled and route_id not in self._route_tasks:
                    self._route_tasks[route_id] = asyncio.create_task(
                        self._run_route(route)
                    )

            # Process pending stops
            while self._pending_stops:
                route_id = self._pending_stops.pop()
                route = self._routes.get(route_id)
                if route:
                    await self._stop_route(route)

            await asyncio.sleep(0.1)

    async def start(self) -> None:
        """Start the routing engine."""
        if self._running:
            return

        self._running = True

        # Start enabled routes
        for route in self._routes.values():
            if route.enabled:
                self._route_tasks[route.id] = asyncio.create_task(
                    self._run_route(route)
                )

        # Start monitor loop for handling routes from sync context
        self._monitor_task = asyncio.create_task(self._monitor_loop())

        logger.info("Routing engine started")

    async def stop(self) -> None:
        """Stop the routing engine."""
        if not self._running:
            return

        self._running = False

        # Stop monitor task
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        # Stop all routes
        for route in self._routes.values():
            await self._stop_route(route)

        self._route_tasks.clear()
        self._pending_starts.clear()
        self._pending_stops.clear()
        logger.info("Routing engine stopped")

    def load_routes(self, routes_data: list[dict[str, Any]]) -> None:
        """Load routes from configuration data."""
        for data in routes_data:
            try:
                route = Route.from_dict(data)
                self._routes[route.id] = route
                logger.info(f"Loaded route: {route.name}")
            except Exception as e:
                logger.error(f"Failed to load route: {e}")

    def save_routes(self) -> list[dict[str, Any]]:
        """Save routes to configuration data."""
        return [route.to_dict() for route in self._routes.values()]
