"""Unit tests for rules dataclasses (Trigger, Action, Rule)."""

import pytest

from ltp_controller.rules import (
    Action,
    ActionType,
    ComparisonOp,
    Rule,
    Trigger,
    TriggerType,
)


class TestTriggerType:
    def test_values(self):
        assert TriggerType.INPUT_CHANGE == "input_change"
        assert TriggerType.INPUT_STATE == "input_state"
        assert TriggerType.SCHEDULE == "schedule"


class TestActionType:
    def test_values(self):
        assert ActionType.SET_CONTROL == "set_control"
        assert ActionType.FILL_SOLID == "fill_solid"
        assert ActionType.CLEAR == "clear"
        assert ActionType.START_SEQUENCE == "start_sequence"
        assert ActionType.STOP_SEQUENCE == "stop_sequence"
        assert ActionType.ENABLE_ROUTE == "enable_route"
        assert ActionType.DISABLE_ROUTE == "disable_route"


class TestComparisonOp:
    def test_values(self):
        assert ComparisonOp.EQUALS == "eq"
        assert ComparisonOp.NOT_EQUALS == "ne"
        assert ComparisonOp.CHANGED_TO == "changed_to"
        assert ComparisonOp.CHANGED_FROM == "changed_from"


class TestTrigger:
    def test_input_change_to_dict(self):
        trigger = Trigger(
            type=TriggerType.INPUT_CHANGE,
            sink_id="sink-123",
            input_id=0,
            comparison=ComparisonOp.CHANGED_TO,
            value=True,
        )
        d = trigger.to_dict()
        assert d["type"] == "input_change"
        assert d["sink_id"] == "sink-123"
        assert d["input_id"] == 0
        assert d["comparison"] == "changed_to"
        assert d["value"] is True

    def test_input_state_to_dict(self):
        trigger = Trigger(
            type=TriggerType.INPUT_STATE,
            sink_id="sink-456",
            input_id=2,
            comparison=ComparisonOp.EQUALS,
            value=5,
        )
        d = trigger.to_dict()
        assert d["type"] == "input_state"
        assert d["comparison"] == "eq"
        assert d["value"] == 5

    def test_schedule_to_dict(self):
        trigger = Trigger(
            type=TriggerType.SCHEDULE,
            cron="0 8 * * *",
            jitter_minutes=5.0,
            days=["mon", "tue", "wed"],
        )
        d = trigger.to_dict()
        assert d["type"] == "schedule"
        assert d["cron"] == "0 8 * * *"
        assert d["jitter_minutes"] == 5.0
        assert d["days"] == ["mon", "tue", "wed"]

    def test_schedule_no_jitter_no_days(self):
        trigger = Trigger(
            type=TriggerType.SCHEDULE,
            cron="*/5 * * * *",
        )
        d = trigger.to_dict()
        assert "jitter_minutes" not in d
        assert "days" not in d

    def test_from_dict_input(self):
        data = {
            "type": "input_change",
            "sink_id": "abc",
            "input_id": 1,
            "comparison": "changed_from",
            "value": False,
        }
        trigger = Trigger.from_dict(data)
        assert trigger.type == TriggerType.INPUT_CHANGE
        assert trigger.sink_id == "abc"
        assert trigger.input_id == 1
        assert trigger.comparison == ComparisonOp.CHANGED_FROM
        assert trigger.value is False

    def test_from_dict_schedule(self):
        data = {
            "type": "schedule",
            "cron": "30 22 * * *",
            "jitter_minutes": 2.0,
            "days": ["fri", "sat"],
        }
        trigger = Trigger.from_dict(data)
        assert trigger.type == TriggerType.SCHEDULE
        assert trigger.cron == "30 22 * * *"
        assert trigger.jitter_minutes == 2.0
        assert trigger.days == ["fri", "sat"]

    def test_roundtrip(self):
        trigger = Trigger(
            type=TriggerType.INPUT_CHANGE,
            sink_id="s1",
            input_id=3,
            comparison=ComparisonOp.NOT_EQUALS,
            value=10,
        )
        restored = Trigger.from_dict(trigger.to_dict())
        assert restored.type == trigger.type
        assert restored.sink_id == trigger.sink_id
        assert restored.input_id == trigger.input_id
        assert restored.comparison == trigger.comparison
        assert restored.value == trigger.value


