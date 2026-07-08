"""Tests for device-rename migration in the controller's discovery handling.

Devices are keyed by mDNS service name, which derives from the display name;
renaming a device therefore re-advertises it under a new key. The controller
must migrate the existing entry (same device UUID) instead of leaving a
stale offline ghost alongside a duplicate-id newcomer.
"""

import asyncio
from uuid import uuid4

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
