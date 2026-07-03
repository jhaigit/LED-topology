"""Rule engine for evaluating triggers and executing actions."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING, Any

from ltp_controller.controller import Controller
from ltp_controller.input_manager import InputEventManager
from ltp_controller.router import RoutingEngine
from ltp_controller.rules import (
    Action,
    ActionType,
    ComparisonOp,
    Rule,
    Trigger,
    TriggerType,
)
from ltp_controller.sink_control import SinkController
from ltp_controller.virtual_sources import VirtualSourceManager

if TYPE_CHECKING:
    from ltp_controller.sequence import SequenceManager

logger = logging.getLogger(__name__)


def _cron_field_matches(field_str: str, current: int) -> bool:
    """Check if a single cron field matches a value.

    Supports: * (any), N (exact), N,N (list), N-N (range), */N (step), N-N/N (range+step).
    """
    for part in field_str.split(","):
        step = 1
        if "/" in part:
            part, step_str = part.split("/", 1)
            step = int(step_str)

        if part == "*":
            if current % step == 0:
                return True
        elif "-" in part:
            lo, hi = part.split("-", 1)
            if int(lo) <= current <= int(hi) and (current - int(lo)) % step == 0:
                return True
        else:
            if current == int(part):
                return True
    return False


class RuleEngine:
    """Evaluates rules against input events and executes actions.

    The rule engine listens for input events from the InputEventManager,
    evaluates each enabled rule's trigger against the event, and executes
    the rule's actions when a trigger matches.
    """

    def __init__(
        self,
        controller: Controller,
        router: RoutingEngine,
        virtual_source_manager: VirtualSourceManager | None,
        input_manager: InputEventManager,
        sink_controller: SinkController | None = None,
        sequence_manager: SequenceManager | None = None,
    ):
        self.controller = controller
        self.router = router
        self.virtual_source_manager = virtual_source_manager
        self.input_manager = input_manager
        self.sink_controller = sink_controller
        self.sequence_manager = sequence_manager
        self._rules: dict[str, Rule] = {}
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._scheduler_task: asyncio.Task | None = None
        self._last_schedule_check: dict[str, float] = {}

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the event loop for async action execution."""
        self._event_loop = loop

    def start(self) -> None:
        """Start the rule engine by registering for input events."""
        self.input_manager.add_listener(self._on_input_event)
        if self._event_loop and self._event_loop.is_running():
            self._scheduler_task = asyncio.run_coroutine_threadsafe(
                self._start_scheduler(), self._event_loop
            )
        logger.info(f"Rule engine started with {len(self._rules)} rules")

    async def _start_scheduler(self) -> None:
        """Create the scheduler task inside the event loop."""
        self._scheduler_task = asyncio.create_task(self._schedule_loop())

    def stop(self) -> None:
        """Stop the rule engine."""
        self.input_manager.remove_listener(self._on_input_event)
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
        self._scheduler_task = None
        logger.info("Rule engine stopped")

    # ==================== CRUD Operations ====================

    def create_rule(
        self,
        name: str,
        trigger: Trigger,
        actions: list[Action],
        enabled: bool = True,
    ) -> Rule:
        """Create a new rule."""
        rule = Rule.create(name, trigger, actions, enabled)
        self._rules[rule.id] = rule
        logger.info(f"Created rule: {rule.name} (ID: {rule.id})")
        return rule

    def get_rule(self, rule_id: str) -> Rule | None:
        """Get a rule by ID."""
        return self._rules.get(rule_id)

    def update_rule(
        self,
        rule_id: str,
        name: str | None = None,
        trigger: Trigger | None = None,
        actions: list[Action] | None = None,
        enabled: bool | None = None,
    ) -> Rule | None:
        """Update an existing rule."""
        rule = self._rules.get(rule_id)
        if not rule:
            return None

        if name is not None:
            rule.name = name
        if trigger is not None:
            rule.trigger = trigger
        if actions is not None:
            rule.actions = actions
        if enabled is not None:
            rule.enabled = enabled

        logger.info(f"Updated rule: {rule.name} (ID: {rule.id})")
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        """Delete a rule."""
        rule = self._rules.pop(rule_id, None)
        if rule:
            logger.info(f"Deleted rule: {rule.name} (ID: {rule.id})")
            return True
        return False

    def enable_rule(self, rule_id: str) -> bool:
        """Enable a rule."""
        rule = self._rules.get(rule_id)
        if rule:
            rule.enabled = True
            logger.info(f"Enabled rule: {rule.name}")
            return True
        return False

    def disable_rule(self, rule_id: str) -> bool:
        """Disable a rule."""
        rule = self._rules.get(rule_id)
        if rule:
            rule.enabled = False
            logger.info(f"Disabled rule: {rule.name}")
            return True
        return False

    @property
    def rules(self) -> list[Rule]:
        """Get all rules."""
        return list(self._rules.values())

    # ==================== Persistence ====================

    def load_from_config(self, rules: list[dict[str, Any]]) -> None:
        """Load rules from configuration."""
        for rule_data in rules:
            try:
                rule = Rule.from_dict(rule_data)
                self._rules[rule.id] = rule
                logger.debug(f"Loaded rule: {rule.name}")
            except Exception as e:
                logger.error(f"Failed to load rule: {e}")

        logger.info(f"Loaded {len(self._rules)} rules from config")

    def to_config(self) -> list[dict[str, Any]]:
        """Export rules to configuration format."""
        return [rule.to_dict() for rule in self._rules.values()]

    # ==================== Event Handling ====================

    def _on_input_event(
        self,
        sink_id: str,
        input_id: int,
        name: str,
        input_type: str,
        old_value: Any,
        new_value: Any,
    ) -> None:
        """Handle an input event and evaluate all rules."""
        # Snapshot the rules: this runs on the input-manager thread while Flask
        # worker threads may create/delete rules, so iterating the live dict
        # would raise "dictionary changed size during iteration".
        for rule in list(self._rules.values()):
            if not rule.enabled:
                continue

            if self._matches_trigger(rule.trigger, sink_id, input_id, old_value, new_value):
                logger.info(f"Rule triggered: {rule.name} (sink={sink_id}, input={input_id})")
                rule.last_triggered = time.time()
                rule.trigger_count += 1

                # Execute actions asynchronously
                if self._event_loop and self._event_loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._execute_actions(rule),
                        self._event_loop,
                    )
                else:
                    # Try to get the current event loop
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self._execute_actions(rule),
                                loop,
                            )
                        else:
                            loop.run_until_complete(self._execute_actions(rule))
                    except RuntimeError:
                        logger.warning(f"No event loop available to execute rule: {rule.name}")

    def _matches_trigger(
        self,
        trigger: Trigger,
        sink_id: str,
        input_id: int,
        old_value: Any,
        new_value: Any,
    ) -> bool:
        """Check if an input event matches a trigger."""
        # Check sink and input match
        if trigger.sink_id != sink_id or trigger.input_id != input_id:
            return False

        # Evaluate based on trigger type
        if trigger.type == TriggerType.INPUT_CHANGE:
            # Edge trigger - check comparison against change
            return self._evaluate_change_comparison(
                trigger.comparison, trigger.value, old_value, new_value
            )
        elif trigger.type == TriggerType.INPUT_STATE:
            # Level trigger - check current value
            return self._evaluate_state_comparison(
                trigger.comparison, trigger.value, new_value
            )

        return False

    def _evaluate_change_comparison(
        self,
        comparison: ComparisonOp,
        expected: Any,
        old_value: Any,
        new_value: Any,
    ) -> bool:
        """Evaluate a comparison for change triggers."""
        if comparison == ComparisonOp.CHANGED_TO:
            # Value changed TO the expected value
            return new_value == expected and old_value != expected
        elif comparison == ComparisonOp.CHANGED_FROM:
            # Value changed FROM the expected value
            return old_value == expected and new_value != expected
        elif comparison == ComparisonOp.EQUALS:
            # New value equals expected (regardless of old value)
            return new_value == expected
        elif comparison == ComparisonOp.NOT_EQUALS:
            # New value doesn't equal expected
            return new_value != expected
        return False

    def _evaluate_state_comparison(
        self,
        comparison: ComparisonOp,
        expected: Any,
        current_value: Any,
    ) -> bool:
        """Evaluate a comparison for state triggers."""
        if comparison in (ComparisonOp.EQUALS, ComparisonOp.CHANGED_TO):
            return current_value == expected
        elif comparison in (ComparisonOp.NOT_EQUALS, ComparisonOp.CHANGED_FROM):
            return current_value != expected
        return False

    # ==================== Schedule Evaluation ====================

    async def _schedule_loop(self) -> None:
        """Check schedule triggers once per minute.

        This loop must never die except on cancellation: a single transient
        error (including a concurrent rule mutation from a Flask thread) would
        otherwise permanently stop all schedule-triggered rules with no
        recovery. Each tick is fully guarded, and rules are snapshotted before
        iteration to avoid "dictionary changed size during iteration".
        """
        while True:
            try:
                await asyncio.sleep(30)
                now = time.localtime()
                for rule in list(self._rules.values()):
                    if not rule.enabled or rule.trigger.type != TriggerType.SCHEDULE:
                        continue
                    try:
                        await self._evaluate_schedule(rule, now)
                    except Exception as e:
                        logger.error(f"Schedule eval error for {rule.name}: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Schedule loop error (continuing): {e}")

    async def _evaluate_schedule(self, rule: Rule, now: time.struct_time) -> None:
        """Check if a schedule trigger should fire right now."""
        trigger = rule.trigger

        # Day filter
        if trigger.days:
            day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            today = day_names[now.tm_wday]
            if today not in [d.lower()[:3] for d in trigger.days]:
                return

        # Parse and match cron expression
        if not self._cron_matches(trigger.cron, now):
            return

        # Deduplicate: don't fire the same rule more than once per cron minute.
        # Use the minute timestamp (floored) as the key.
        minute_key = time.mktime(now) // 60
        last = self._last_schedule_check.get(rule.id, 0)
        if last >= minute_key:
            return
        self._last_schedule_check[rule.id] = minute_key

        # Apply jitter: delay execution by a random amount
        jitter_secs = random.uniform(0, trigger.jitter_minutes * 60) if trigger.jitter_minutes > 0 else 0
        if jitter_secs > 0:
            logger.info(f"Schedule rule {rule.name} matched, firing in {jitter_secs:.0f}s (jitter)")
            await asyncio.sleep(jitter_secs)
        else:
            logger.info(f"Schedule rule {rule.name} matched, firing now")

        rule.last_triggered = time.time()
        rule.trigger_count += 1
        await self._execute_actions(rule)

    @staticmethod
    def _cron_matches(expr: str, now: time.struct_time) -> bool:
        """Match a 5-field cron expression against a time.

        Fields: minute hour day-of-month month day-of-week
        Supports: *, specific values, comma-separated, ranges (1-5), steps (*/10).
        Day-of-week: 0=Mon..6=Sun (also accepts 7=Sun).
        """
        parts = expr.strip().split()
        if len(parts) != 5:
            return False

        values = [now.tm_min, now.tm_hour, now.tm_mday, now.tm_mon, now.tm_wday]

        for field_str, current in zip(parts, values):
            if not _cron_field_matches(field_str, current):
                return False
        return True

    # ==================== Action Execution ====================

    async def _execute_actions(self, rule: Rule) -> None:
        """Execute all actions for a rule."""
        for action in rule.actions:
            try:
                await self._execute_action(action)
            except Exception as e:
                logger.error(f"Error executing action {action.type.value}: {e}")

    async def _execute_action(self, action: Action) -> None:
        """Execute a single action."""
        logger.debug(f"Executing action: {action.type.value} on {action.target_id}")

        if action.type == ActionType.SET_CONTROL:
            await self._action_set_control(action)
        elif action.type == ActionType.ENABLE_ROUTE:
            await self._action_enable_route(action)
        elif action.type == ActionType.DISABLE_ROUTE:
            await self._action_disable_route(action)
        elif action.type == ActionType.ENABLE_SOURCE:
            await self._action_enable_source(action)
        elif action.type == ActionType.DISABLE_SOURCE:
            await self._action_disable_source(action)
        elif action.type == ActionType.SET_PIXEL:
            await self._action_set_pixel(action)
        elif action.type == ActionType.FILL_SOLID:
            await self._action_fill_solid(action)
        elif action.type == ActionType.CLEAR:
            await self._action_clear(action)
        elif action.type == ActionType.START_SEQUENCE:
            await self._action_start_sequence(action)
        elif action.type == ActionType.STOP_SEQUENCE:
            await self._action_stop_sequence(action)
        else:
            logger.warning(f"Unknown action type: {action.type}")

    async def _action_enable_route(self, action: Action) -> None:
        """Enable a route."""
        success = await self.router.enable_route(action.target_id)
        if success:
            logger.info(f"Enabled route: {action.target_id}")
        else:
            logger.warning(f"Failed to enable route: {action.target_id}")

    async def _action_disable_route(self, action: Action) -> None:
        """Disable a route."""
        success = await self.router.disable_route(action.target_id)
        if success:
            logger.info(f"Disabled route: {action.target_id}")
        else:
            logger.warning(f"Failed to disable route: {action.target_id}")

    async def _action_set_control(self, action: Action) -> None:
        """Set a control value on a sink."""
        if not action.control_id:
            logger.warning("SET_CONTROL action missing control_id")
            return

        sink = self.controller.get_sink(action.target_id)
        if not sink:
            logger.warning(f"Sink not found for SET_CONTROL: {action.target_id}")
            return

        success = await self.controller.set_device_control(
            sink, action.control_id, action.value
        )
        if success:
            logger.info(f"Set {action.control_id}={action.value} on sink {sink.name}")
        else:
            logger.warning(f"Failed to set control on sink {sink.name}")

    async def _action_enable_source(self, action: Action) -> None:
        """Enable a virtual source."""
        if not self.virtual_source_manager:
            logger.warning("Virtual source manager not available")
            return

        source = self.virtual_source_manager.get(action.target_id)
        if not source:
            logger.warning(f"Virtual source not found: {action.target_id}")
            return

        source.start()
        logger.info(f"Enabled virtual source: {source.name}")

    async def _action_disable_source(self, action: Action) -> None:
        """Disable a virtual source."""
        if not self.virtual_source_manager:
            logger.warning("Virtual source manager not available")
            return

        source = self.virtual_source_manager.get(action.target_id)
        if not source:
            logger.warning(f"Virtual source not found: {action.target_id}")
            return

        source.stop()
        logger.info(f"Disabled virtual source: {source.name}")

    @staticmethod
    def _parse_color(value: str | list | tuple) -> tuple[int, int, int]:
        """Parse a color from hex string or RGB list."""
        if isinstance(value, str) and value.startswith("#"):
            h = value.lstrip("#")
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return (int(value[0]), int(value[1]), int(value[2]))
        return (255, 255, 255)

    async def _action_set_pixel(self, action: Action) -> None:
        """Set a single pixel on a sink, preserving other pixels."""
        if not self.sink_controller:
            logger.warning("Sink controller not available for SET_PIXEL")
            return

        if not isinstance(action.value, dict):
            logger.warning("SET_PIXEL action requires value dict with index and color")
            return

        index = int(action.value.get("index", 0))
        color = list(self._parse_color(action.value.get("color", "#ffffff")))
        result = await self.sink_controller.paint_pixels(
            action.target_id,
            {"index": index, "color": color},
        )
        if result.get("status") == "ok":
            logger.info(f"Set pixel {index} to {color} on {action.target_id}")
        else:
            logger.warning(f"Failed to set pixel: {result.get('message')}")

    async def _action_fill_solid(self, action: Action) -> None:
        """Fill a sink with a solid color."""
        if not self.sink_controller:
            logger.warning("Sink controller not available for FILL_SOLID")
            return

        if isinstance(action.value, dict):
            color = self._parse_color(action.value.get("color", "#ffffff"))
        else:
            color = self._parse_color(action.value)
        result = await self.sink_controller.fill_solid(action.target_id, color)
        if result.get("status") == "ok":
            logger.info(f"Filled {action.target_id} with {color}")
        else:
            logger.warning(f"Failed to fill: {result.get('message')}")

    async def _action_clear(self, action: Action) -> None:
        """Clear a sink (fill black)."""
        if not self.sink_controller:
            logger.warning("Sink controller not available for CLEAR")
            return

        result = await self.sink_controller.clear(action.target_id)
        if result.get("status") == "ok":
            logger.info(f"Cleared {action.target_id}")
        else:
            logger.warning(f"Failed to clear: {result.get('message')}")

    async def _action_start_sequence(self, action: Action) -> None:
        """Start a sequence."""
        if not self.sequence_manager:
            logger.warning("Sequence manager not available")
            return
        seq = self.sequence_manager.get(action.target_id) or self.sequence_manager.get_by_name(action.target_id)
        if not seq:
            logger.warning(f"Sequence not found: {action.target_id}")
            return
        await self.sequence_manager.start_sequence(seq.id)

    async def _action_stop_sequence(self, action: Action) -> None:
        """Stop a sequence."""
        if not self.sequence_manager:
            logger.warning("Sequence manager not available")
            return
        seq = self.sequence_manager.get(action.target_id) or self.sequence_manager.get_by_name(action.target_id)
        if not seq:
            logger.warning(f"Sequence not found: {action.target_id}")
            return
        await self.sequence_manager.stop_sequence(seq.id)
