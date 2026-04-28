"""Sequences: ordered lists of actions with timed delays and jitter."""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from ltp_controller.rules import Action

logger = logging.getLogger(__name__)


class SequenceState(str, Enum):
    """Runtime state of a sequence."""

    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass
class SequenceStep:
    """A single step in a sequence."""

    action: Action
    delay_before: float = 0.0  # seconds to wait before executing
    jitter: float = 0.0        # random 0..jitter seconds added to delay

    def effective_delay(self) -> float:
        """Compute actual delay with random jitter applied."""
        return self.delay_before + random.uniform(0, self.jitter) if self.jitter > 0 else self.delay_before

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action": self.action.to_dict(),
            "delay_before": self.delay_before,
        }
        if self.jitter > 0:
            result["jitter"] = self.jitter
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SequenceStep":
        return cls(
            action=Action.from_dict(data["action"]),
            delay_before=float(data.get("delay_before", 0)),
            jitter=float(data.get("jitter", 0)),
        )


@dataclass
class Sequence:
    """An ordered list of timed actions."""

    id: str
    name: str
    enabled: bool = True
    loop: bool = False
    steps: list[SequenceStep] = field(default_factory=list)

    # Runtime state (not persisted)
    state: SequenceState = field(default=SequenceState.STOPPED, repr=False)
    current_step: int = field(default=0, repr=False)
    run_count: int = field(default=0, repr=False)
    last_started: float | None = field(default=None, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)
    _pause_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def __post_init__(self) -> None:
        self._pause_event.set()  # not paused by default

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "loop": self.loop,
            "steps": [s.to_dict() for s in self.steps],
            "state": self.state.value,
            "current_step": self.current_step,
            "run_count": self.run_count,
            "last_started": self.last_started,
        }

    def to_config(self) -> dict[str, Any]:
        """Export for config persistence (no runtime state)."""
        result: dict[str, Any] = {
            "name": self.name,
            "enabled": self.enabled,
            "steps": [s.to_dict() for s in self.steps],
        }
        if self.loop:
            result["loop"] = True
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Sequence":
        return cls(
            id=data.get("id", str(uuid4())),
            name=data["name"],
            enabled=data.get("enabled", True),
            loop=data.get("loop", False),
            steps=[SequenceStep.from_dict(s) for s in data.get("steps", [])],
        )


