"""Tests for serial sink fleet mode (discovery, matching, adopt/retire)."""

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from ltp_serial_sink import fleet as fleet_mod
from ltp_serial_sink.fleet import (
    DeviceMatch,
    DeviceOverride,
    FleetConfig,
    FleetScanConfig,
    PortCandidate,
    ReportedInfo,
    SerialFleet,
    enumerate_candidates,
    load_fleet_config,
    stable_device_id,
)

# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


class TestConfigParsing:
    def test_defaults(self):
        cfg = load_fleet_config({})
        assert "/dev/ttyUSB*" in cfg.scan.include
        assert cfg.scan.probe_timeout == 4.0
        assert cfg.devices == []

    def test_nested_fleet_key(self):
        cfg = load_fleet_config({"fleet": {"scan": {"usb_ids": ["1a86:7523"]}, "devices": []}})
        assert cfg.scan.usb_ids == ["1a86:7523"]

    def test_top_level_keys(self):
        cfg = load_fleet_config(
            {
                "scan": {"rescan_interval": 0},
                "devices": [{"match": {"usb_serial": "X1"}, "name": "A", "enabled": False}],
            }
        )
        assert cfg.scan.rescan_interval == 0
        assert cfg.devices[0].name == "A"
        assert not cfg.devices[0].enabled


# ---------------------------------------------------------------------------
# Candidate enumeration
# ---------------------------------------------------------------------------


def _fake_comport(device, serial_number=None, vid=None, pid=None, description=""):
    return SimpleNamespace(
        device=device, serial_number=serial_number, vid=vid, pid=pid, description=description
    )


class TestEnumeration:
    @pytest.fixture()
    def devdir(self, tmp_path, monkeypatch):
        """A fake /dev with two USB adapters and a by-id symlink for one."""
        (tmp_path / "ttyUSB0").touch()
        (tmp_path / "ttyUSB1").touch()
        (tmp_path / "ttyS0").touch()
        byid = tmp_path / "by-id"
        byid.mkdir()
        os.symlink(tmp_path / "ttyUSB0", byid / "usb-1a86_USB_Serial-if00-port0")
        monkeypatch.setattr(
            fleet_mod,
            "_comports",
            lambda: [
                _fake_comport(
                    str(tmp_path / "ttyUSB0"),
                    serial_number="A5069RR4",
                    vid=0x1A86,
                    pid=0x7523,
                    description="USB Serial",
                ),
                _fake_comport(
                    str(tmp_path / "ttyUSB1"), vid=0x10C4, pid=0xEA60, description="CP2102"
                ),
            ],
        )
        return tmp_path

    def test_glob_and_metadata(self, devdir):
        scan = FleetScanConfig(include=[str(devdir / "ttyUSB*")])
        cands = enumerate_candidates(scan)
        assert [os.path.basename(c.real_path) for c in cands] == ["ttyUSB0", "ttyUSB1"]
        assert cands[0].usb_serial == "A5069RR4"
        assert cands[1].usb_serial is None

    def test_by_id_symlink_dedupes_and_wins(self, devdir):
        scan = FleetScanConfig(include=[str(devdir / "by-id" / "*"), str(devdir / "ttyUSB*")])
        cands = enumerate_candidates(scan)
        # ttyUSB0 appears once, via its by-id path (listed first)
        assert len(cands) == 2
        assert "by-id" in cands[0].path
        assert os.path.basename(cands[0].real_path) == "ttyUSB0"

    def test_exclude(self, devdir):
        scan = FleetScanConfig(include=[str(devdir / "ttyUSB*")], exclude=["*ttyUSB1"])
        cands = enumerate_candidates(scan)
        assert len(cands) == 1

    def test_usb_id_filter(self, devdir):
        scan = FleetScanConfig(include=[str(devdir / "tty*")], usb_ids=["1a86:7523"])
        cands = enumerate_candidates(scan)
        # ttyS0 has no metadata, ttyUSB1 is a CP2102 — only the CH340 passes
        assert len(cands) == 1
        assert cands[0].usb_serial == "A5069RR4"


# ---------------------------------------------------------------------------
# Override matching + identity
# ---------------------------------------------------------------------------


def _cand(path="/dev/ttyUSB0", usb_serial=None):
    return PortCandidate(path=path, real_path=path, usb_serial=usb_serial)


def _rep(name="", pixels=None, dimensions=None, firmware_name=""):
    return ReportedInfo(
        name=name, pixels=pixels, dimensions=dimensions, firmware_name=firmware_name
    )


