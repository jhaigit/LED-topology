"""Device rename via the serial sink's `device_name` pseudo-control.

The firmware's EEPROM-backed instance name (CMD_SET_NAME) is exposed to the
network protocol as a string control so the controller GUI can rename a
device. Setting it must write the device, sync the control to the name the
device reports back (it may truncate / restore a default), and notify the
owner (fleet mode) so the sink re-advertises under the new name.
"""

import asyncio
from types import SimpleNamespace

from libltp.protocol import control_set
from ltp_serial_sink import fleet as fleet_mod
from ltp_serial_sink.fleet import FleetConfig, SerialFleet
from ltp_serial_sink.sink import DEVICE_NAME_CONTROL_ID, SerialSink, SerialSinkConfig


class FakeRenderer:
    """Just enough of V2Renderer for control setup / set paths."""

    def __init__(self, device_name="LTP-Strip", accept=True, reported=None):
        self.device_info = SimpleNamespace(device_name=device_name)
        self.build_info = None
        self.controls = {}
        self.inputs = {}
        self.accept = accept
        self.reported = reported  # name the device reports after set (None = echo)
        self.set_name_calls = []

    def is_connected(self):
        return True

    def get_control(self, ctrl_id):
        return None

    def set_input_event_callback(self, cb):
        pass

    def set_name(self, name):
        self.set_name_calls.append(name)
        if not self.accept:
            return None
        new = self.reported if self.reported is not None else name
        self.device_info = SimpleNamespace(device_name=new)
        return new


def _sink(renderer):
    cfg = SerialSinkConfig(name="Sink", port="/dev/fake", pixels=8)
    sink = SerialSink(cfg, renderer=renderer)
    sink._setup_device_controls()
    return sink


class TestDeviceNameControl:
    def test_registered_from_device_info(self):
        sink = _sink(FakeRenderer("Hall Strip"))
        ctrl = sink._controls.get(DEVICE_NAME_CONTROL_ID)
        assert ctrl is not None
        assert ctrl.type == "string"
        assert ctrl.value == "Hall Strip"
        assert ctrl.max_length == 15
        # It's advertised in the capability list the controller renders.
        ids = {c["id"] for c in sink._controls.to_list()}
        assert DEVICE_NAME_CONTROL_ID in ids

    def test_not_registered_without_device_name(self):
        sink = _sink(FakeRenderer(device_name=""))
        assert sink._controls.get(DEVICE_NAME_CONTROL_ID) is None

    def test_set_writes_device_and_syncs_reported_name(self):
        renderer = FakeRenderer("Old", reported="New Name")
        sink = _sink(renderer)
        renamed = []
        sink.on_device_renamed = renamed.append

        resp = sink._handle_control_set(control_set(7, {DEVICE_NAME_CONTROL_ID: "  New Name  "}))

        assert renderer.set_name_calls == ["New Name"]  # stripped
        assert resp.data["status"] == "ok"
        assert resp.data["applied"] == {DEVICE_NAME_CONTROL_ID: "New Name"}
        assert sink._controls.get_value(DEVICE_NAME_CONTROL_ID) == "New Name"
        assert renamed == ["New Name"]

    def test_applied_value_is_what_device_reports(self):
        # Firmware truncates to 15 bytes / restores default on empty; the
        # response must carry the device's truth, not the request.
        renderer = FakeRenderer("Old", reported="LTP-LPD8806")
        sink = _sink(renderer)
        resp = sink._handle_control_set(control_set(1, {DEVICE_NAME_CONTROL_ID: ""}))
        assert renderer.set_name_calls == [""]
        assert resp.data["applied"][DEVICE_NAME_CONTROL_ID] == "LTP-LPD8806"
        assert sink._controls.get_value(DEVICE_NAME_CONTROL_ID) == "LTP-LPD8806"

    def test_device_failure_is_reported_not_applied(self):
        renderer = FakeRenderer("Old", accept=False)
        sink = _sink(renderer)
        renamed = []
        sink.on_device_renamed = renamed.append

        resp = sink._handle_control_set(control_set(1, {DEVICE_NAME_CONTROL_ID: "X"}))

        assert resp.data["status"] != "ok"
        assert DEVICE_NAME_CONTROL_ID in resp.data["errors"]
        assert resp.data["applied"] == {}
        assert sink._controls.get_value(DEVICE_NAME_CONTROL_ID) == "Old"
        assert renamed == []

    def test_callback_failure_does_not_fail_the_set(self):
        sink = _sink(FakeRenderer("Old"))

        def boom(_):
            raise RuntimeError("nope")

        sink.on_device_renamed = boom
        resp = sink._handle_control_set(control_set(1, {DEVICE_NAME_CONTROL_ID: "New"}))
        assert resp.data["status"] == "ok"