class SequenceManager:
    """Manages sequences and their execution."""

    def __init__(self) -> None:
        self._sequences: dict[str, Sequence] = {}
        self._action_executor: Any = None  # set by wiring to rule engine

    def set_action_executor(self, executor: Any) -> None:
        """Set the async action executor (typically RuleEngine._execute_action)."""
        self._action_executor = executor

    # ==================== CRUD ====================

    def create(
        self,
        name: str,
        steps: list[SequenceStep],
        enabled: bool = True,
        loop: bool = False,
    ) -> Sequence:
        seq = Sequence(
            id=str(uuid4()),
            name=name,
            enabled=enabled,
            loop=loop,
            steps=steps,
        )
        self._sequences[seq.id] = seq
        logger.info(f"Created sequence: {seq.name} ({len(seq.steps)} steps)")
        return seq

    def get(self, seq_id: str) -> Sequence | None:
        return self._sequences.get(seq_id)

    def get_by_name(self, name: str) -> Sequence | None:
        for seq in self._sequences.values():
            if seq.name == name:
                return seq
        return None

    def update(
        self,
        seq_id: str,
        name: str | None = None,
        steps: list[SequenceStep] | None = None,
        enabled: bool | None = None,
        loop: bool | None = None,
    ) -> Sequence | None:
        seq = self._sequences.get(seq_id)
        if not seq:
            return None
        if name is not None:
            seq.name = name
        if steps is not None:
            seq.steps = steps
        if enabled is not None:
            seq.enabled = enabled
        if loop is not None:
            seq.loop = loop
        logger.info(f"Updated sequence: {seq.name}")
        return seq

    def delete(self, seq_id: str) -> bool:
        seq = self._sequences.get(seq_id)
        if not seq:
            return False
        if seq.state != SequenceState.STOPPED:
            self._cancel_run(seq)
        del self._sequences[seq_id]
        logger.info(f"Deleted sequence: {seq.name}")
        return True

    @property
    def sequences(self) -> list[Sequence]:
        return list(self._sequences.values())

    # ==================== Persistence ====================

    def load_from_config(self, items: list[dict[str, Any]]) -> None:
        for data in items:
            try:
                seq = Sequence.from_dict(data)
                self._sequences[seq.id] = seq
                logger.debug(f"Loaded sequence: {seq.name}")
            except Exception as e:
                logger.error(f"Failed to load sequence: {e}")
        logger.info(f"Loaded {len(self._sequences)} sequences from config")

    def to_config(self) -> list[dict[str, Any]]:
        return [seq.to_config() for seq in self._sequences.values()]

    # ==================== Execution ====================

    async def start_sequence(self, seq_id: str) -> bool:
        seq = self._sequences.get(seq_id)
        if not seq:
            logger.warning(f"Sequence not found: {seq_id}")
            return False
        if not seq.enabled:
            logger.warning(f"Sequence disabled: {seq.name}")
            return False
        if seq.state == SequenceState.RUNNING:
            logger.info(f"Sequence already running: {seq.name}, restarting")
            self._cancel_run(seq)

        seq.state = SequenceState.RUNNING
        seq.current_step = 0
        seq.last_started = time.time()
        seq._pause_event.set()
        seq._task = asyncio.create_task(self._run(seq))
        logger.info(f"Started sequence: {seq.name}")
        return True

    async def stop_sequence(self, seq_id: str) -> bool:
        seq = self._sequences.get(seq_id)
        if not seq:
            return False
        if seq.state == SequenceState.STOPPED:
            return True
        self._cancel_run(seq)
        logger.info(f"Stopped sequence: {seq.name}")
        return True

    async def pause_sequence(self, seq_id: str) -> bool:
        seq = self._sequences.get(seq_id)
        if not seq or seq.state != SequenceState.RUNNING:
            return False
        seq.state = SequenceState.PAUSED
        seq._pause_event.clear()
        logger.info(f"Paused sequence: {seq.name} at step {seq.current_step}")
        return True

    async def resume_sequence(self, seq_id: str) -> bool:
        seq = self._sequences.get(seq_id)
        if not seq or seq.state != SequenceState.PAUSED:
            return False
        seq.state = SequenceState.RUNNING
        seq._pause_event.set()
        logger.info(f"Resumed sequence: {seq.name} at step {seq.current_step}")
        return True

    def _cancel_run(self, seq: Sequence) -> None:
        if seq._task and not seq._task.done():
            seq._task.cancel()
        seq._task = None
        seq.state = SequenceState.STOPPED
        seq._pause_event.set()  # unblock any paused wait

    async def _run(self, seq: Sequence) -> None:
        """Execute all steps in a sequence, optionally looping."""
        try:
            while True:
                for i, step in enumerate(seq.steps):
                    seq.current_step = i

                    # Wait if paused
                    await seq._pause_event.wait()

                    # Delay before this step
                    delay = step.effective_delay()
                    if delay > 0:
                        await asyncio.sleep(delay)

                    # Check still running after sleep
                    if seq.state == SequenceState.STOPPED:
                        return

                    # Wait if paused (could have been paused during sleep)
                    await seq._pause_event.wait()

                    # Execute the action
                    if self._action_executor:
                        try:
                            await self._action_executor(step.action)
                        except Exception as e:
                            logger.error(
                                f"Sequence {seq.name} step {i} ({step.action.type.value}) failed: {e}"
                            )

                seq.run_count += 1

                if not seq.loop:
                    break

        except asyncio.CancelledError:
            pass
        finally:
            seq.state = SequenceState.STOPPED
            seq._task = None
            logger.info(f"Sequence finished: {seq.name} (ran {seq.run_count} times)")

    async def stop_all(self) -> None:
        """Stop all running sequences."""
        for seq in self._sequences.values():
            if seq.state != SequenceState.STOPPED:
                self._cancel_run(seq)
