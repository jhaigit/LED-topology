"""Sink groups for ganging multiple physical sinks into one virtual strip."""

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ltp_controller.controller import Controller

logger = logging.getLogger(__name__)


@dataclass
class SinkGroupMember:
    """A physical sink within a group."""

    sink_id: str
    pixel_offset: int = 0
    pixel_count: int = 0
    reversed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sink_id": self.sink_id,
            "pixel_offset": self.pixel_offset,
            "pixel_count": self.pixel_count,
            "reversed": self.reversed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SinkGroupMember":
        return cls(
            sink_id=data.get("sink_id") or data.get("sink", ""),
            pixel_offset=data.get("pixel_offset", 0),
            pixel_count=data.get("pixel_count", 0),
            reversed=data.get("reversed", False),
        )


@dataclass
class SinkGroup:
    """A group of physical sinks treated as one continuous strip."""

    id: str
    name: str
    members: list[SinkGroupMember] = field(default_factory=list)
    total_pixels: int = 0

    def recompute(self, controller: "Controller") -> None:
        """Recompute pixel offsets and total from live sink data."""
        offset = 0
        for member in self.members:
            sink = controller.get_sink(member.sink_id)
            if sink:
                dims = self._get_sink_pixels(sink)
                member.pixel_count = dims
            # Keep last known pixel_count for offline sinks
            member.pixel_offset = offset
            offset += member.pixel_count
        self.total_pixels = offset

    @staticmethod
    def _get_sink_pixels(sink: Any) -> int:
        props = sink.device.properties
        if props.get("dim"):
            parts = props["dim"].split("x")
            result = 1
            for p in parts:
                result *= int(p)
            return result
        if props.get("pixels"):
            return int(props["pixels"])
        return 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "members": [m.to_dict() for m in self.members],
            "total_pixels": self.total_pixels,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SinkGroup":
        members = [SinkGroupMember.from_dict(m) for m in data.get("members", [])]
        return cls(
            id=data.get("id", f"sg-{uuid4().hex[:8]}"),
            name=data["name"],
            members=members,
            total_pixels=data.get("total_pixels", 0),
        )


class SinkGroupManager:
    """Manages sink groups."""

    def __init__(self, controller: "Controller"):
        self.controller = controller
        self._groups: dict[str, SinkGroup] = {}

    @property
    def groups(self) -> list[SinkGroup]:
        return list(self._groups.values())

    def get(self, identifier: str) -> SinkGroup | None:
        if identifier in self._groups:
            return self._groups[identifier]
        for g in self._groups.values():
            if g.name == identifier:
                return g
        return None

    def create(
        self,
        name: str,
        member_sink_ids: list[str],
        reversed_flags: list[bool] | None = None,
    ) -> SinkGroup:
        """Create a new sink group from a list of sink IDs."""
        members = []
        for i, sid in enumerate(member_sink_ids):
            rev = reversed_flags[i] if reversed_flags and i < len(reversed_flags) else False
            members.append(SinkGroupMember(sink_id=sid, reversed=rev))

        group = SinkGroup(
            id=f"sg-{uuid4().hex[:8]}",
            name=name,
            members=members,
        )
        group.recompute(self.controller)
        self._groups[group.id] = group
        logger.info(f"Created sink group: {group.name} ({group.total_pixels} pixels, {len(members)} members)")
        return group

    def update(
        self,
        group_id: str,
        name: str | None = None,
        member_sink_ids: list[str] | None = None,
        reversed_flags: list[bool] | None = None,
    ) -> SinkGroup | None:
        group = self._groups.get(group_id)
        if not group:
            return None

        if name is not None:
            group.name = name

        if member_sink_ids is not None:
            members = []
            for i, sid in enumerate(member_sink_ids):
                rev = reversed_flags[i] if reversed_flags and i < len(reversed_flags) else False
                members.append(SinkGroupMember(sink_id=sid, reversed=rev))
            group.members = members

        group.recompute(self.controller)
        logger.info(f"Updated sink group: {group.name}")
        return group

    def delete(self, group_id: str) -> bool:
        group = self._groups.pop(group_id, None)
        if group:
            logger.info(f"Deleted sink group: {group.name}")
            return True
        return False

    def on_member_sink_changed(self, sink_id: str) -> None:
        """Called when a member sink comes online or changes pixel count."""
        for group in self._groups.values():
            recompute_needed = False
            for member in group.members:
                if member.sink_id == sink_id:
                    recompute_needed = True
                    break
                resolved = self.controller.get_sink(member.sink_id)
                if resolved and resolved.id == sink_id:
                    member.sink_id = sink_id
                    recompute_needed = True
                    break
            if recompute_needed:
                group.recompute(self.controller)
                logger.debug(f"Recomputed group {group.name} (member {sink_id} changed)")

    def load_from_config(self, config: list[dict[str, Any]]) -> None:
        for data in config:
            try:
                group = SinkGroup.from_dict(data)
                # Resolve sink names to IDs
                for member in group.members:
                    sink = self.controller.get_sink(member.sink_id)
                    if sink:
                        member.sink_id = sink.id
                group.recompute(self.controller)
                self._groups[group.id] = group
                logger.debug(f"Loaded sink group: {group.name}")
            except Exception as e:
                logger.error(f"Failed to load sink group: {e}")
        logger.info(f"Loaded {len(self._groups)} sink groups")

    def to_config(self) -> list[dict[str, Any]]:
        return [g.to_dict() for g in self._groups.values()]
