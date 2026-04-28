"""Unit tests for SinkGroup, SinkGroupMember, and SinkGroupManager."""

from unittest.mock import MagicMock

import pytest

from ltp_controller.sink_group import SinkGroup, SinkGroupManager, SinkGroupMember


def _make_mock_controller(sinks: dict[str, dict]):
    """Create a mock controller with configurable sink properties.

    sinks: {sink_id: {"pixels": N} or {"dim": "WxH"}}
    """
    controller = MagicMock()

    def get_sink(sink_id):
        if sink_id not in sinks:
            return None
        mock_sink = MagicMock()
        mock_sink.id = sink_id
        mock_sink.device.properties = sinks[sink_id]
        return mock_sink

    controller.get_sink.side_effect = get_sink
    return controller


class TestSinkGroupMember:
    def test_defaults(self):
        member = SinkGroupMember(sink_id="abc")
        assert member.pixel_offset == 0
        assert member.pixel_count == 0
        assert member.reversed is False

    def test_to_dict(self):
        member = SinkGroupMember(
            sink_id="sink-1", pixel_offset=60, pixel_count=100, reversed=True
        )
        d = member.to_dict()
        assert d == {
            "sink_id": "sink-1",
            "pixel_offset": 60,
            "pixel_count": 100,
            "reversed": True,
        }

    def test_from_dict(self):
        data = {"sink_id": "s1", "pixel_offset": 10, "pixel_count": 50, "reversed": False}
        member = SinkGroupMember.from_dict(data)
        assert member.sink_id == "s1"
        assert member.pixel_offset == 10
        assert member.pixel_count == 50

    def test_from_dict_legacy_sink_key(self):
        data = {"sink": "legacy-id", "pixel_count": 30}
        member = SinkGroupMember.from_dict(data)
        assert member.sink_id == "legacy-id"

    def test_from_dict_defaults(self):
        data = {"sink_id": "s2"}
        member = SinkGroupMember.from_dict(data)
        assert member.pixel_offset == 0
        assert member.pixel_count == 0
        assert member.reversed is False


class TestSinkGroup:
    def test_recompute_sequential(self):
        controller = _make_mock_controller({
            "s1": {"pixels": "60"},
            "s2": {"pixels": "100"},
            "s3": {"pixels": "60"},
        })
        group = SinkGroup(
            id="sg-1",
            name="Test Group",
            members=[
                SinkGroupMember(sink_id="s1"),
                SinkGroupMember(sink_id="s2"),
                SinkGroupMember(sink_id="s3"),
            ],
        )
        group.recompute(controller)

        assert group.members[0].pixel_offset == 0
        assert group.members[0].pixel_count == 60
        assert group.members[1].pixel_offset == 60
        assert group.members[1].pixel_count == 100
        assert group.members[2].pixel_offset == 160
        assert group.members[2].pixel_count == 60
        assert group.total_pixels == 220

    def test_recompute_with_dim_property(self):
        controller = _make_mock_controller({
            "s1": {"dim": "16x10"},
            "s2": {"dim": "8x8"},
        })
        group = SinkGroup(
            id="sg-2",
            name="Matrix Group",
            members=[
                SinkGroupMember(sink_id="s1"),
                SinkGroupMember(sink_id="s2"),
            ],
        )
        group.recompute(controller)

        assert group.members[0].pixel_count == 160  # 16*10
        assert group.members[1].pixel_count == 64   # 8*8
        assert group.members[1].pixel_offset == 160
        assert group.total_pixels == 224

    def test_recompute_offline_sink_keeps_last_count(self):
        controller = _make_mock_controller({"s1": {"pixels": "100"}})
        group = SinkGroup(
            id="sg-3",
            name="Partial",
            members=[
                SinkGroupMember(sink_id="s1"),
                SinkGroupMember(sink_id="offline", pixel_count=50),
            ],
        )
        group.recompute(controller)

        assert group.members[0].pixel_count == 100
        assert group.members[1].pixel_count == 50  # retained
        assert group.members[1].pixel_offset == 100
        assert group.total_pixels == 150

    def test_recompute_default_pixel_count(self):
        controller = _make_mock_controller({"s1": {}})
        group = SinkGroup(
            id="sg-4",
            name="Default",
            members=[SinkGroupMember(sink_id="s1")],
        )
        group.recompute(controller)
        assert group.members[0].pixel_count == 60

    def test_to_dict(self):
        group = SinkGroup(
            id="sg-5",
            name="My Group",
            members=[SinkGroupMember(sink_id="a", pixel_offset=0, pixel_count=60)],
            total_pixels=60,
        )
        d = group.to_dict()
        assert d["id"] == "sg-5"
        assert d["name"] == "My Group"
        assert d["total_pixels"] == 60
        assert len(d["members"]) == 1

    def test_from_dict(self):
        data = {
            "id": "sg-6",
            "name": "Loaded",
            "members": [
                {"sink_id": "x", "pixel_offset": 0, "pixel_count": 80, "reversed": True},
            ],
            "total_pixels": 80,
        }
        group = SinkGroup.from_dict(data)
        assert group.id == "sg-6"
        assert group.name == "Loaded"
        assert group.total_pixels == 80
        assert group.members[0].reversed is True

    def test_from_dict_auto_id(self):
        data = {"name": "Auto ID"}
        group = SinkGroup.from_dict(data)
        assert group.id.startswith("sg-")

    def test_roundtrip(self):
        group = SinkGroup(
            id="sg-rt",
            name="Roundtrip",
            members=[
                SinkGroupMember(sink_id="a", pixel_offset=0, pixel_count=60),
                SinkGroupMember(sink_id="b", pixel_offset=60, pixel_count=100, reversed=True),
            ],
            total_pixels=160,
        )
        restored = SinkGroup.from_dict(group.to_dict())
        assert restored.id == group.id
        assert restored.total_pixels == group.total_pixels
        assert len(restored.members) == 2
        assert restored.members[1].reversed is True