class TestMatching:
    def test_usb_serial_match(self):
        m = DeviceMatch(usb_serial="X1")
        assert m.matches(_cand(usb_serial="X1"), _rep("any"))
        assert not m.matches(_cand(usb_serial="X2"), _rep("any"))
        assert not m.matches(_cand(), _rep("any"))

    def test_device_name_match(self):
        m = DeviceMatch(device_name="ltp-328p-dual")
        assert m.matches(_cand(), _rep("ltp-328p-dual"))
        assert not m.matches(_cand(), _rep("other"))

    def test_firmware_name_match(self):
        # firmware_name targets a software LOAD, regardless of instance name.
        m = DeviceMatch(firmware_name="ltp-328p-dual")
        assert m.matches(_cand(), _rep("Hall Strip", firmware_name="ltp-328p-dual"))
        assert m.matches(_cand(), _rep("Kitchen", firmware_name="ltp-328p-dual"))
        assert not m.matches(_cand(), _rep("Hall Strip", firmware_name="ltp-apa102"))
        # A rule that keys only on firmware_name must not match a device that
        # reported no build info.
        assert not m.matches(_cand(), _rep("Hall Strip"))

    def test_firmware_name_anded_with_instance(self):
        # firmware_name (type) AND device_name (instance) together.
        m = DeviceMatch(firmware_name="ltp-328p-dual", device_name="Hall Strip")
        assert m.matches(_cand(), _rep("Hall Strip", firmware_name="ltp-328p-dual"))
        assert not m.matches(_cand(), _rep("Kitchen", firmware_name="ltp-328p-dual"))

    def test_port_glob_match(self):
        m = DeviceMatch(port="/dev/ttyUSB*")
        assert m.matches(_cand("/dev/ttyUSB2"), _rep())
        assert not m.matches(_cand("/dev/ttyACM0"), _rep())

    def test_empty_match_never_matches(self):
        assert not DeviceMatch().matches(_cand(usb_serial="X1"), _rep("name"))

    def test_fields_are_anded(self):
        m = DeviceMatch(usb_serial="X1", device_name="strip")
        assert m.matches(_cand(usb_serial="X1"), _rep("strip"))
        assert not m.matches(_cand(usb_serial="X1"), _rep("other"))

    def test_pixels_match(self):
        # Two boards, same firmware name, no USB serial: pixel count tells
        # them apart.
        m160 = DeviceMatch(device_name="LTP-328P-Dual", pixels=160)
        m80 = DeviceMatch(device_name="LTP-328P-Dual", pixels=80)
        strip = _rep("LTP-328P-Dual", pixels=160, dimensions="160")
        matrix = _rep("LTP-328P-Dual", pixels=80, dimensions="16x5")
        assert m160.matches(_cand(), strip)
        assert not m160.matches(_cand(), matrix)
        assert m80.matches(_cand(), matrix)

    def test_dimensions_match(self):
        m = DeviceMatch(dimensions="16x5")
        assert m.matches(_cand(), _rep(pixels=80, dimensions="16x5"))
        assert not m.matches(_cand(), _rep(pixels=80, dimensions="80"))

    def test_reported_info_from_device_info(self):
        matrix = SimpleNamespace(
            device_name="X", total_pixels=80, is_matrix=True, dimensions=(16, 5)
        )
        strip = SimpleNamespace(
            device_name="Y", total_pixels=160, is_matrix=False, dimensions=(160, 1)
        )
        assert ReportedInfo.from_device_info(matrix).dimensions == "16x5"
        assert ReportedInfo.from_device_info(strip).dimensions == "160"
        assert ReportedInfo.from_device_info(None).name == ""

    def test_reported_info_captures_firmware_name(self):
        info = SimpleNamespace(
            device_name="Hall Strip", total_pixels=160, is_matrix=False, dimensions=(160, 1)
        )
        build = SimpleNamespace(firmware_name="ltp-328p-dual")
        rep = ReportedInfo.from_device_info(info, build)
        assert rep.firmware_name == "ltp-328p-dual"
        assert rep.name == "Hall Strip"
        # No build info -> empty firmware_name, not an error.
        assert ReportedInfo.from_device_info(info).firmware_name == ""
        # firmware_name available even when device_info is None.
        assert ReportedInfo.from_device_info(None, build).firmware_name == "ltp-328p-dual"


class TestStableIdentity:
    def test_uuid_stable_across_port_moves(self):
        a = stable_device_id(
            PortCandidate(path="/dev/ttyUSB0", real_path="/dev/ttyUSB0", usb_serial="S1"),
            "strip",
        )
        b = stable_device_id(
            PortCandidate(path="/dev/ttyUSB7", real_path="/dev/ttyUSB7", usb_serial="S1"),
            "strip",
        )
        assert a == b

    def test_uuid_differs_per_serial(self):
        a = stable_device_id(_cand(usb_serial="S1"), "strip")
        b = stable_device_id(_cand(usb_serial="S2"), "strip")
        assert a != b

    def test_no_usb_serial_falls_back_to_path_and_name(self):
        a = stable_device_id(_cand("/dev/by-id/x"), "strip")
        b = stable_device_id(_cand("/dev/by-id/x"), "strip")
        c = stable_device_id(_cand("/dev/by-id/y"), "strip")
        assert a == b != c
        assert isinstance(a, uuid.UUID)


# ---------------------------------------------------------------------------
# Adopt / retire loop (fakes; no hardware, no network)
# ---------------------------------------------------------------------------


