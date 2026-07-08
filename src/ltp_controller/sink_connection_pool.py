"""Connection pool for shared sink connections."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from libltp import ClaimSession, ControlClient, DeviceAuthError, Message, MessageType
from libltp.addr import format_address_port
from libltp.deviceauth import PRIVILEGED_TYPES

from ltp_controller.controller import Controller, DeviceState
from ltp_controller.keystore import KeyStore

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
    # Layer 2 claim session (None if the device is Level 0 or unkeyed).
    session: ClaimSession | None = None
    renew_task: asyncio.Task | None = None


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

    def __init__(self, controller: Controller, keystore: KeyStore | None = None):
        self.controller = controller
        self.keystore = keystore
        self.controller_id = str(controller.device_id)
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
        """Establish a connection to a sink.

        The TCP connect itself is performed *outside* the pool lock: it is a
        network operation that can block for seconds (even with the connect
        timeout), and holding the global lock across it would serialize every
        other sink's requests and stall pool.stop(). The lock is only taken to
        read/mutate the connections dict, with a re-check after connecting so a
        concurrent connect doesn't leave a duplicate.
        """
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

        # Connect without holding the pool lock.
        client = ControlClient(
            sink.host,
            sink.port,
            handler=lambda msg: self._handle_message(sink_id, msg),
        )
        try:
            await client.connect()
        except Exception as e:
            logger.warning(f"Pool: Failed to connect to sink {sink.name}: {e}")
            return

        async with self._lock:
            # Another connect may have won the race while we were handshaking.
            existing = self._connections.get(sink_id)
            if existing and existing.connected and existing.client.is_connected:
                await client.close()
                return

            conn = PooledConnection(
                sink_id=sink_id,
                client=client,
                host=sink.host,
                port=sink.port,
                connected=True,
            )
            self._connections[sink_id] = conn
            logger.info(
                f"Pool: Connected to sink {sink.name} ({format_address_port(sink.host, sink.port)})"
            )

        # Claim the device (Layer 2) outside the pool lock — the handshake is
        # a network round-trip. A failure here leaves the connection up but
        # unclaimed; privileged requests will then fail loudly.
        await self._maybe_claim(sink, conn)

    def _device_auth_mode(self, sink: DeviceState) -> str:
        """auth mode advertised in the mDNS TXT record (none|siphash)."""
        props = sink.device.properties or {}
        return str(props.get("auth", "none"))

    async def _maybe_claim(self, sink: DeviceState, conn: PooledConnection) -> None:
        """Run the claim handshake if the device requires auth and we hold
        its key. Sets conn.session and starts the renew loop on success."""
        if self.keystore is None or self._device_auth_mode(sink) == "none":
            return
        psk = self.keystore.get_key(sink.id)
        if psk is None:
            logger.warning(
                f"Pool: sink {sink.name} requires auth but no key is stored — "
                "privileged commands will be rejected. Pair the device to add its key."
            )
            sink.auth_state = "unkeyed"
            return

        session = ClaimSession(conn.client, psk, self.controller_id)
        try:
            await session.claim()
        except DeviceAuthError as e:
            if e.code.name == "LEASE_HELD":
                logger.warning(
                    f"Pool: sink {sink.name} is claimed by another controller "
                    f"(retry in {e.retry_after}s)"
                )
                sink.auth_state = "held"
            else:
                logger.error(f"Pool: claim failed for {sink.name}: {e}")
                sink.auth_state = "error"
            return
        except Exception as e:
            logger.error(f"Pool: claim error for {sink.name}: {e!r}")
            return

        conn.session = session
        sink.auth_state = "owned"
        conn.renew_task = asyncio.create_task(self._renew_loop(sink_id=sink.id))
        logger.info(f"Pool: claimed sink {sink.name} (lease {session.lease_seconds:.0f}s)")

    async def _renew_loop(self, sink_id: str) -> None:
        """Renew the lease at half the lease interval so a transient failure
        has a second chance before expiry."""
        while self._running:
            conn = self._connections.get(sink_id)
            if conn is None or conn.session is None or not conn.client.is_connected:
                return
            await asyncio.sleep(max(2.0, conn.session.lease_seconds / 2))
            conn = self._connections.get(sink_id)
            if conn is None or conn.session is None:
                return
            async with conn.lock:
                try:
                    await conn.session.renew()
                except Exception as e:
                    logger.warning(f"Pool: lease renew failed for {sink_id}: {e}")
                    return

    async def _disconnect_from_sink(self, sink_id: str) -> None:
        """Disconnect from a sink."""
        conn = self._connections.pop(sink_id, None)
        if conn:
            conn.connected = False
            if conn.renew_task:
                conn.renew_task.cancel()
                conn.renew_task = None
            # Best-effort graceful release so the device frees its lease
            # immediately rather than waiting for expiry.
            if conn.session and conn.client.is_connected:
                try:
                    await conn.session.release()
                except Exception:
                    pass
            conn.session = None
            try:
                await conn.client.close()
            except Exception:
                pass
            sink = self.controller.get_sink(sink_id)
            if sink is not None:
                sink.auth_state = "none"
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

    async def request(self, sink_id: str, message: Message, timeout: float = 5.0) -> Message | None:
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
                if conn.session is not None and message.type in PRIVILEGED_TYPES:
                    conn.session.sign(message)
                logger.debug(
                    f"Pool: Sending {message.type.value} seq={message.seq} to {sink_id} "
                    f"(connected={conn.connected}, is_connected={conn.client.is_connected})"
                )
                result = await conn.client.request(message, timeout=timeout)
                logger.debug(
                    f"Pool: Got response for {sink_id}: {result.type.value if result else 'None'}"
                )
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
                if conn.session is not None and message.type in PRIVILEGED_TYPES:
                    conn.session.sign(message)
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
