"""Data structures for input event rules."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class TriggerType(str, Enum):
    """Type of trigger for a rule."""

    INPUT_CHANGE = "input_change"  # Edge: value changed
    INPUT_STATE = "input_state"    # Level: matches state


class ActionType(str, Enum):
    """Type of action to execute."""

    SET_CONTROL = "set_control"
    ENABLE_ROUTE = "enable_route"
    DISABLE_ROUTE = "disable_route"
    ENABLE_SOURCE = "enable_source"
    DISABLE_SOURCE = "disable_source"


class ComparisonOp(str, Enum):
    """Comparison operator for trigger conditions."""

    EQUALS = "eq"
    NOT_EQUALS = "ne"
    CHANGED_TO = "changed_to"
    CHANGED_FROM = "changed_from"


@dataclass
class Trigger:
    """Defines when a rule should fire."""

    type: TriggerType
    sink_id: str
    input_id: int
    comparison: ComparisonOp = ComparisonOp.CHANGED_TO
    value: Any = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "type": self.type.value,
            "sink_id": self.sink_id,
            "input_id": self.input_id,
            "comparison": self.comparison.value,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trigger":
        """Create from dictionary."""
        return cls(
            type=TriggerType(data["type"]),
            sink_id=data["sink_id"],
            input_id=data["input_id"],
            comparison=ComparisonOp(data.get("comparison", "changed_to")),
            value=data.get("value", True),
        )


@dataclass
class Action:
    """Defines what happens when a rule fires."""

    type: ActionType
    target_id: str              # sink_id, route_id, or source_id
    control_id: str | None = None  # for SET_CONTROL
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result: dict[str, Any] = {
            "type": self.type.value,
            "target_id": self.target_id,
        }
        if self.control_id is not None:
            result["control_id"] = self.control_id
        if self.value is not None:
            result["value"] = self.value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        """Create from dictionary."""
        return cls(
            type=ActionType(data["type"]),
            target_id=data["target_id"],
            control_id=data.get("control_id"),
            value=data.get("value"),
        )


@dataclass
class Rule:
    """A complete rule with trigger and actions."""

    id: str
    name: str
    enabled: bool
    trigger: Trigger
    actions: list[Action]
    last_triggered: float | None = None
    trigger_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "trigger": self.trigger.to_dict(),
            "actions": [a.to_dict() for a in self.actions],
            "last_triggered": self.last_triggered,
            "trigger_count": self.trigger_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        """Create from dictionary."""
        return cls(
            id=data.get("id", str(uuid4())),
            name=data["name"],
            enabled=data.get("enabled", True),
            trigger=Trigger.from_dict(data["trigger"]),
            actions=[Action.from_dict(a) for a in data.get("actions", [])],
            last_triggered=data.get("last_triggered"),
            trigger_count=data.get("trigger_count", 0),
        )

    @classmethod
    def create(
        cls,
        name: str,
        trigger: Trigger,
        actions: list[Action],
        enabled: bool = True,
    ) -> "Rule":
        """Create a new rule with auto-generated ID."""
        return cls(
            id=str(uuid4()),
            name=name,
            enabled=enabled,
            trigger=trigger,
            actions=actions,
        )


@dataclass
class InputState:
    """Current state of an input on a sink."""

    input_id: int
    name: str
    input_type: str  # button, switch, encoder, analog, motion, etc.
    value: Any
    timestamp: int | None = None  # Device timestamp in milliseconds

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "input_id": self.input_id,
            "name": self.name,
            "type": self.input_type,
            "value": self.value,
            "timestamp": self.timestamp,
        }