class FakeRenderer:
    def __init__(self, device_name="ltp-328p-dual", firmware_name="ltp-328p-dual"):
        self.device_info = SimpleNamespace(device_name=device_name)
        self.build_info = SimpleNamespace(firmware_name=firmware_name)
        self.closed = False

    def close(self):
        self.closed = True


@dataclass
class FakeSink:
    config: object
    renderer: object
    stopped: bool = field(default=False)

    async def run(self):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.stopped = True
            raise

    @property
    def is_running(self):
        return not self.stopped

    @property
    def serial_connected(self):
        return True


@pytest.fixture()
def fake_sink_cls(monkeypatch):
    created = []

    def factory(config, renderer=None):
        sink = FakeSink(config=config, renderer=renderer)
        created.append(sink)
        return sink

    monkeypatch.setattr(fleet_mod, "SerialSink", factory)
    return created


class TestFleetLoop:
    async def test_adopt_uses_device_name_and_adopted_renderer(
        self, fake_sink_cls, monkeypatch, tmp_path
    ):
        port = tmp_path / "ttyUSB0"
        port.touch()
        renderer = FakeRenderer("Hall Device")
        monkeypatch.setattr(fleet_mod, "probe_port", lambda c, s: renderer)

        fleet = SerialFleet(
            FleetConfig(scan=FleetScanConfig(include=[str(port)], rescan_interval=0))
        )
        await fleet.scan_once()
        await asyncio.sleep(0)  # let the sink task enter run()

        assert len(fleet.members) == 1
        member = next(iter(fleet.members.values()))
        assert member.name == "Hall Device"
        assert member.sink.renderer is renderer  # adopted, not reopened
        assert member.sink.config.device_id == stable_device_id(member.candidate, "Hall Device")
        await fleet.stop()
        assert member.sink.stopped

    async def test_override_disable_closes_probe(self, fake_sink_cls, monkeypatch, tmp_path):
        port = tmp_path / "ttyUSB0"
        port.touch()
        renderer = FakeRenderer("Skip Me")
        monkeypatch.setattr(fleet_mod, "probe_port", lambda c, s: renderer)

        fleet = SerialFleet(
            FleetConfig(
                scan=FleetScanConfig(include=[str(port)]),
                devices=[DeviceOverride(match=DeviceMatch(device_name="Skip Me"), enabled=False)],
            )
        )
        await fleet.scan_once()
        assert fleet.members == {}
        assert renderer.closed

    async def test_override_name_and_duplicate_names(self, fake_sink_cls, monkeypatch, tmp_path):
        for name in ("ttyUSB0", "ttyUSB1"):
            (tmp_path / name).touch()
        monkeypatch.setattr(fleet_mod, "probe_port", lambda c, s: FakeRenderer("Same Name"))
        fleet = SerialFleet(FleetConfig(scan=FleetScanConfig(include=[str(tmp_path / "ttyUSB*")])))
        await fleet.scan_once()
        names = sorted(m.name for m in fleet.members.values())
        assert names[0] == "Same Name"
        assert names[1] == "Same Name (ttyUSB1)"
        await fleet.stop()

    async def test_non_ltp_port_not_adopted_and_logged_once(
        self, fake_sink_cls, monkeypatch, tmp_path
    ):
        port = tmp_path / "ttyUSB0"
        port.touch()

        def failing_probe(c, s):
            raise ConnectionError("no LTP handshake")

        monkeypatch.setattr(fleet_mod, "probe_port", failing_probe)
        fleet = SerialFleet(FleetConfig(scan=FleetScanConfig(include=[str(port)])))
        await fleet.scan_once()
        await fleet.scan_once()
        assert fleet.members == {}
        assert str(port) in fleet._failed_ports

    async def test_vanished_port_retired_after_grace(self, fake_sink_cls, monkeypatch, tmp_path):
        port = tmp_path / "ttyUSB0"
        port.touch()
        monkeypatch.setattr(fleet_mod, "probe_port", lambda c, s: FakeRenderer())
        fleet = SerialFleet(FleetConfig(scan=FleetScanConfig(include=[str(port)])))
        await fleet.scan_once()
        assert len(fleet.members) == 1

        port.unlink()  # unplug
        await fleet.scan_once()
        assert len(fleet.members) == 1  # grace period
        await fleet.scan_once()
        assert fleet.members == {}  # retired

    async def test_dead_sink_reaped_and_readopted(self, fake_sink_cls, monkeypatch, tmp_path):
        port = tmp_path / "ttyUSB0"
        port.touch()
        monkeypatch.setattr(fleet_mod, "probe_port", lambda c, s: FakeRenderer())
        fleet = SerialFleet(FleetConfig(scan=FleetScanConfig(include=[str(port)])))
        await fleet.scan_once()
        member = next(iter(fleet.members.values()))
        member.task.cancel()
        await asyncio.sleep(0)  # let cancellation land

        await fleet.scan_once()
        assert len(fleet.members) == 1
        assert next(iter(fleet.members.values())) is not member
        await fleet.stop()
