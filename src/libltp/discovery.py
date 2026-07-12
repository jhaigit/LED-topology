"""mDNS service discovery for LTP devices."""

import asyncio
import atexit
import ctypes
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable
from uuid import UUID

from zeroconf import IPVersion, ServiceInfo, ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

# Track all active avahi processes for cleanup on exit
_active_avahi_processes: list[subprocess.Popen] = []


def _avahi_available() -> bool:
    """Check if avahi-publish-service is available."""
    return shutil.which("avahi-publish-service") is not None


def _cleanup_avahi_processes() -> None:
    """Cleanup handler for atexit - kills any remaining avahi processes."""
    for proc in _active_avahi_processes[:]:
        if proc.poll() is None:  # Still running
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
        _active_avahi_processes.remove(proc)


# Register cleanup handler at module load time
atexit.register(_cleanup_avahi_processes)


def _set_pdeathsig() -> None:
    """Set PR_SET_PDEATHSIG so child dies when parent dies (Linux only)."""
    if sys.platform == "linux":
        try:
            PR_SET_PDEATHSIG = 1
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
        except (OSError, AttributeError):
            pass  # Not critical, just a backup mechanism


from libltp.types import (
    ColorFormat,
    DataType,
    DeviceType,
    PROTOCOL_VERSION,
    ScalarFormat,
    SERVICE_TYPE_CONTROLLER,
    SERVICE_TYPE_FLEET,
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
    ipv4_addresses: list[str] = field(default_factory=list)
    ipv6_addresses: list[str] = field(default_factory=list)

    @property
    def is_sink(self) -> bool:
        return SERVICE_TYPE_SINK in self.service_type

    @property
    def is_source(self) -> bool:
        return SERVICE_TYPE_SOURCE in self.service_type

    @property
    def is_controller(self) -> bool:
        return SERVICE_TYPE_CONTROLLER in self.service_type

    @property
    def is_fleet(self) -> bool:
        return SERVICE_TYPE_FLEET in self.service_type


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
        self._avahi_process: subprocess.Popen | None = None
        self._use_avahi = _avahi_available()

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
        if self._use_avahi:
            await self._start_avahi()
        else:
            await self._start_zeroconf()

    async def _start_avahi(self) -> None:
        """Start advertising using avahi-publish-service."""
        if self._avahi_process is not None:
            return

        # Build TXT record arguments
        properties = _build_txt_properties(
            self.device_id,
            self.display_name,
            self.description,
            self.has_controls,
            **self.extra_properties,
        )

        # avahi-publish-service args: name type port [txt ...]
        # Strip .local. suffix - avahi doesn't want it
        service_type = self.service_type
        if service_type.endswith(".local."):
            service_type = service_type[:-7]  # Remove ".local."

        cmd = [
            "avahi-publish-service",
            self.name,
            service_type,
            str(self.port),
        ]

        # Add TXT records (decode bytes values to strings)
        for key, value in properties.items():
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            cmd.append(f"{key}={value}")

        logger.info(
            f"Advertising {self.service_type} service '{self.name}' on port {self.port} (via avahi)"
        )

        # Start avahi-publish-service in background
        # - start_new_session: Creates new process group so it can be killed as a group
        # - preexec_fn: Sets PR_SET_PDEATHSIG so child dies if parent dies unexpectedly
        self._avahi_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
            preexec_fn=_set_pdeathsig,
        )

        # Track for cleanup on exit
        _active_avahi_processes.append(self._avahi_process)

    async def _start_zeroconf(self) -> None:
        """Start advertising using python-zeroconf."""
        if self._zeroconf is not None:
            return

        self._zeroconf = AsyncZeroconf(ip_version=IPVersion.All)
        self._service_info = self._build_service_info()

        logger.info(
            f"Advertising {self.service_type} service '{self.name}' on port {self.port} (via zeroconf)"
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

        # Stop avahi process if running
        if self._avahi_process is not None:
            # Remove from global tracking list first
            if self._avahi_process in _active_avahi_processes:
                _active_avahi_processes.remove(self._avahi_process)

            self._avahi_process.terminate()
            try:
                self._avahi_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._avahi_process.kill()
                self._avahi_process.wait(timeout=1)  # Wait after kill
            self._avahi_process = None
            logger.info(f"Stopped advertising service '{self.name}' (avahi)")
            return

        if self._zeroconf is None:
            return

        if self._service_info:
            await self._zeroconf.async_unregister_service(self._service_info)

        await self._zeroconf.async_close()
        self._zeroconf = None
        self._service_info = None
        logger.info(f"Stopped advertising service '{self.name}' (zeroconf)")

    async def _reannounce_loop(self) -> None:
        """Periodically re-announce the service to improve discoverability.

        Only used for zeroconf; avahi handles re-announcement automatically.
        """
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
        self.extra_properties.update(kwargs)

        if self._avahi_process is not None:
            # Restart avahi with new properties
            await self.stop()
            await self._start_avahi()
        elif self._zeroconf is not None and self._service_info is not None:
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
        data_type: DataType = DataType.VISUAL,
        scalar_format: ScalarFormat | None = None,
        channels: int = 0,
        auth: str = "none",
    ):
        dims = dimensions or [pixels]
        dim_str = "x".join(str(d) for d in dims)

        properties = {
            "type": device_type.value,
            "pixels": str(pixels),
            "dim": dim_str,
            "color": color_format.name.lower(),
            "rate": str(max_rate),
            "data": data_type.value,
            # Device-auth mode (none|siphash) so controllers know before
            # connecting whether a claim handshake is required.
            "auth": auth,
        }

        # Add scalar-specific properties
        if data_type == DataType.SCALAR:
            if scalar_format:
                properties["scalar"] = scalar_format.name.lower()
            if channels > 0:
                properties["channels"] = str(channels)

        super().__init__(
            service_type=SERVICE_TYPE_SINK,
            name=name,
            port=port,
            device_id=device_id,
            display_name=display_name,
            description=description,
            has_controls=has_controls,
            properties=properties,
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
        data_type: DataType = DataType.VISUAL,
        scalar_format: ScalarFormat | None = None,
        channels: int = 0,
    ):
        dims = dimensions or [60]
        output_str = "x".join(str(d) for d in dims)

        properties = {
            "output": output_str,
            "color": color_format.name.lower(),
            "rate": str(rate),
            "mode": mode.value,
            "data": data_type.value,
        }

        # Add scalar-specific properties
        if data_type == DataType.SCALAR:
            if scalar_format:
                properties["scalar"] = scalar_format.name.lower()
            if channels > 0:
                properties["channels"] = str(channels)

        super().__init__(
            service_type=SERVICE_TYPE_SOURCE,
            name=name,
            port=port,
            device_id=device_id,
            display_name=display_name,
            description=description,
            has_controls=has_controls,
            properties=properties,
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
        self._pending_removals: dict[str, asyncio.Task] = {}
        self._removal_grace_period = 30.0  # seconds before acting on removal

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
        asyncio.create_task(self._handle_service_change(zeroconf, service_type, name, state_change))

    async def _handle_service_change(
        self,
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        """Handle service state change asynchronously."""
        try:
            async with self._lock:
                if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
                    # Cancel any pending removal for this service
                    pending = self._pending_removals.pop(name, None)
                    if pending and not pending.done():
                        pending.cancel()
                        logger.debug(f"Cancelled pending removal for {name} (service reappeared)")
                    await self._add_service(zeroconf, service_type, name)
                elif state_change == ServiceStateChange.Removed:
                    self._schedule_removal(name)
        except Exception as e:
            logger.error(f"Error handling service change for {name}: {e}", exc_info=True)

    async def _add_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        """Add or update a discovered service."""
        logger.debug(f"Resolving service: {name} ({service_type})")
        info = AsyncServiceInfo(service_type, name)
        # Use 5 second timeout for cross-network discovery
        resolved = await info.async_request(zeroconf, 5000)

        if not resolved:
            logger.warning(f"Service {name} resolution timed out")
            return

        if not info.port:
            logger.warning(f"Service {name} has no port (server={info.server}), ignoring")
            return

        properties = _parse_txt_properties(info)

        # Parse device ID
        device_id = None
        if "id" in properties:
            try:
                device_id = UUID(properties["id"])
            except ValueError:
                pass

        # Get addresses - prefer IPv4 for better compatibility
        ipv4_addresses = []
        ipv6_addresses = []

        # Extract IPv4 addresses
        for addr in info.addresses_by_version(IPVersion.V4Only):
            try:
                ipv4_addresses.append(socket.inet_ntoa(addr))
            except (OSError, ValueError):
                pass

        # Extract IPv6 addresses
        for addr in info.addresses_by_version(IPVersion.V6Only):
            try:
                ipv6_addresses.append(socket.inet_ntop(socket.AF_INET6, addr))
            except (OSError, ValueError):
                pass

        # If no IPv4 from mDNS, try to resolve the hostname to get IPv4
        if not ipv4_addresses and info.server:
            try:
                # Use getaddrinfo which respects NSS (including mDNS via nss-mdns)
                hostname = info.server.rstrip(".")
                results = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
                if results:
                    resolved_ip = results[0][4][0]
                    ipv4_addresses = [resolved_ip]
                    logger.debug(f"Resolved {hostname} to {resolved_ip}")
            except socket.gaierror as e:
                logger.warning(f"Could not resolve {info.server} to IPv4: {e}")

        # If no IPv6 from mDNS, try to resolve the hostname to get IPv6
        if not ipv6_addresses and info.server:
            try:
                hostname = info.server.rstrip(".")
                results = socket.getaddrinfo(hostname, None, socket.AF_INET6, socket.SOCK_STREAM)
                if results:
                    resolved_ip = results[0][4][0]
                    ipv6_addresses = [resolved_ip]
                    logger.debug(f"Resolved {hostname} to IPv6 {resolved_ip}")
            except socket.gaierror:
                pass  # IPv6 resolution failure is not a warning

        # Prefer IPv4, fall back to IPv6
        addresses = ipv4_addresses if ipv4_addresses else ipv6_addresses
        logger.debug(
            f"Addresses for {name}: IPv4={ipv4_addresses}, IPv6={ipv6_addresses}, using={addresses}"
        )

        if not addresses:
            logger.warning(f"No addresses for {name} (server={info.server})")

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
            ipv4_addresses=ipv4_addresses,
            ipv6_addresses=ipv6_addresses,
        )

        is_new = name not in self._devices
        self._devices[name] = device

        logger.info(
            f"{'Discovered' if is_new else 'Updated'} {service_type} service: "
            f"{device.display_name} at {device.host}:{device.port}"
        )

        if self.callback:
            self.callback(device, True)

    def _schedule_removal(self, name: str) -> None:
        """Schedule a delayed service removal with grace period.

        If the service reappears before the grace period expires, the
        removal is cancelled. This prevents route teardown on transient
        mDNS blips.
        """
        if name in self._pending_removals:
            return  # already scheduled
        if name not in self._devices:
            return  # nothing to remove

        device = self._devices[name]
        logger.info(
            f"Service disappeared: {device.display_name} "
            f"(grace period {self._removal_grace_period}s)"
        )
        self._pending_removals[name] = asyncio.create_task(self._delayed_removal(name))

    async def _delayed_removal(self, name: str) -> None:
        """Wait for the grace period, then remove the service."""
        try:
            await asyncio.sleep(self._removal_grace_period)
        except asyncio.CancelledError:
            return  # service reappeared, removal cancelled

        async with self._lock:
            self._pending_removals.pop(name, None)
            device = self._devices.pop(name, None)
            if device:
                logger.info(f"Service removed (after grace period): {device.display_name}")
                if self.callback:
                    self.callback(device, False)

    async def start(self) -> None:
        """Start browsing for services."""
        if self._zeroconf is not None:
            return

        self._zeroconf = AsyncZeroconf(ip_version=IPVersion.All)

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
        # Cancel any pending removals
        for task in self._pending_removals.values():
            task.cancel()
        self._pending_removals.clear()

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
