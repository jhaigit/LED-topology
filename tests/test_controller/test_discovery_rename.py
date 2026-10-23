"""Tests for device-rename migration in the controller's discovery handling.

Devices are keyed by mDNS service name, which derives from the display name;
renaming a device therefore re-advertises it under a new key. The controller
must migrate the existing entry (same device UUID) instead of leaving a
stale offline ghost alongside a duplicate-id newcomer.
"""

import asyncio
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from libltp.discovery import SERVICE_TYPE_SINK, DiscoveredDevice
from ltp_controller.controller import Controller


def _device(name: str, device_id, port: int = 5000) -> DiscoveredDevice:
    return DiscoveredDevice(
        name=name,
        service_type=SERVICE_TYPE_SINK,
        host="block.local.",
        port=port,
        device_id=device_id,
        display_name=name.replace("-", " ").title(),
        description="",
        addresses=["10.0.1.251"],
    )


@pytest.fixture()
def controller(monkeypatch):
    c = Controller(name="test-controller")
    # _handle_sink schedules capability fetches; neuter them for unit tests.
    monkeypatch.setattr(c, "_fetch_device_info", lambda state: _noop())
    return c


async def _noop():
    return None


class TestRenameMigration:
    async def test_rename_migrates_instead_of_duplicating(self, controller):
        device_id = uuid4()
        controller._handle_sink(_device("old-name", device_id), True)
        assert len(controller.sinks) == 1
        original_state = controller.sinks[0]
        stable_id = original_state.id
        first_seen = original_state.first_seen

        # Same device re-advertises under a new service name (renamed)
        controller._handle_sink(_device("new-name", device_id, port=5001), True)

        assert len(controller.sinks) == 1  # no ghost entry
        state = controller.sinks[0]
        assert state is original_state  # state object reused
        assert state.id == stable_id  # routes keep working
        assert state.first_seen == first_seen
        assert state.name == "New Name"
        assert state.online

    async def test_late_goodbye_for_old_name_is_ignored(self, controller):
        device_id = uuid4()
        controller._handle_sink(_device("old-name", device_id), True)
        controller._handle_sink(_device("new-name", device_id), True)

        # mDNS goodbye for the old service name arrives after the rename
        controller._handle_sink(_device("old-name", device_id), False)

        assert len(controller.sinks) == 1
        assert controller.sinks[0].online  # not knocked offline by the ghost

    async def test_goodbye_before_new_advertisement(self, controller):
        device_id = uuid4()
        controller._handle_sink(_device("old-name", device_id), True)
        controller._handle_sink(_device("old-name", device_id), False)
        assert not controller.sinks[0].online

        controller._handle_sink(_device("new-name", device_id), True)
        assert len(controller.sinks) == 1
        assert controller.sinks[0].online
        assert controller.sinks[0].name == "New Name"

    async def test_distinct_devices_not_merged(self, controller):
        controller._handle_sink(_device("sink-a", uuid4()), True)
        controller._handle_sink(_device("sink-b", uuid4()), True)
        assert len(controller.sinks) == 2

    async def test_no_device_id_never_migrates(self, controller):
        controller._handle_sink(_device("name-a", None), True)
        controller._handle_sink(_device("name-b", None), True)
        assert len(controller.sinks) == 2

    async def test_plain_readvertisement_still_updates(self, controller):
        device_id = uuid4()
        controller._handle_sink(_device("same-name", device_id, port=5000), True)
        controller._handle_sink(_device("same-name", device_id, port=5002), True)
        assert len(controller.sinks) == 1
        assert controller.sinks[0].port == 5002

    async def test_source_rename_migrates_too(self, controller):
        from libltp.discovery import SERVICE_TYPE_SOURCE

        device_id = uuid4()

        def src(name):
            return DiscoveredDevice(
                name=name,
                service_type=SERVICE_TYPE_SOURCE,
                host="h.local.",
                port=6000,
                device_id=device_id,
                display_name=name,
                description="",
            )

        controller._handle_source(src("src-old"), True)
        controller._handle_source(src("src-new"), True)
        assert len(controller.sources) == 1
        assert controller.sources[0].name == "src-new"


