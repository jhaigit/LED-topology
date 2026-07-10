"""Serial sink fleet mode: discover, probe, and run one sink per LTP device.

Formalizes the probe-then-spawn shell-script workflow into the sink itself:

- Enumerate candidate serial ports (path globs + optional USB VID:PID
  allowlist, so probe bytes are never written at unrelated devices).
- Probe each candidate once with the normal v2 handshake; a responding
  device's connection is *adopted* by the sink it spawns, so AVR boards are
  only reset once (opening a port pulses DTR).
- Name/description default to what the device itself reports, with optional
  per-device config overrides matched on USB serial number, device-reported
  name, or port glob.
- Device UUIDs are derived deterministically from stable identity, so the
  controller sees the same sink across restarts and re-plugs.
- Periodic rescan gives hotplug: new devices are adopted, and a sink whose
  port path has vanished (device re-enumerated elsewhere) is retired so the
  rescan can re-adopt it at its new path.
"""

from __future__ import annotations

import asyncio
import fnmatch
import glob
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from ltp_serial_sink.sink import SerialSink, SerialSinkConfig
from ltp_serial_sink.v2_renderer import V2Renderer, V2RendererConfig

logger = logging.getLogger(__name__)

# Namespace for deterministic device UUIDs (uuid5 of stable identity).
_FLEET_UUID_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "ltp-serial-sink.fleet")

# A sink whose port path is missing for this many consecutive scans is
# retired (its device re-enumerated at a different path or was unplugged).
_MISSING_SCANS_BEFORE_RETIRE = 2


class FleetScanConfig(BaseModel):
    """What to probe and how."""

    include: list[str] = Field(default_factory=lambda: ["/dev/ttyUSB*", "/dev/ttyACM*"])
    exclude: list[str] = Field(default_factory=list)
    # "vid:pid" hex pairs, e.g. "1a86:7523" (CH340) or "2341:0043" (Uno).
    # Empty = probe everything matched by include.
    usb_ids: list[str] = Field(default_factory=list)
    baudrate: int = 115200
    # Old-bootloader Nanos take ~2s before the sketch runs; the HELLO wait
    # needs headroom on top of that.
    probe_timeout: float = 4.0
    # Seconds between rescans (hotplug). 0 = scan once at startup only.
    rescan_interval: float = 10.0


class DeviceMatch(BaseModel):
    """Selects a device for an override. Fields are ANDed; at least one must
    be set. usb_serial is the most stable key — but note CH340 clones often
    report none, in which case match on the device-reported name instead."""

    usb_serial: str | None = None
    device_name: str | None = None  # exact match on device-reported name
    # Firmware/software-load identity (e.g. "ltp-328p-dual") — the same for
    # every board running a given build, distinct from the per-instance
    # device_name. Use to target every device of a firmware type at once.
    firmware_name: str | None = None
    port: str | None = None  # fnmatch glob on the port path
    # Device-reported geometry, for telling apart two boards with identical
    # firmware name and indistinguishable USB adapters (no serial, same
    # VID:PID): total pixel count and/or "WxH" / "N" dimensions string.
    pixels: int | None = None
    dimensions: str | None = None  # e.g. "16x10" (matrix) or "160" (strip)

    def matches(self, candidate: "PortCandidate", reported: "ReportedInfo") -> bool:
        if (
            self.usb_serial is None
            and self.device_name is None
            and self.firmware_name is None
            and self.port is None
            and self.pixels is None
            and self.dimensions is None
        ):
            return False
        if self.usb_serial is not None and self.usb_serial != (candidate.usb_serial or ""):
            return False
        if self.device_name is not None and self.device_name != reported.name:
            return False
        if self.firmware_name is not None and self.firmware_name != reported.firmware_name:
            return False
        if self.pixels is not None and self.pixels != reported.pixels:
            return False
        if self.dimensions is not None and self.dimensions != reported.dimensions:
            return False
        if self.port is not None and not (
            fnmatch.fnmatch(candidate.path, self.port)
            or fnmatch.fnmatch(candidate.real_path, self.port)
        ):
            return False
        return True


@dataclass
class ReportedInfo:
    """What the device said about itself in the probe handshake."""

    name: str = ""
    pixels: int | None = None
    dimensions: str | None = None  # "WxH" for matrices, "N" for strips
    firmware_name: str = ""  # software-load id from INFO_BUILD, e.g. "ltp-328p-dual"

    @classmethod
    def from_device_info(cls, info: Any, build: Any = None) -> "ReportedInfo":
        if info is None:
            return cls(firmware_name=getattr(build, "firmware_name", "") or "")
        pixels = getattr(info, "total_pixels", None)
        dimensions = None
        if getattr(info, "is_matrix", False):
            w, h = info.dimensions
            dimensions = f"{w}x{h}"
        elif pixels is not None:
            dimensions = str(pixels)
        return cls(
            name=getattr(info, "device_name", "") or "",
            pixels=pixels,
            dimensions=dimensions,
            firmware_name=getattr(build, "firmware_name", "") or "",
        )