class TestSinkRename:
    async def test_rename_updates_config_and_readvertises(self):
        sink = _sink(FakeRenderer("Old"))
        events = []

        class FakeAdvertiser:
            def __init__(self, name):
                self.name = name

            async def stop(self):
                events.append(("stop", self.name))

            async def start(self):
                events.append(("start", self.name))

        sink._advertiser = FakeAdvertiser("old")
        sink._build_advertiser = lambda: FakeAdvertiser(sink.config.name)

        await sink.rename("New Name")

        assert sink.config.name == "New Name"
        assert events == [("stop", "old"), ("start", "New Name")]

    async def test_rename_noop_when_unchanged(self):
        sink = _sink(FakeRenderer("Old"))
        sink._advertiser = None
        await sink.rename("Sink")
        assert sink.config.name == "Sink"

    async def test_rename_before_start_only_updates_config(self):
        sink = _sink(FakeRenderer("Old"))
        await sink.rename("Later")
        assert sink.config.name == "Later"


class _Member:
    def __init__(self, name):
        self.name = name
        self.sink = SimpleNamespace(rename=self._rename)
        self.renamed_to = []

    async def _rename(self, name):
        self.renamed_to.append(name)


class TestFleetFollowsRename:
    def _fleet(self):
        return SerialFleet(FleetConfig(), provision_store=SimpleNamespace(get=lambda k: None))

    def test_unique_name_ignores_own_entry(self):
        fleet = self._fleet()
        fleet.members["/dev/ttyUSB0"] = _Member("Hall")
        fleet.members["/dev/ttyUSB1"] = _Member("Porch")
        # Renaming USB0 to its own current name must not get a suffix...
        assert fleet._unique_name("Hall", "/dev/ttyUSB0") == "Hall"
        # ...but colliding with another member still does.
        assert fleet._unique_name("Porch", "/dev/ttyUSB0") == "Porch (ttyUSB0)"

    async def test_member_rename_readvertises_sink(self):
        fleet = self._fleet()
        member = _Member("Hall")
        fleet.members["/dev/ttyUSB0"] = member

        fleet._on_member_renamed("/dev/ttyUSB0", "Kitchen")
        await asyncio.sleep(0)  # let the scheduled rename task run

        assert member.name == "Kitchen"
        assert member.renamed_to == ["Kitchen"]

    async def test_member_rename_ignores_unknown_or_empty(self):
        fleet = self._fleet()
        member = _Member("Hall")
        fleet.members["/dev/ttyUSB0"] = member
        fleet._on_member_renamed("/dev/ttyUSB9", "Kitchen")
        fleet._on_member_renamed("/dev/ttyUSB0", "")
        await asyncio.sleep(0)
        assert member.name == "Hall"
        assert member.renamed_to == []

    async def test_adopt_wires_callback_unless_name_overridden(self, monkeypatch, tmp_path):
        created = []

        class FakeSink:
            def __init__(self, config, renderer=None):
                self.config = config
                self.on_device_renamed = None
                created.append(self)

            async def run(self):
                await asyncio.Event().wait()

        monkeypatch.setattr(fleet_mod, "SerialSink", FakeSink)
        port = tmp_path / "ttyUSB0"
        port.touch()

        class R:
            device_info = SimpleNamespace(device_name="Hall")
            build_info = None

        cand = fleet_mod.PortCandidate(path=str(port), real_path=str(port))

        fleet = self._fleet()
        fleet._adopt(cand, R())
        assert created[-1].on_device_renamed is not None
        for m in fleet.members.values():
            m.task.cancel()

        # With a configured override name, the fleet name is authoritative.
        cfg = fleet_mod.load_fleet_config(
            {"devices": [{"match": {"port": str(port)}, "name": "Fixed"}]}
        )
        fleet2 = SerialFleet(cfg, provision_store=SimpleNamespace(get=lambda k: None))
        fleet2._adopt(cand, R())
        assert fleet2.members[str(port)].name == "Fixed"
        assert created[-1].on_device_renamed is None
        for m in fleet2.members.values():
            m.task.cancel()
