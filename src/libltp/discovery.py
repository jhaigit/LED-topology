"""mDNS service discovery for LTP devices."""

import asyncio
import logging
import socket
from dataclasses import dataclass, field
from typing import Callable
from uuid import UUID

from zeroconf import IPVersion, ServiceInfo, ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from libltp.types import (
    ColorFormat,
    DeviceType,
    PROTOCOL_VERSION,
    SERVICE_TYPE_CONTROLLER,
    SERVICE_TYPE_SINK,
    SERVICE_TYPE_SOURCE,
    SourceMode,
)

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredDevice:
    """Information about a discovered device."""

    name: str
    service_type: str
    host: str
    port: int
    device_id: UUID | None
    display_name: str
    description: str
    properties: dict[str, str] = field(default_factory=dict)
    addresses: list[str] = field(default_factory=list)

    @property
    def is_sink(self) -> bool:
        return SERVICE_TYPE_SINK in self.service_type

    @property
    def is_source(self) -> bool:
        return SERVICE_TYPE_SOURCE in self.service_type

    @property
    def is_controller(self) -> bool:
        return SERVICE_TYPE_CONTROLLER in self.service_type


def _parse_txt_properties(info: ServiceInfo) -> dict[str, str]:
    """Parse TXT record properties from ServiceInfo."""
    props = {}
    if info.properties:
        for key, value in info.properties.items():
            if isinstance(key, bytes):
                key = key.decode("utf-8", errors="replace")
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            props[key] = value
    return props


def _build_txt_properties(
    device_id: UUID,
    name: str,
    description: str,
    has_controls: bool,
    **kwargs: str,
) -> dict[str, bytes]:
    """Build TXT record properties dictionary."""
    props = {
        "ver": PROTOCOL_VERSION.encode(),
        "name": name.encode(),
        "desc": description.encode(),
        "id": str(device_id).encode(),
        "ctrl": b"1" if has_controls else b"0",
    }
    for key, value in kwargs.items():
        props[key] = str(value).encode()
    return props


class ServiceAdvertiser:
    """Advertises an LTP service via mDNS."""

    def __init__(
        self,
        service_type: str,
        name: str,
        port: int,
        device_id: UUID,
        display_name: str,
        description: str = "",
        has_controls: bool = False,
        properties: dict[str, str] | None = None,
        reannounce_interval: float = 30.0,
    ):
        self.service_type = service_type
        self.name = name
        self.port = port
        self.device_id = device_id
        self.display_name = display_name
        self.description = description
        self.has_controls = has_controls
        self.extra_properties = properties or {}
        self.reannounce_interval = reannounce_interval

        self._zeroconf: AsyncZeroconf | None = None
        self._service_info: ServiceInfo | None = None
        self._reannounce_task: asyncio.Task | None = None

    def _build_service_info(self) -> ServiceInfo:
        """Build the ServiceInfo object."""
        # Get local hostname
        hostname = socket.gethostname()
        if not hostname.endswith(".local."):
            hostname = f"{hostname}.local."

        properties = _build_txt_properties(
            self.device_id,
            self.display_name,
            self.description,
            self.has_controls,
            **self.extra_properties,
        )

        return ServiceInfo(
            self.service_type,
            f"{self.name}.{self.service_type}",
            port=self.port,
            properties=properties,
            server=hostname,
        )

    async def start(self) -> None:
        """Start advertising the service."""
        if self._zeroconf is not None:
            return

        self._zeroconf = AsyncZeroconf(ip_version=IPVersion.V4Only)
        self._service_info = self._build_service_info()

        logger.info(
            f"Advertising {self.service_type} service '{self.name}' on port {self.port}"
        )
        await self._zeroconf.async_register_service(self._service_info)

        # Start periodic re-announcement
        if self.reannounce_interval > 0:
            self._reannounce_task = asyncio.create_task(self._reannounce_loop())

    async def stop(self) -> None:
        """Stop advertising the service."""
        # Cancel reannounce task
        if self._reannounce_task:
            self._reannounce_task.cancel()
            try:
                await self._reannounce_task
            except asyncio.CancelledError:
                pass
            self._reannounce_task = None

        if self._zeroconf is None:
            return

        if self._service_info:
            await self._zeroconf.async_unregister_service(self._service_info)

        await self._zeroconf.async_close()
        self._zeroconf = None
        self._service_info = None
        logger.info(f"Stopped advertising service '{self.name}'")

    async def _reannounce_loop(self) -> None:
        """Periodically re-announce the service to improve discoverability."""
        while True:
            await asyncio.sleep(self.reannounce_interval)
            if self._zeroconf and self._service_info:
                try:
                    # Unregister and re-register to force announcement
                    await self._zeroconf.async_unregister_service(self._service_info)
                    await self._zeroconf.async_register_service(self._service_info)
                    logger.debug(f"Re-announced service '{self.name}'")
                except Exception as e:
                    logger.warning(f"Failed to re-announce service: {e}")

    async def update_properties(self, **kwargs: str) -> None:
        """Update service properties."""
        if self._zeroconf is None or self._service_info is None:
            return

        self.extra_properties.update(kwargs)

        # Unregister and re-register with new properties
        await self._zeroconf.async_unregister_service(self._service_info)
        self._service_info = self._build_service_info()
        await self._zeroconf.async_register_service(self._service_info)