class DeviceOverride(BaseModel):
    """Per-device configuration overrides."""

    match: DeviceMatch
    enabled: bool = True
    name: str | None = None
    description: str | None = None
    baudrate: int | None = None
    pixels: int = 0  # 0 = auto-detect
    # Layer 2 PSK (32 hex chars) enabling device-auth for this sink. The
    # serial bridge enforces it network-side, so the AVR needs no changes
    # (proposal §Layer 3 — serial devices get anti-hijack for free).
    auth_psk: str | None = None
    auth_read_open: bool = True


class FleetConfig(BaseModel):
    scan: FleetScanConfig = Field(default_factory=FleetScanConfig)
    devices: list[DeviceOverride] = Field(default_factory=list)


@dataclass
class PortCandidate:
    """A serial port that passed the include/exclude/usb_ids filters."""

    path: str  # as matched (may be a by-id symlink)
    real_path: str  # resolved device node, e.g. /dev/ttyUSB0
    usb_serial: str | None = None
    vid: int | None = None
    pid: int | None = None
    usb_description: str | None = None

    @property
    def stable_key(self) -> str:
        """Identity for UUID derivation: USB serial number when the adapter
        has one, else the matched path (stable for by-id/by-path patterns)."""
        if self.usb_serial:
            return f"usb-serial:{self.usb_serial}"
        return f"port:{self.path}"


def _comports() -> list[Any]:
    try:
        import serial.tools.list_ports  # type: ignore[import-untyped]
    except ImportError:
        return []
    return list(serial.tools.list_ports.comports())


def enumerate_candidates(scan: FleetScanConfig) -> list[PortCandidate]:
    """Expand include globs, drop excludes, attach USB metadata, filter by
    VID:PID. Paths that resolve to the same device node are deduplicated
    (first matching pattern wins, so list by-id patterns before /dev/tty*)."""
    meta = {p.device: p for p in _comports()}

    seen: set[str] = set()
    candidates: list[PortCandidate] = []
    for pattern in scan.include:
        for path in sorted(glob.glob(pattern)):
            real = os.path.realpath(path)
            if real in seen:
                continue
            if any(fnmatch.fnmatch(path, ex) or fnmatch.fnmatch(real, ex) for ex in scan.exclude):
                continue
            info = meta.get(real)
            candidate = PortCandidate(
                path=path,
                real_path=real,
                usb_serial=getattr(info, "serial_number", None),
                vid=getattr(info, "vid", None),
                pid=getattr(info, "pid", None),
                usb_description=getattr(info, "description", None),
            )
            if scan.usb_ids:
                if candidate.vid is None or candidate.pid is None:
                    continue
                vidpid = f"{candidate.vid:04x}:{candidate.pid:04x}"
                if vidpid not in [v.lower() for v in scan.usb_ids]:
                    continue
            seen.add(real)
            candidates.append(candidate)
    return candidates


def probe_port(candidate: PortCandidate, scan: FleetScanConfig) -> V2Renderer:
    """Open the port and run the v2 handshake. Returns the OPEN renderer on
    success (the caller adopts it into a sink); raises on any failure with
    the port released. Blocking — run in an executor."""
    renderer = V2Renderer(
        V2RendererConfig(
            port=candidate.path,
            baudrate=scan.baudrate,
            timeout=scan.probe_timeout,
        )
    )
    try:
        renderer.open()
        if not renderer.is_connected() or renderer.device_info is None:
            raise ConnectionError("no LTP handshake")
        return renderer
    except Exception:
        renderer.close()
        raise


def stable_device_id(candidate: PortCandidate, reported_name: str) -> uuid.UUID:
    """Deterministic sink UUID so the controller sees the same device across
    restarts. Keyed on USB serial when present, else reported name + path."""
    if candidate.usb_serial:
        key = candidate.stable_key
    else:
        key = f"{candidate.stable_key}:{reported_name}"
    return uuid.uuid5(_FLEET_UUID_NS, key)


@dataclass
class FleetMember:
    candidate: PortCandidate
    sink: SerialSink
    task: asyncio.Task
    name: str
    missing_scans: int = 0