class TestAction:
    def test_set_control_to_dict(self):
        action = Action(
            type=ActionType.SET_CONTROL,
            target_id="sink-1",
            control_id="brightness",
            value=200,
        )
        d = action.to_dict()
        assert d["type"] == "set_control"
        assert d["target_id"] == "sink-1"
        assert d["control_id"] == "brightness"
        assert d["value"] == 200

    def test_fill_solid_to_dict(self):
        action = Action(
            type=ActionType.FILL_SOLID,
            target_id="sink-2",
            value="#FF0000",
        )
        d = action.to_dict()
        assert d["type"] == "fill_solid"
        assert d["value"] == "#FF0000"

    def test_clear_to_dict(self):
        action = Action(type=ActionType.CLEAR, target_id="sink-3")
        d = action.to_dict()
        assert d["type"] == "clear"
        assert d["target_id"] == "sink-3"

    def test_from_dict(self):
        data = {
            "type": "start_sequence",
            "target_id": "seq-abc",
        }
        action = Action.from_dict(data)
        assert action.type == ActionType.START_SEQUENCE
        assert action.target_id == "seq-abc"

    def test_roundtrip(self):
        action = Action(
            type=ActionType.SET_CONTROL,
            target_id="sink-1",
            control_id="gamma",
            value=2.2,
        )
        restored = Action.from_dict(action.to_dict())
        assert restored.type == action.type
        assert restored.target_id == action.target_id
        assert restored.control_id == action.control_id
        assert restored.value == action.value


class TestRule:
    def test_create(self):
        trigger = Trigger(type=TriggerType.INPUT_CHANGE, sink_id="s1", input_id=0)
        actions = [Action(type=ActionType.FILL_SOLID, target_id="s1", value="#00FF00")]
        rule = Rule.create("My Rule", trigger, actions)

        assert rule.name == "My Rule"
        assert rule.enabled is True
        assert rule.trigger == trigger
        assert rule.actions == actions
        assert rule.id  # non-empty

    def test_create_disabled(self):
        trigger = Trigger(type=TriggerType.SCHEDULE, cron="0 * * * *")
        rule = Rule.create("Disabled Rule", trigger, [], enabled=False)
        assert rule.enabled is False

    def test_to_dict(self):
        trigger = Trigger(type=TriggerType.INPUT_CHANGE, sink_id="s1", input_id=0)
        actions = [Action(type=ActionType.CLEAR, target_id="s1")]
        rule = Rule(
            id="rule-1",
            name="Test Rule",
            enabled=True,
            trigger=trigger,
            actions=actions,
            trigger_count=5,
        )
        d = rule.to_dict()
        assert d["id"] == "rule-1"
        assert d["name"] == "Test Rule"
        assert d["enabled"] is True
        assert d["trigger"]["type"] == "input_change"
        assert len(d["actions"]) == 1
        assert d["trigger_count"] == 5

    def test_from_dict(self):
        data = {
            "id": "rule-2",
            "name": "Rule Two",
            "enabled": False,
            "trigger": {
                "type": "schedule",
                "cron": "0 9 * * *",
            },
            "actions": [
                {"type": "start_sequence", "target_id": "seq-1"},
            ],
        }
        rule = Rule.from_dict(data)
        assert rule.id == "rule-2"
        assert rule.name == "Rule Two"
        assert rule.enabled is False
        assert rule.trigger.type == TriggerType.SCHEDULE
        assert rule.trigger.cron == "0 9 * * *"
        assert len(rule.actions) == 1
        assert rule.actions[0].type == ActionType.START_SEQUENCE

    def test_roundtrip(self):
        trigger = Trigger(
            type=TriggerType.SCHEDULE,
            cron="*/10 * * * *",
            jitter_minutes=1.0,
            days=["mon"],
        )
        actions = [
            Action(type=ActionType.FILL_SOLID, target_id="s1", value="#FFFFFF"),
            Action(type=ActionType.SET_CONTROL, target_id="s2", control_id="brightness", value=100),
        ]
        rule = Rule.create("Roundtrip", trigger, actions)
        restored = Rule.from_dict(rule.to_dict())

        assert restored.id == rule.id
        assert restored.name == rule.name
        assert restored.trigger.cron == "*/10 * * * *"
        assert len(restored.actions) == 2