class TestSinkGroupManager:
    def setup_method(self):
        self.controller = _make_mock_controller({
            "sink-a": {"pixels": "60"},
            "sink-b": {"pixels": "100"},
            "sink-c": {"pixels": "60"},
        })
        self.manager = SinkGroupManager(self.controller)

    def test_create(self):
        group = self.manager.create("Test", ["sink-a", "sink-b"])
        assert group.name == "Test"
        assert group.id.startswith("sg-")
        assert len(group.members) == 2
        assert group.total_pixels == 160

    def test_create_with_reversed(self):
        group = self.manager.create("Rev", ["sink-a", "sink-b"], reversed_flags=[False, True])
        assert group.members[0].reversed is False
        assert group.members[1].reversed is True

    def test_get_by_id(self):
        group = self.manager.create("Find", ["sink-a"])
        assert self.manager.get(group.id) is group

    def test_get_by_name(self):
        self.manager.create("By Name", ["sink-a"])
        found = self.manager.get("By Name")
        assert found is not None
        assert found.name == "By Name"

    def test_get_not_found(self):
        assert self.manager.get("nonexistent") is None

    def test_update_name(self):
        group = self.manager.create("Old", ["sink-a"])
        updated = self.manager.update(group.id, name="New")
        assert updated.name == "New"

    def test_update_members(self):
        group = self.manager.create("Update", ["sink-a"])
        updated = self.manager.update(group.id, member_sink_ids=["sink-a", "sink-b", "sink-c"])
        assert len(updated.members) == 3
        assert updated.total_pixels == 220

    def test_update_nonexistent(self):
        assert self.manager.update("fake", name="x") is None

    def test_delete(self):
        group = self.manager.create("Delete Me", ["sink-a"])
        assert self.manager.delete(group.id) is True
        assert self.manager.get(group.id) is None

    def test_delete_nonexistent(self):
        assert self.manager.delete("fake") is False

    def test_groups_property(self):
        self.manager.create("A", ["sink-a"])
        self.manager.create("B", ["sink-b"])
        assert len(self.manager.groups) == 2

    def test_on_member_sink_changed(self):
        group = self.manager.create("Dynamic", ["sink-a", "sink-b"])
        original_total = group.total_pixels

        # Change sink-a pixels
        self.controller.get_sink.side_effect = None
        mock_sink_a = MagicMock()
        mock_sink_a.id = "sink-a"
        mock_sink_a.device.properties = {"pixels": "120"}
        mock_sink_b = MagicMock()
        mock_sink_b.id = "sink-b"
        mock_sink_b.device.properties = {"pixels": "100"}

        def get_sink(sid):
            if sid == "sink-a":
                return mock_sink_a
            if sid == "sink-b":
                return mock_sink_b
            return None

        self.controller.get_sink.side_effect = get_sink
        self.manager.on_member_sink_changed("sink-a")

        assert group.total_pixels == 220  # 120 + 100

    def test_load_from_config(self):
        config = [
            {
                "id": "sg-cfg",
                "name": "Config Group",
                "members": [
                    {"sink_id": "sink-a", "pixel_count": 60},
                    {"sink_id": "sink-b", "pixel_count": 100},
                ],
                "total_pixels": 160,
            }
        ]
        self.manager.load_from_config(config)
        assert len(self.manager.groups) == 1
        group = self.manager.get("sg-cfg")
        assert group is not None
        assert group.name == "Config Group"

    def test_to_config(self):
        self.manager.create("Persist", ["sink-a", "sink-b"])
        config = self.manager.to_config()
        assert len(config) == 1
        assert config[0]["name"] == "Persist"
        assert config[0]["total_pixels"] == 160