class SinkAdvertiser(ServiceAdvertiser):
    """Advertises an LTP sink device."""

    def __init__(
        self,
        name: str,
        port: int,
        device_id: UUID,
        display_name: str,
        description: str = "",
        device_type: DeviceType = DeviceType.STRING,
        pixels: int = 60,
        dimensions: list[int] | None = None,
        color_format: ColorFormat = ColorFormat.RGB,
        max_rate: int = 60,
        has_controls: bool = False,
    ):
        dims = dimensions or [pixels]
        dim_str = "x".join(str(d) for d in dims)

        super().__init__(
            service_type=SERVICE_TYPE_SINK,
            name=name,
            port=port,
            device_id=device_id,
            display_name=display_name,
            description=description,
            has_controls=has_controls,
            properties={
                "type": device_type.value,
                "pixels": str(pixels),
                "dim": dim_str,
                "color": color_format.name.lower(),
                "rate": str(max_rate),
            },
        )


class SourceAdvertiser(ServiceAdvertiser):
    """Advertises an LTP data source."""

    def __init__(
        self,
        name: str,
        port: int,
        device_id: UUID,
        display_name: str,
        description: str = "",
        dimensions: list[int] | None = None,
        color_format: ColorFormat = ColorFormat.RGB,
        rate: int = 30,
        mode: SourceMode = SourceMode.STREAM,
        has_controls: bool = False,
    ):
        dims = dimensions or [60]
        output_str = "x".join(str(d) for d in dims)

        super().__init__(
            service_type=SERVICE_TYPE_SOURCE,
            name=name,
            port=port,
            device_id=device_id,
            display_name=display_name,
            description=description,
            has_controls=has_controls,
            properties={
                "output": output_str,
                "color": color_format.name.lower(),
                "rate": str(rate),
                "mode": mode.value,
            },
        )


class ControllerAdvertiser(ServiceAdvertiser):
    """Advertises an LTP controller."""

    def __init__(
        self,
        name: str,
        port: int,
        device_id: UUID,
        display_name: str,
        description: str = "",
    ):
        super().__init__(
            service_type=SERVICE_TYPE_CONTROLLER,
            name=name,
            port=port,
            device_id=device_id,
            display_name=display_name,
            description=description,
        )


# Callback type for discovery events
DiscoveryCallback = Callable[[DiscoveredDevice, bool], None]  # device, is_added


