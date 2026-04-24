"""Connection pool for shared sink connections."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from libltp import ControlClient, Message, MessageType
from libltp.addr import format_address_port

from ltp_controller.controller import Controller, DeviceState

logger = logging.getLogger(__name__)

# Type for unsolicited message callbacks (e.g., INPUT_EVENT)
UnsolicitedMessageCallback = Callable[[str, Message], None]  # sink_id, message


@dataclass
class PooledConnection:
    """A pooled connection to a sink."""

    sink_id: str
    client: ControlClient
    host: str
    port: int
    connected: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SinkConnectionPool:
    """Manages shared TCP connections to sinks.

    Provides a single connection per sink that is shared across all components
    (InputEventManager, Controller, SinkController, RoutingEngine).

    Features:
    - Single connection per sink
    - Automatic reconnection on failure
    - Request/response routing via sequence numbers
    - Unsolicited message routing (INPUT_EVENT) to registered listeners
    """

    def __init__(self, controller: Controller):
        self.controller = controller
        self._connections: dict[str, PooledConnection] = {}
        self._unsolicited_listeners: list[UnsolicitedMessageCallback] = []
        self._running = False
        self._reconnect_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def add_unsolicited_listener(self, callback: UnsolicitedMessageCallback) -> None:
        """Add a listener for unsolicited messages (e.g., INPUT_EVENT).

        Args:
            callback: Function called with (sink_id, message) for unsolicited messages
        """
        if callback not in self._unsolicited_listeners:
            self._unsolicited_listeners.append(callback)

    def remove_unsolicited_listener(self, callback: UnsolicitedMessageCallback) -> None:
        """Remove an unsolicited message listener."""
        if callback in self._unsolicited_listeners:
            self._unsolicited_listeners.remove(callback)

    async def start(self) -> None:
        """Start the connection pool."""
        if self._running:
            return

        self._running = True

        # Register for sink discovery callbacks
        self.controller.on_sink_change(self._on_sink_change)

        # Connect to all existing online sinks
        for sink in self.controller.online_sinks:
            asyncio.create_task(self._connect_to_sink(sink))

        # Start reconnection loop
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

        logger.info("Sink connection pool started")

    async def stop(self) -> None:
        """Stop the connection pool."""
        if not self._running:
            return

        self._running = False

        # Cancel reconnection task
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        # Disconnect from all sinks
        async with self._lock:
            for sink_id in list(self._connections.keys()):
                await self._disconnect_from_sink(sink_id)

        logger.info("Sink connection pool stopped")

    def _on_sink_change(self, sink: DeviceState, is_online: bool) -> None:
        """Handle sink discovery/removal events."""
        if is_online:
            asyncio.create_task(self._connect_to_sink(sink))
        else:
            asyncio.create_task(self._disconnect_from_sink(sink.id))

    async def _connect_to_sink(self, sink: DeviceState) -> None:
        """Establish a connection to a sink."""
        sink_id = sink.id

        async with self._lock:
            # Already connected?
            if sink_id in self._connections:
                conn = self._connections[sink_id]
                if conn.connected and conn.client.is_connected:
                    return
                # Clean up old connection
                await self._disconnect_from_sink(sink_id)

            if not sink.online:
                return

            try:
                client = ControlClient(
                    sink.host,
                    sink.port,
                    handler=lambda msg: self._handle_message(sink_id, msg),
                )
                await client.connect()

                conn = PooledConnection(
                    sink_id=sink_id,
                    client=client,
                    host=sink.host,
                    port=sink.port,
                    connected=True,
                )
                self._connections[sink_id] = conn
                logger.info(f"Pool: Connected to sink {sink.name} ({format_address_port(sink.host, sink.port)})")

            except Exception as e:
                logger.warning(f"Pool: Failed to connect to sink {sink.name}: {e}")

    async def _disconnect_from_sink(self, sink_id: str) -> None:
        """Disconnect from a sink."""
        conn = self._connections.pop(sink_id, None)
        if conn:
            conn.connected = False
            try:
                await conn.client.close()
            except Exception:
                pass
            logger.info(f"Pool: Disconnected from sink {sink_id}")

    async def _reconnect_loop(self) -> None:
        """Periodically check and reconnect to disconnected sinks."""
        while self._running:
            await asyncio.sleep(5.0)  # Check every 5 seconds

            for sink in self.controller.online_sinks:
                sink_id = sink.id
                if sink_id not in self._connections:
                    await self._connect_to_sink(sink)
                elif not self._connections[sink_id].client.is_connected:
                    # Connection dropped, reconnect
                    self._connections[sink_id].connected = False
                    await self._connect_to_sink(sink)

    def _handle_message(self, sink_id: str, message: Message) -> None:
        """Handle incoming messages from a sink.

        This is called for unsolicited messages (not responses to requests).
        Request responses are handled by the ControlClient's seq tracking.
        """
        # Dispatch to unsolicited listeners
        for listener in self._unsolicited_listeners:
            try:
                listener(sink_id, message)
            except Exception as e:
                logger.error(f"Unsolicited message listener error: {e}")

    def is_connected(self, sink_id: str) -> bool:
        """Check if we have an active connection to a sink."""
        if sink_id in self._connections:
            conn = self._connections[sink_id]
            return conn.connected and conn.client.is_connected
        return False

    def get_connection(self, sink_id: str) -> PooledConnection | None:
        """Get a pooled connection for a sink.

        Returns None if not connected. Caller should use the lock.
        """
        conn = self._connections.get(sink_id)
        if conn and conn.connected and conn.client.is_connected:
            return conn
        return None

    async def request(
        self, sink_id: str, message: Message, timeout: float = 5.0
    ) -> Message | None:
        """Send a request and wait for response on a pooled connection.

        Args:
            sink_id: The sink to send to
            message: The request message
            timeout: Request timeout in seconds

        Returns:
            Response message or None if failed
        """
        conn = self.get_connection(sink_id)
        if not conn:
            # Try to connect on-demand
            sink = self.controller.get_sink(sink_id)
            if sink and sink.online:
                await self._connect_to_sink(sink)
                conn = self.get_connection(sink_id)

        if not conn:
            logger.warning(f"Pool: No connection to sink {sink_id}")
            return None

        async with conn.lock:
            try:
                logger.debug(f"Pool: Sending {message.type.value} seq={message.seq} to {sink_id} "
                             f"(connected={conn.connected}, is_connected={conn.client.is_connected})")
                result = await conn.client.request(message, timeout=timeout)
                logger.debug(f"Pool: Got response for {sink_id}: {result.type.value if result else 'None'}")
                if result:
                    sink = self.controller.get_sink(sink_id)
                    if sink and sink.backend_connected is False:
                        sink.backend_connected = None
                        asyncio.create_task(self.controller._fetch_device_info(sink))
                return result
            except Exception as e:
                logger.warning(f"Pool: Request to {sink_id} failed: {type(e).__name__}: {e!r}")
                conn.connected = False
                return None

    async def send(self, sink_id: str, message: Message) -> bool:
        """Send a message without waiting for response.

        Args:
            sink_id: The sink to send to
            message: The message to send

        Returns:
            True if sent successfully
        """
        conn = self.get_connection(sink_id)
        if not conn:
            logger.warning(f"Pool: No connection to sink {sink_id}")
            return False

        async with conn.lock:
            try:
                await conn.client.send(message)
                return True
            except Exception as e:
                logger.warning(f"Pool: Send to {sink_id} failed: {e}")
                conn.connected = False
                return False

    def get_client(self, sink_id: str) -> ControlClient | None:
        """Get the raw ControlClient for a sink (for advanced use).

        WARNING: Use request() or send() instead when possible.
        The caller is responsible for thread-safety when using the raw client.
        """
        conn = self.get_connection(sink_id)
        return conn.client if conn else None

    @property
    def connected_sinks(self) -> list[str]:
        """Get list of sink IDs with active connections."""
        return [
            sink_id
            for sink_id, conn in self._connections.items()
            if conn.connected and conn.client.is_connected
        ]
