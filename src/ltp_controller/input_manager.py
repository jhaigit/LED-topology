"""Input event manager for receiving input events from sinks."""

import asyncio
import logging
from typing import Any, Callable

from libltp import ControlClient, Message, MessageType, capability_request

from ltp_controller.controller import Controller, DeviceState
from ltp_controller.rules import InputState

logger = logging.getLogger(__name__)

# Type for input event callbacks
# (sink_id, input_id, name, input_type, old_value, new_value)
InputEventCallback = Callable[[str, int, str, str, Any, Any], None]


class InputEventManager:
    """Manages persistent connections to sinks to receive input events.

    Maintains TCP connections to all discovered sinks and listens for
    INPUT_EVENT messages. Tracks the current state of all inputs and
    notifies listeners when input values change.
    """

    def __init__(self, controller: Controller):
        self.controller = controller
        self._connections: dict[str, ControlClient] = {}  # sink_id -> client
        self._input_states: dict[str, dict[int, InputState]] = {}  # sink_id -> {input_id -> state}
        self._listeners: list[InputEventCallback] = []
        self._running = False
        self._reconnect_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def add_listener(self, callback: InputEventCallback) -> None:
        """Add a listener for input events.

        Args:
            callback: Function called with (sink_id, input_id, name, type, old_val, new_val)
        """
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: InputEventCallback) -> None:
        """Remove a listener."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def get_inputs_for_sink(self, sink_id: str) -> list[InputState]:
        """Get all known inputs for a sink."""
        if sink_id in self._input_states:
            return list(self._input_states[sink_id].values())
        return []

    def get_all_inputs(self) -> dict[str, list[InputState]]:
        """Get all inputs grouped by sink ID."""
        return {
            sink_id: list(inputs.values())
            for sink_id, inputs in self._input_states.items()
        }

    def get_input_state(self, sink_id: str, input_id: int) -> InputState | None:
        """Get the current state of a specific input."""
        if sink_id in self._input_states:
            return self._input_states[sink_id].get(input_id)
        return None

    async def start(self) -> None:
        """Start the input event manager."""
        if self._running:
            return

        self._running = True
        logger.info("Input event manager starting...")

        # Register for sink discovery callbacks
        self.controller.on_sink_change(self._on_sink_change)

        # Connect to all existing online sinks
        for sink in self.controller.online_sinks:
            asyncio.create_task(self._connect_to_sink(sink))

        # Start reconnection loop
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

        logger.info("Input event manager started")

    async def stop(self) -> None:
        """Stop the input event manager."""
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

        logger.info("Input event manager stopped")

    def _on_sink_change(self, sink: DeviceState, is_online: bool) -> None:
        """Handle sink discovery/removal events."""
        if is_online:
            # New sink or sink came online - connect to it
            asyncio.create_task(self._connect_to_sink(sink))
        else:
            # Sink went offline - disconnect
            asyncio.create_task(self._disconnect_from_sink(sink.id))

    async def _connect_to_sink(self, sink: DeviceState) -> None:
        """Establish a persistent connection to a sink for input events."""
        sink_id = sink.id

        async with self._lock:
            # Already connected?
            if sink_id in self._connections:
                client = self._connections[sink_id]
                if client.is_connected:
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
                self._connections[sink_id] = client
                logger.info(f"Connected to sink {sink.name} for input events")

                # Query initial input states from capability response
                await self._query_initial_inputs(sink_id, client)

            except Exception as e:
                logger.warning(f"Failed to connect to sink {sink.name} for input events: {e}")

    async def _query_initial_inputs(self, sink_id: str, client: ControlClient) -> None:
        """Query sink capabilities to get initial input states."""
        try:
            cap_req = capability_request(0)
            cap_resp = await client.request(cap_req, timeout=5.0)

            if "device" not in cap_resp.data:
                return

            device = cap_resp.data["device"]
            inputs = device.get("inputs", [])

            if not inputs:
                return

            # Populate initial input states
            if sink_id not in self._input_states:
                self._input_states[sink_id] = {}

            for inp in inputs:
                input_id = inp.get("id")
                if input_id is None:
                    continue

                self._input_states[sink_id][input_id] = InputState(
                    input_id=input_id,
                    name=inp.get("name", f"Input {input_id}"),
                    input_type=inp.get("type", "unknown"),
                    value=inp.get("value"),
                    timestamp=None,
                )

            logger.info(f"Loaded {len(inputs)} initial inputs from sink {sink_id}")

        except Exception as e:
            logger.debug(f"Failed to query initial inputs from {sink_id}: {e}")

    async def _disconnect_from_sink(self, sink_id: str) -> None:
        """Disconnect from a sink."""
        client = self._connections.pop(sink_id, None)
        if client:
            try:
                await client.close()
            except Exception:
                pass
            logger.info(f"Disconnected from sink {sink_id}")

    async def _reconnect_loop(self) -> None:
        """Periodically check and reconnect to disconnected sinks."""
        while self._running:
            await asyncio.sleep(10.0)  # Check every 10 seconds

            for sink in self.controller.online_sinks:
                sink_id = sink.id
                if sink_id not in self._connections:
                    await self._connect_to_sink(sink)
                elif not self._connections[sink_id].is_connected:
                    await self._connect_to_sink(sink)

    def _handle_message(self, sink_id: str, message: Message) -> None:
        """Handle incoming messages from a sink."""
        if message.type == MessageType.INPUT_EVENT:
            self._handle_input_event(sink_id, message)

    def _handle_input_event(self, sink_id: str, msg: Message) -> None:
        """Handle an input event message from a sink."""
        input_id = msg.data.get("input_id")
        input_name = msg.data.get("input_name", f"Input {input_id}")
        input_type = msg.data.get("input_type", "unknown")
        value = msg.data.get("value")
        timestamp = msg.data.get("timestamp")

        if input_id is None:
            logger.warning(f"INPUT_EVENT from {sink_id} missing input_id")
            return

        # Get old value
        old_value = None
        if sink_id in self._input_states and input_id in self._input_states[sink_id]:
            old_value = self._input_states[sink_id][input_id].value

        # Update state
        if sink_id not in self._input_states:
            self._input_states[sink_id] = {}

        self._input_states[sink_id][input_id] = InputState(
            input_id=input_id,
            name=input_name,
            input_type=input_type,
            value=value,
            timestamp=timestamp,
        )

        logger.debug(
            f"Input event: sink={sink_id} input={input_id} ({input_name}) "
            f"type={input_type} value={old_value} -> {value}"
        )

        # Notify listeners
        for listener in self._listeners:
            try:
                listener(sink_id, input_id, input_name, input_type, old_value, value)
            except Exception as e:
                logger.error(f"Input event listener error: {e}")

    def is_connected_to_sink(self, sink_id: str) -> bool:
        """Check if we have an active connection to a sink."""
        if sink_id in self._connections:
            return self._connections[sink_id].is_connected
        return False