class ServiceBrowser:
    """Browses for LTP services on the network."""

    def __init__(
        self,
        service_types: list[str] | None = None,
        callback: DiscoveryCallback | None = None,
    ):
        self.service_types = service_types or [
            SERVICE_TYPE_SINK,
            SERVICE_TYPE_SOURCE,
            SERVICE_TYPE_CONTROLLER,
        ]
        self.callback = callback

        self._zeroconf: AsyncZeroconf | None = None
        self._browsers: list[AsyncServiceBrowser] = []
        self._devices: dict[str, DiscoveredDevice] = {}
        self._lock = asyncio.Lock()

    @property
    def devices(self) -> dict[str, DiscoveredDevice]:
        """Get all discovered devices."""
        return dict(self._devices)

    @property
    def sinks(self) -> list[DiscoveredDevice]:
        """Get discovered sink devices."""
        return [d for d in self._devices.values() if d.is_sink]

    @property
    def sources(self) -> list[DiscoveredDevice]:
        """Get discovered source devices."""
        return [d for d in self._devices.values() if d.is_source]

    @property
    def controllers(self) -> list[DiscoveredDevice]:
        """Get discovered controllers."""
        return [d for d in self._devices.values() if d.is_controller]

    def _on_service_state_change(
        self,
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        """Handle service state change (sync callback, schedules async work)."""
        asyncio.create_task(
            self._handle_service_change(zeroconf, service_type, name, state_change)
        )

    async def _handle_service_change(
        self,
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        """Handle service state change asynchronously."""
        async with self._lock:
            if state_change == ServiceStateChange.Added:
                await self._add_service(zeroconf, service_type, name)
            elif state_change == ServiceStateChange.Removed:
                self._remove_service(name)
            elif state_change == ServiceStateChange.Updated:
                await self._add_service(zeroconf, service_type, name)

    async def _add_service(
        self, zeroconf: Zeroconf, service_type: str, name: str
    ) -> None:
        """Add or update a discovered service."""
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(zeroconf, 3000)

        if not info.port:
            logger.warning(f"Service {name} has no port, ignoring")
            return

        properties = _parse_txt_properties(info)

        # Parse device ID
        device_id = None
        if "id" in properties:
            try:
                device_id = UUID(properties["id"])
            except ValueError:
                pass

        # Get addresses
        addresses = [socket.inet_ntoa(addr) for addr in info.addresses]

        device = DiscoveredDevice(
            name=name,
            service_type=service_type,
            host=info.server or (addresses[0] if addresses else ""),
            port=info.port,
            device_id=device_id,
            display_name=properties.get("name", name),
            description=properties.get("desc", ""),
            properties=properties,
            addresses=addresses,
        )

        is_new = name not in self._devices
        self._devices[name] = device

        logger.info(
            f"{'Discovered' if is_new else 'Updated'} {service_type} service: "
            f"{device.display_name} at {device.host}:{device.port}"
        )

        if self.callback:
            self.callback(device, True)

    def _remove_service(self, name: str) -> None:
        """Remove a discovered service."""
        device = self._devices.pop(name, None)
        if device:
            logger.info(f"Service removed: {device.display_name}")
            if self.callback:
                self.callback(device, False)

    async def start(self) -> None:
        """Start browsing for services."""
        if self._zeroconf is not None:
            return

        self._zeroconf = AsyncZeroconf(ip_version=IPVersion.V4Only)

        for service_type in self.service_types:
            browser = AsyncServiceBrowser(
                self._zeroconf.zeroconf,
                service_type,
                handlers=[self._on_service_state_change],
            )
            self._browsers.append(browser)
            logger.info(f"Browsing for {service_type} services")

    async def stop(self) -> None:
        """Stop browsing for services."""
        for browser in self._browsers:
            await browser.async_cancel()
        self._browsers.clear()

        if self._zeroconf:
            await self._zeroconf.async_close()
            self._zeroconf = None

        self._devices.clear()

    async def refresh(self) -> None:
        """Refresh service discovery."""
        # Force re-browse by restarting browsers
        await self.stop()
        await self.start()

    def get_device_by_id(self, device_id: UUID) -> DiscoveredDevice | None:
        """Find a device by its ID."""
        for device in self._devices.values():
            if device.device_id == device_id:
                return device
        return None

    def get_device_by_name(self, name: str) -> DiscoveredDevice | None:
        """Find a device by its display name."""
        for device in self._devices.values():
            if device.display_name == name:
                return device
        return None