def _v5(key: str):
    """A deterministic (fleet-style) device ID."""
    return uuid5(NAMESPACE_URL, key)


class TestDeviceIdChange:
    """A known device re-advertising under the same service name with a new
    ID: deterministic (uuid5) IDs are adopted and every holder re-pointed;
    random (uuid4) IDs stay locked so routes survive sink restarts."""

    async def test_random_id_change_stays_locked(self, controller):
        first = uuid4()
        controller._handle_sink(_device("office-lights", first), True)
        controller._handle_sink(_device("office-lights", uuid4()), True)
        assert controller.sinks[0].id == str(first)

    async def test_deterministic_id_change_is_adopted(self, controller):
        seen = []
        controller.on_device_id_change(lambda old, new: seen.append((old, new)))
        old, new = _v5("ttyUSB0:LTP-328P-PWM"), _v5("ttyUSB0:Office Lights")
        controller._handle_sink(_device("office-lights", old), True)
        controller._handle_sink(_device("office-lights", new), True)

        assert len(controller.sinks) == 1
        assert controller.sinks[0].id == str(new)
        assert controller.get_sink(str(new)) is controller.sinks[0]
        assert controller.get_sink(str(old)) is None
        assert seen == [(str(old), str(new))]

    async def test_unchanged_id_fires_nothing(self, controller):
        seen = []
        controller.on_device_id_change(lambda old, new: seen.append((old, new)))
        device_id = _v5("ttyACM0")
        controller._handle_sink(_device("big-array", device_id), True)
        controller._handle_sink(_device("big-array", device_id, port=5001), True)
        assert seen == []

    async def test_failing_listener_does_not_block_others(self, controller):
        seen = []

        def boom(old, new):
            raise RuntimeError("listener bug")

        controller.on_device_id_change(boom)
        controller.on_device_id_change(lambda old, new: seen.append(new))
        new = _v5("b")
        controller._handle_sink(_device("s", _v5("a")), True)
        controller._handle_sink(_device("s", new), True)
        assert seen == [str(new)]
        assert controller.sinks[0].id == str(new)

    async def test_holders_are_repointed(self, controller, tmp_path):
        from ltp_controller.input_manager import InputEventManager
        from ltp_controller.keystore import KeyStore
        from ltp_controller.router import RoutingEngine
        from ltp_controller.rule_engine import RuleEngine
        from ltp_controller.rules import Action, ActionType, Trigger, TriggerType
        from ltp_controller.sink_connection_pool import SinkConnectionPool

        old, new = _v5("old"), _v5("new")
        old_id, new_id = str(old), str(new)
        controller._handle_sink(_device("small-square", old), True)

        keystore = KeyStore(path=tmp_path / "keys.yaml")
        keystore.set_key(old_id, b"k" * 16)
        SinkConnectionPool(controller, keystore=keystore)
        router = RoutingEngine(controller)
        route = router.create_route(name="r", source_id="src-1", sink_id=old_id)
        input_manager = InputEventManager(controller)
        input_manager._input_states[old_id] = {0: "state"}
        rules = RuleEngine(controller, router, None, input_manager)
        rule = rules.create_rule(
            "rule",
            Trigger(type=TriggerType.INPUT_CHANGE, sink_id=old_id),
            [Action(type=ActionType.SET_CONTROL, target_id=old_id)],
        )
        group = controller.sink_group_manager.create("g", [old_id, "other-sink"])

        controller._handle_sink(_device("small-square", new), True)

        assert route.sink_id == new_id
        assert [m.sink_id for m in group.members] == [new_id, "other-sink"]
        assert rule.trigger.sink_id == new_id
        assert rule.actions[0].target_id == new_id
        assert input_manager._input_states == {new_id: {0: "state"}}
        assert keystore.get_key(new_id) == b"k" * 16
        assert keystore.get_key(old_id) is None
        reloaded = KeyStore(path=tmp_path / "keys.yaml")
        reloaded.load()
        assert reloaded.device_ids == [new_id]  # persisted