class SerialFleet:
    """Runs one SerialSink per discovered LTP device in a single process."""

    def __init__(self, config: FleetConfig | None = None):
        self.config = config or FleetConfig()
        self.members: dict[str, FleetMember] = {}  # keyed by real_path
        self._failed_ports: set[str] = set()  # probed, not LTP — log once
        self._running = False

    # -- configuration matching ------------------------------------------

    def _override_for(
        self, candidate: PortCandidate, reported: ReportedInfo
    ) -> DeviceOverride | None:
        for override in self.config.devices:
            if override.match.matches(candidate, reported):
                return override
        return None

    def _unique_name(self, base: str, port_path: str) -> str:
        """mDNS advertisement names derive from the sink name; make sure two
        identically-named devices don't collide."""
        names = {m.name for m in self.members.values()}
        if base not in names:
            return base
        return f"{base} ({os.path.basename(port_path)})"

    # -- scan/adopt loop ---------------------------------------------------

    async def _probe_candidates(
        self, candidates: list[PortCandidate]
    ) -> list[tuple[PortCandidate, V2Renderer | None]]:
        loop = asyncio.get_running_loop()

        async def probe_one(c: PortCandidate) -> tuple[PortCandidate, V2Renderer | None]:
            try:
                renderer = await loop.run_in_executor(None, probe_port, c, self.config.scan)
                return c, renderer
            except Exception as e:
                if c.real_path not in self._failed_ports:
                    self._failed_ports.add(c.real_path)
                    logger.info(f"Port {c.path}: not an LTP device ({e})")
                else:
                    logger.debug(f"Port {c.path}: probe failed again ({e})")
                return c, None

        return list(await asyncio.gather(*(probe_one(c) for c in candidates)))

    def _adopt(self, candidate: PortCandidate, renderer: V2Renderer) -> None:
        reported = ReportedInfo.from_device_info(renderer.device_info, renderer.build_info)
        reported_name = reported.name
        override = self._override_for(candidate, reported)

        if override is not None and not override.enabled:
            logger.info(f"Port {candidate.path}: device disabled by config, skipping")
            renderer.close()
            return

        base_name = (
            (override.name if override else None)
            or reported_name
            or f"Serial LED Strip ({os.path.basename(candidate.real_path)})"
        )
        name = self._unique_name(base_name, candidate.real_path)
        description = (override.description if override else None) or (
            f"{candidate.usb_description or 'serial device'} on {candidate.real_path}"
        )

        sink_config = SerialSinkConfig(
            device_id=stable_device_id(candidate, reported_name),
            name=name,
            description=description,
            port=candidate.path,
            baudrate=(override.baudrate if override else None) or self.config.scan.baudrate,
            timeout=self.config.scan.probe_timeout,
            pixels=override.pixels if override else 0,
            auth_psk=(override.auth_psk if override else None) or "",
            auth_read_open=override.auth_read_open if override else True,
        )
        sink = SerialSink(sink_config, renderer=renderer)
        task = asyncio.create_task(sink.run(), name=f"sink:{candidate.real_path}")
        self.members[candidate.real_path] = FleetMember(
            candidate=candidate, sink=sink, task=task, name=name
        )
        logger.info(
            f"Adopted {candidate.path}: '{name}' "
            f"({sink_config.device_id}, usb_serial={candidate.usb_serial or 'n/a'})"
        )

    async def _retire(self, real_path: str, reason: str) -> None:
        member = self.members.pop(real_path, None)
        if member is None:
            return
        logger.info(f"Retiring sink '{member.name}' ({real_path}): {reason}")
        member.task.cancel()
        try:
            await member.task
        except (asyncio.CancelledError, Exception):
            pass

    async def scan_once(self) -> None:
        """One discovery pass: adopt new devices, retire vanished ones."""
        candidates = enumerate_candidates(self.config.scan)
        present = {c.real_path for c in candidates}

        # Retire members whose port path has been gone for several scans
        # (a replug that re-enumerated elsewhere shows up as a new candidate;
        # the sink's own reconnect logic can't follow a renamed device node).
        for real_path in list(self.members):
            member = self.members[real_path]
            if real_path in present or os.path.exists(real_path):
                member.missing_scans = 0
            else:
                member.missing_scans += 1
                if member.missing_scans >= _MISSING_SCANS_BEFORE_RETIRE:
                    await self._retire(real_path, "port disappeared")

        # Reap sinks that died on their own so the port can be re-probed.
        for real_path in list(self.members):
            if self.members[real_path].task.done():
                await self._retire(real_path, "sink task exited")

        new = [c for c in candidates if c.real_path not in self.members]
        if not new:
            return
        for candidate, renderer in await self._probe_candidates(new):
            if renderer is not None:
                self._failed_ports.discard(candidate.real_path)
                self._adopt(candidate, renderer)

    async def run(self) -> None:
        """Scan until cancelled (or once, if rescan_interval is 0)."""
        self._running = True
        interval = self.config.scan.rescan_interval
        try:
            await self.scan_once()
            if not self.members and interval <= 0:
                logger.warning("No LTP devices found and rescanning is disabled")
            while self._running:
                if interval > 0:
                    await asyncio.sleep(interval)
                    await self.scan_once()
                else:
                    await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        for real_path in list(self.members):
            await self._retire(real_path, "fleet shutdown")

    def get_stats(self) -> dict[str, Any]:
        return {
            "members": {
                path: {
                    "name": m.name,
                    "usb_serial": m.candidate.usb_serial,
                    "running": m.sink.is_running,
                    "serial_connected": m.sink.serial_connected,
                }
                for path, m in self.members.items()
            },
        }


def load_fleet_config(data: dict[str, Any]) -> FleetConfig:
    """Build a FleetConfig from a parsed YAML document.

    Accepts either the nested form ({fleet: {scan: ..., devices: ...}}) or
    scan/devices at the top level."""
    if "fleet" in data and isinstance(data["fleet"], dict):
        data = data["fleet"]
    return FleetConfig(
        scan=FleetScanConfig(**data.get("scan", {})),
        devices=[DeviceOverride(**d) for d in data.get("devices", [])],
    )
