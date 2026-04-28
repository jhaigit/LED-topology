"""Unit tests for Sequence, SequenceStep, and SequenceManager."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from ltp_controller.rules import Action, ActionType
from ltp_controller.sequence import Sequence, SequenceManager, SequenceState, SequenceStep


class TestSequenceStep:
    def test_effective_delay_no_jitter(self):
        step = SequenceStep(
            action=Action(type=ActionType.CLEAR, target_id="s1"),
            delay_before=2.0,
            jitter=0.0,
        )
        assert step.effective_delay() == 2.0

    def test_effective_delay_with_jitter(self):
        step = SequenceStep(
            action=Action(type=ActionType.CLEAR, target_id="s1"),
            delay_before=1.0,
            jitter=0.5,
        )
        delays = [step.effective_delay() for _ in range(100)]
        assert all(1.0 <= d <= 1.5 for d in delays)
        assert not all(d == delays[0] for d in delays)

    def test_to_dict(self):
        step = SequenceStep(
            action=Action(type=ActionType.FILL_SOLID, target_id="s1", value="#FF0000"),
            delay_before=3.0,
            jitter=1.0,
        )
        d = step.to_dict()
        assert d["delay_before"] == 3.0
        assert d["jitter"] == 1.0
        assert d["action"]["type"] == "fill_solid"
        assert d["action"]["target_id"] == "s1"

    def test_to_dict_no_jitter(self):
        step = SequenceStep(
            action=Action(type=ActionType.CLEAR, target_id="s1"),
            delay_before=1.0,
        )
        d = step.to_dict()
        assert "jitter" not in d

    def test_from_dict(self):
        data = {
            "action": {"type": "clear", "target_id": "s2"},
            "delay_before": 5.0,
            "jitter": 2.0,
        }
        step = SequenceStep.from_dict(data)
        assert step.delay_before == 5.0
        assert step.jitter == 2.0
        assert step.action.type == ActionType.CLEAR

    def test_from_dict_defaults(self):
        data = {"action": {"type": "clear", "target_id": "s1"}}
        step = SequenceStep.from_dict(data)
        assert step.delay_before == 0.0
        assert step.jitter == 0.0

    def test_roundtrip(self):
        step = SequenceStep(
            action=Action(type=ActionType.SET_CONTROL, target_id="s1", control_id="brightness", value=100),
            delay_before=2.5,
            jitter=0.5,
        )
        restored = SequenceStep.from_dict(step.to_dict())
        assert restored.delay_before == step.delay_before
        assert restored.jitter == step.jitter
        assert restored.action.type == step.action.type
        assert restored.action.control_id == step.action.control_id


class TestSequence:
    def test_defaults(self):
        seq = Sequence(id="seq-1", name="Test")
        assert seq.enabled is True
        assert seq.loop is False
        assert seq.steps == []
        assert seq.state == SequenceState.STOPPED
        assert seq.current_step == 0
        assert seq.run_count == 0

    def test_to_dict_includes_state(self):
        seq = Sequence(id="seq-1", name="Running Seq")
        seq.state = SequenceState.RUNNING
        seq.current_step = 2
        seq.run_count = 3

        d = seq.to_dict()
        assert d["state"] == "running"
        assert d["current_step"] == 2
        assert d["run_count"] == 3

    def test_to_config_excludes_state(self):
        seq = Sequence(id="seq-1", name="Persisted Seq", loop=True)
        seq.state = SequenceState.RUNNING
        seq.run_count = 10

        d = seq.to_config()
        assert "state" not in d
        assert "run_count" not in d
        assert "current_step" not in d
        assert d["name"] == "Persisted Seq"
        assert d["loop"] is True

    def test_to_config_no_loop_key_when_false(self):
        seq = Sequence(id="seq-1", name="No Loop", loop=False)
        d = seq.to_config()
        assert "loop" not in d

    def test_from_dict(self):
        data = {
            "id": "seq-2",
            "name": "My Sequence",
            "enabled": False,
            "loop": True,
            "steps": [
                {"action": {"type": "clear", "target_id": "s1"}, "delay_before": 1.0},
                {"action": {"type": "fill_solid", "target_id": "s1", "value": "#FF0000"}, "delay_before": 2.0},
            ],
        }
        seq = Sequence.from_dict(data)
        assert seq.id == "seq-2"
        assert seq.name == "My Sequence"
        assert seq.enabled is False
        assert seq.loop is True
        assert len(seq.steps) == 2
        assert seq.steps[0].delay_before == 1.0
        assert seq.steps[1].action.type == ActionType.FILL_SOLID

    def test_from_dict_generates_id(self):
        data = {"name": "Auto ID"}
        seq = Sequence.from_dict(data)
        assert seq.id  # non-empty, auto-generated

    def test_roundtrip(self):
        steps = [
            SequenceStep(
                action=Action(type=ActionType.FILL_SOLID, target_id="s1", value="#00FF00"),
                delay_before=1.0,
            ),
            SequenceStep(
                action=Action(type=ActionType.CLEAR, target_id="s1"),
                delay_before=2.0,
                jitter=0.5,
            ),
        ]
        seq = Sequence(id="seq-rt", name="Roundtrip", enabled=True, loop=True, steps=steps)
        restored = Sequence.from_dict(seq.to_dict())
        assert restored.id == seq.id
        assert restored.name == seq.name
        assert restored.loop is True
        assert len(restored.steps) == 2


class TestSequenceManager:
    def setup_method(self):
        self.manager = SequenceManager()

    def test_create(self):
        steps = [
            SequenceStep(
                action=Action(type=ActionType.CLEAR, target_id="s1"),
                delay_before=1.0,
            )
        ]
        seq = self.manager.create("Test Seq", steps, enabled=True, loop=False)
        assert seq.name == "Test Seq"
        assert seq.id
        assert seq.enabled is True
        assert len(seq.steps) == 1

    def test_get(self):
        seq = self.manager.create("Find Me", [])
        found = self.manager.get(seq.id)
        assert found is seq

    def test_get_nonexistent(self):
        assert self.manager.get("nonexistent") is None

    def test_get_by_name(self):
        seq = self.manager.create("Named Seq", [])
        found = self.manager.get_by_name("Named Seq")
        assert found is seq

    def test_get_by_name_not_found(self):
        assert self.manager.get_by_name("nope") is None

    def test_update_name(self):
        seq = self.manager.create("Old Name", [])
        updated = self.manager.update(seq.id, name="New Name")
        assert updated.name == "New Name"

    def test_update_steps(self):
        seq = self.manager.create("Update Steps", [])
        new_steps = [
            SequenceStep(action=Action(type=ActionType.CLEAR, target_id="s1"), delay_before=0.5)
        ]
        updated = self.manager.update(seq.id, steps=new_steps)
        assert len(updated.steps) == 1
        assert updated.steps[0].delay_before == 0.5

    def test_update_nonexistent(self):
        result = self.manager.update("fake-id", name="nope")
        assert result is None

    def test_delete(self):
        seq = self.manager.create("Delete Me", [])
        assert self.manager.delete(seq.id) is True
        assert self.manager.get(seq.id) is None

    def test_delete_nonexistent(self):
        assert self.manager.delete("fake") is False

    def test_sequences_property(self):
        self.manager.create("A", [])
        self.manager.create("B", [])
        assert len(self.manager.sequences) == 2

    def test_load_from_config(self):
        config = [
            {
                "name": "Config Seq",
                "enabled": True,
                "steps": [
                    {"action": {"type": "clear", "target_id": "s1"}, "delay_before": 1.0}
                ],
            }
        ]
        self.manager.load_from_config(config)
        assert len(self.manager.sequences) == 1
        assert self.manager.sequences[0].name == "Config Seq"

    def test_to_config(self):
        self.manager.create("Persist", [
            SequenceStep(action=Action(type=ActionType.CLEAR, target_id="s1"), delay_before=2.0)
        ], loop=True)
        config = self.manager.to_config()
        assert len(config) == 1
        assert config[0]["name"] == "Persist"
        assert config[0]["loop"] is True


class TestSequenceExecution:
    def setup_method(self):
        self.manager = SequenceManager()
        self.executor = AsyncMock()
        self.manager.set_action_executor(self.executor)

    @pytest.mark.asyncio
    async def test_start_sequence(self):
        steps = [
            SequenceStep(action=Action(type=ActionType.CLEAR, target_id="s1"), delay_before=0),
            SequenceStep(action=Action(type=ActionType.FILL_SOLID, target_id="s1", value="#FF0000"), delay_before=0),
        ]
        seq = self.manager.create("Run", steps)
        result = await self.manager.start_sequence(seq.id)
        assert result is True

        await asyncio.sleep(0.1)

        assert self.executor.call_count == 2
        assert seq.state == SequenceState.STOPPED
        assert seq.run_count == 1

    @pytest.mark.asyncio
    async def test_start_nonexistent(self):
        result = await self.manager.start_sequence("fake")
        assert result is False

    @pytest.mark.asyncio
    async def test_stop_sequence(self):
        steps = [
            SequenceStep(action=Action(type=ActionType.CLEAR, target_id="s1"), delay_before=10),
        ]
        seq = self.manager.create("Long", steps)
        await self.manager.start_sequence(seq.id)
        await asyncio.sleep(0.05)

        result = await self.manager.stop_sequence(seq.id)
        assert result is True
        assert seq.state == SequenceState.STOPPED

    @pytest.mark.asyncio
    async def test_pause_resume(self):
        steps = [
            SequenceStep(action=Action(type=ActionType.CLEAR, target_id="s1"), delay_before=5),
        ]
        seq = self.manager.create("Pauseable", steps)
        await self.manager.start_sequence(seq.id)
        await asyncio.sleep(0.05)

        result = await self.manager.pause_sequence(seq.id)
        assert result is True
        assert seq.state == SequenceState.PAUSED

        result = await self.manager.resume_sequence(seq.id)
        assert result is True
        assert seq.state == SequenceState.RUNNING

        await self.manager.stop_sequence(seq.id)

    @pytest.mark.asyncio
    async def test_stop_all(self):
        for i in range(3):
            steps = [SequenceStep(action=Action(type=ActionType.CLEAR, target_id="s1"), delay_before=10)]
            seq = self.manager.create(f"Seq {i}", steps)
            await self.manager.start_sequence(seq.id)

        await asyncio.sleep(0.05)
        await self.manager.stop_all()

        for seq in self.manager.sequences:
            assert seq.state == SequenceState.STOPPED
