"""Core controller with discovery aggregation and device management."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from uuid import UUID, uuid4

from libltp import (
    ControlClient,
    ControllerAdvertiser,
    DiscoveredDevice,
    ServiceBrowser,
    capability_request,
    control_get,
    control_set,
)
from libltp.types import SERVICE_TYPE_SINK, SERVICE_TYPE_SOURCE

logger = logging.getLogger(__name__)


@dataclass
class DeviceState:
    """Extended state for a discovered device."""

    device: DiscoveredDevice
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    online: bool = True
    capabilities: dict[str, Any] | None = None
    controls: dict[str, Any] | None = None
    control_values: dict[str, Any] = field(default_factory=dict)
    # Stable ID that persists across device restarts
    _stable_id: str | None = field(default=None, repr=False)
    # Track consecutive failures before marking offline
    _consecutive_failures: int = field(default=0, repr=False)

    @property
    def id(self) -> str:
        """Get device ID as string (stable across restarts)."""
        # Use stable ID if set, otherwise use device's current ID
        if self._stable_id:
            return self._stable_id
        if self.device.device_id:
            return str(self.device.device_id)
        return self.device.name

    def set_stable_id(self) -> None:
        """Lock in the current device ID as the stable ID."""
        if self._stable_id is None:
            self._stable_id = self.id

    @property
    def name(self) -> str:
        """Get display name."""
        return self.device.display_name

    @property
    def description(self) -> str:
        """Get description."""
        return self.device.description

    @property
    def host(self) -> str:
        """Get host address."""
        if self.device.addresses:
            return self.device.addresses[0]
        return self.device.host.rstrip(".local.")

    @property
    def port(self) -> int:
        """Get control port."""
        return self.device.port

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "host": self.host,
            "port": self.port,
            "online": self.online,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "properties": self.device.properties,
            "capabilities": self.capabilities,
            "controls": self.controls,
            "control_values": self.control_values,
        }


DeviceCallback = Callable[[DeviceState, bool], None]  # device, is_added


class Controller:
    """Central controller for discovering and managing LTP devices."""

    def __init__(
        self,
        name: str = "ltp-controller",
        display_name: str = "LTP Controller",
        description: str = "Central routing controller",
        device_id: UUID | None = None,
        control_port: int = 0,
        health_check_interval: float = 30.0,
    ):
        self.name = name
        self.display_name = display_name
        self.description = description
        self.device_id = device_id or uuid4()
        self.control_port = control_port
        self.health_check_interval = health_check_interval

        self._browser: ServiceBrowser | None = None
        self._advertiser: ControllerAdvertiser | None = None
        self._sources: dict[str, DeviceState] = {}
        self._sinks: dict[str, DeviceState] = {}
        self._running = False
        self._health_check_task: asyncio.Task | None = None

        self._on_source_change: DeviceCallback | None = None
        self._on_sink_change: DeviceCallback | None = None

    @property
    def sources(self) -> list[DeviceState]:
        """Get all discovered sources."""
        return list(self._sources.values())

    @property
    def sinks(self) -> list[DeviceState]:
        """Get all discovered sinks."""
        return list(self._sinks.values())

    @property
    def online_sources(self) -> list[DeviceState]:
        """Get online sources."""
        return [s for s in self._sources.values() if s.online]

    @property
    def online_sinks(self) -> list[DeviceState]:
        """Get online sinks."""
        return [s for s in self._sinks.values() if s.online]

    def on_source_change(self, callback: DeviceCallback) -> None:
        """Set callback for source changes."""
        self._on_source_change = callback

    def on_sink_change(self, callback: DeviceCallback) -> None:
        """Set callback for sink changes."""
        self._on_sink_change = callback

    def _on_device_discovered(self, device: DiscoveredDevice, is_added: bool) -> None:
        """Handle device discovery events."""
        if device.is_source:
            self._handle_source(device, is_added)
        elif device.is_sink:
            self._handle_sink(device, is_added)

    def _handle_source(self, device: DiscoveredDevice, is_added: bool) -> None:
        """Handle source device changes."""
        device_key = device.name

        if is_added:
            if device_key in self._sources:
                # Update existing
                state = self._sources[device_key]
                state.device = device
                state.last_seen = datetime.now()
                state.online = True
                logger.info(f"Source updated: {state.name}")
            else:
                # New source
                state = DeviceState(device=device)
                state.set_stable_id()  # Lock in the ID for route references
                self._sources[device_key] = state
                logger.info(f"Source discovered: {state.name} (ID: {state.id})")
                # Fetch capabilities async
                asyncio.create_task(self._fetch_device_info(state))

            if self._on_source_change:
                self._on_source_change(state, True)
        else:
            if device_key in self._sources:
                state = self._sources[device_key]
                state.online = False
                logger.info(f"Source went offline: {state.name}")
                if self._on_source_change:
                    self._on_source_change(state, False)

    def _handle_sink(self, device: DiscoveredDevice, is_added: bool) -> None:
        """Handle sink device changes."""
        device_key = device.name

        if is_added:
            if device_key in self._sinks:
                # Update existing
                state = self._sinks[device_key]
                state.device = device
                state.last_seen = datetime.now()
                state.online = True
                logger.info(f"Sink updated: {state.name}")
            else:
                # New sink
                state = DeviceState(device=device)
                state.set_stable_id()  # Lock in the ID for route references
                self._sinks[device_key] = state
                logger.info(f"Sink discovered: {state.name} (ID: {state.id})")
                # Fetch capabilities async
                asyncio.create_task(self._fetch_device_info(state))

            if self._on_sink_change:
                self._on_sink_change(state, True)
        else:
            if device_key in self._sinks:
                state = self._sinks[device_key]
                state.online = False
                logger.info(f"Sink went offline: {state.name}")
                if self._on_sink_change:
                    self._on_sink_change(state, False)

    async def _fetch_device_info(self, state: DeviceState) -> None:
        """Fetch capabilities and controls from a device."""
        try:
            client = ControlClient(state.host, state.port)
            await client.connect()

            try:
                # Request capabilities
                cap_req = capability_request(0)
                cap_resp = await client.request(cap_req, timeout=5.0)
                if "device" in cap_resp.data:
                    state.capabilities = cap_resp.data["device"]
                    if "controls" in cap_resp.data["device"]:
                        state.controls = cap_resp.data["device"]["controls"]
                    logger.debug(f"Got capabilities from {state.name}")

                # Get control values
                if state.controls:
                    ctrl_req = control_get(0)
                    ctrl_resp = await client.request(ctrl_req, timeout=5.0)
                    if "values" in ctrl_resp.data:
                        state.control_values = ctrl_resp.data["values"]
                        logger.debug(f"Got control values from {state.name}")
            finally:
                await client.close()

        except Exception as e:
            logger.warning(f"Failed to fetch info from {state.name}: {e}")

    async def _health_check_loop(self) -> None:
        """Periodically check device health by pinging via control channel."""
        while self._running:
            await asyncio.sleep(self.health_check_interval)

            all_devices = list(self._sources.values()) + list(self._sinks.values())
            for state in all_devices:
                await self._ping_device(state)

    async def _ping_device(self, state: DeviceState) -> None:
        """Ping a device to check if it's still online.

        Uses a simple TCP connect test rather than a full protocol exchange
        to minimize load on the device and reduce false positives.
        """
        # Require 5 consecutive failures before marking offline
        FAILURES_BEFORE_OFFLINE = 5

        try:
            # Simple TCP connect test - just verify the port is reachable
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(state.host, state.port),
                timeout=10.0,
            )
            writer.close()
            await writer.wait_closed()

            # Device responded - reset failure count and mark as online
            state._consecutive_failures = 0

            if not state.online:
                state.online = True
                logger.info(f"Device came online: {state.name}")
                if state.device.is_source and self._on_source_change:
                    self._on_source_change(state, True)
                elif state.device.is_sink and self._on_sink_change:
                    self._on_sink_change(state, True)

            state.last_seen = datetime.now()

        except Exception as e:
            # Increment failure count
            state._consecutive_failures += 1
            logger.debug(
                f"Health check failed for {state.name} ({state._consecutive_failures}/{FAILURES_BEFORE_OFFLINE}): {e}"
            )

            # Only mark offline after multiple consecutive failures
            if state.online and state._consecutive_failures >= FAILURES_BEFORE_OFFLINE:
                state.online = False
                logger.info(f"Device went offline: {state.name} (after {state._consecutive_failures} failed health checks)")
                if state.device.is_source and self._on_source_change:
                    self._on_source_change(state, False)
                elif state.device.is_sink and self._on_sink_change:
                    self._on_sink_change(state, False)

    async def start(self) -> None:
        """Start the controller."""
        if self._running:
            return

        self._running = True

        # Start service browser
        self._browser = ServiceBrowser(
            service_types=[SERVICE_TYPE_SINK, SERVICE_TYPE_SOURCE],
            callback=self._on_device_discovered,
        )
        await self._browser.start()

        # Start advertising
        self._advertiser = ControllerAdvertiser(
            name=self.name,
            port=self.control_port,
            device_id=self.device_id,
            display_name=self.display_name,
            description=self.description,
        )
        await self._advertiser.start()

        # Start health check loop
        self._health_check_task = asyncio.create_task(self._health_check_loop())

        logger.info(f"Controller started: {self.display_name}")

    async def stop(self) -> None:
        """Stop the controller."""
        if not self._running:
            return

        self._running = False

        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None

        if self._advertiser:
            await self._advertiser.stop()
            self._advertiser = None

        if self._browser:
            await self._browser.stop()
            self._browser = None

        logger.info("Controller stopped")

    def get_source(self, identifier: str) -> DeviceState | None:
        """Get a source by ID or name."""
        # Try by key first
        if identifier in self._sources:
            return self._sources[identifier]

        # Try by UUID
        for state in self._sources.values():
            if state.id == identifier:
                return state

        # Try by display name
        for state in self._sources.values():
            if state.name == identifier:
                return state

        return None

    def get_sink(self, identifier: str) -> DeviceState | None:
        """Get a sink by ID or name."""
        # Try by key first
        if identifier in self._sinks:
            return self._sinks[identifier]

        # Try by UUID
        for state in self._sinks.values():
            if state.id == identifier:
                return state

        # Try by display name
        for state in self._sinks.values():
            if state.name == identifier:
                return state

        return None

    async def set_device_control(
        self, state: DeviceState, control_id: str, value: Any
    ) -> bool:
        """Set a control value on a device."""
        if not state.online:
            logger.warning(f"Cannot set control on offline device: {state.name}")
            return False

        try:
            client = ControlClient(state.host, state.port)
            await client.connect()

            try:
                req = control_set(0, {control_id: value})
                resp = await client.request(req, timeout=5.0)

                if resp.data.get("status") == "ok":
                    state.control_values[control_id] = value
                    logger.info(f"Set {control_id}={value} on {state.name}")
                    return True
                else:
                    logger.warning(
                        f"Failed to set control on {state.name}: {resp.data}"
                    )
                    return False
            finally:
                await client.close()

        except Exception as e:
            logger.error(f"Error setting control on {state.name}: {e}")
            return False

    async def refresh_device(self, state: DeviceState) -> None:
        """Refresh device info."""
        await self._fetch_device_info(state)
        state.last_seen = datetime.now()

    async def refresh_discovery(self) -> None:
        """Force a refresh of mDNS service discovery.

        Restarts the service browser to pick up any missed announcements.
        """
        if self._browser:
            logger.info("Refreshing mDNS discovery...")
            await self._browser.refresh()
            logger.info("mDNS discovery refreshed")
