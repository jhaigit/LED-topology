"""Input event manager for receiving input events from sinks."""

import asyncio
import logging
from typing import Any, Callable

from libltp import Message, MessageType, capability_request

from ltp_controller.controller import Controller, DeviceState
from ltp_controller.rules import InputState

# Avoid circular import
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ltp_controller.sink_connection_pool import SinkConnectionPool

logger = logging.getLogger(__name__)

# Type for input event callbacks
# (sink_id, input_id, name, input_type, old_value, new_value)
InputEventCallback = Callable[[str, int, str, str, Any, Any], None]


class InputEventManager:
    """Manages input events received from sinks.

    Listens for INPUT_EVENT messages via the shared connection pool and
    tracks the current state of all inputs. Notifies listeners when input
    values change.
    """

    def __init__(
        self,
        controller: Controller,
        connection_pool: "SinkConnectionPool | None" = None,
    ):
        self.controller = controller
        self._pool = connection_pool
        self._input_states: dict[str, dict[int, InputState]] = {}  # sink_id -> {input_id -> state}
        self._listeners: list[InputEventCallback] = []
        self._running = False
        self._retry_task: asyncio.Task | None = None

    def set_connection_pool(self, pool: "SinkConnectionPool") -> None:
        """Set the connection pool (for late binding)."""
        self._pool = pool

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

        # Register as listener for unsolicited messages (INPUT_EVENT)
        if self._pool:
            self._pool.add_unsolicited_listener(self._handle_message)

        # Load initial inputs from all existing online sinks
        for sink in self.controller.online_sinks:
            asyncio.create_task(self._load_inputs_for_sink(sink))

        # Start periodic retry loop for sinks missing inputs
        self._retry_task = asyncio.create_task(self._retry_load_inputs_loop())

        logger.info("Input event manager started")

    async def stop(self) -> None:
        """Stop the input event manager."""
        if not self._running:
            return

        self._running = False

        # Cancel retry task
        if self._retry_task:
            self._retry_task.cancel()
            try:
                await self._retry_task
            except asyncio.CancelledError:
                pass
            self._retry_task = None

        # Unregister from pool
        if self._pool:
            self._pool.remove_unsolicited_listener(self._handle_message)

        logger.info("Input event manager stopped")

    def _on_sink_change(self, sink: DeviceState, is_online: bool) -> None:
        """Handle sink discovery/removal events."""
        if is_online:
            # New sink or sink came online - load its inputs
            asyncio.create_task(self._load_inputs_for_sink(sink))

    async def _retry_load_inputs_loop(self) -> None:
        """Periodically retry loading inputs for sinks that don't have them yet."""
        while self._running:
            await asyncio.sleep(5.0)  # Check every 5 seconds

            for sink in self.controller.online_sinks:
                sink_id = sink.id
                # Check if this sink should have inputs but we don't have them
                has_inputs_in_caps = (
                    sink.capabilities and
                    sink.capabilities.get("inputs") and
                    len(sink.capabilities.get("inputs", [])) > 0
                )
                have_loaded_inputs = (
                    sink_id in self._input_states and
                    len(self._input_states[sink_id]) > 0
                )

                if has_inputs_in_caps and not have_loaded_inputs:
                    logger.debug(f"Retrying input load for sink {sink.name}")
                    await self._load_inputs_for_sink(sink)

    async def _load_inputs_for_sink(self, sink: DeviceState) -> None:
        """Load input states for a sink from cached capabilities or via query."""
        sink_id = sink.id
        inputs = []

        # Wait briefly for capabilities to be populated (they're fetched async on discovery)
        for _ in range(5):
            if sink.capabilities:
                break
            await asyncio.sleep(0.5)

        # First try to get inputs from the controller's cached capabilities
        if sink.capabilities:
            inputs = sink.capabilities.get("inputs", [])
            if inputs:
                logger.debug(f"Using {len(inputs)} cached inputs from controller for {sink_id}")

        # If no cached inputs and pool is available, query the sink
        if not inputs and self._pool:
            # Wait briefly for pool connection
            for _ in range(5):
                if self._pool.is_connected(sink_id):
                    break
                await asyncio.sleep(0.5)

            if self._pool.is_connected(sink_id):
                try:
                    cap_req = capability_request(0)
                    cap_resp = await self._pool.request(sink_id, cap_req, timeout=5.0)

                    if cap_resp and "device" in cap_resp.data:
                        device = cap_resp.data["device"]
                        inputs = device.get("inputs", [])
                        if inputs:
                            logger.debug(f"Queried {len(inputs)} inputs from sink {sink_id}")
                        else:
                            logger.debug(f"Sink {sink_id} has no inputs in capability response")

                except Exception as e:
                    logger.warning(f"Failed to query inputs from {sink_id}: {e}")

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

        logger.info(f"Loaded {len(inputs)} initial inputs for sink {sink_id}")

    def _handle_message(self, sink_id: str, message: Message) -> None:
        """Handle incoming messages from a sink (via pool)."""
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
        """Check if we have an active connection to a sink (via pool)."""
        if self._pool:
            return self._pool.is_connected(sink_id)
        return False
