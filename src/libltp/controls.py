"""Control definitions and validation for LTP devices."""

import re
from abc import ABC, abstractmethod
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator


class ControlValidationError(Exception):
    """Error validating a control value."""

    def __init__(self, control_id: str, message: str):
        self.control_id = control_id
        self.message = message
        super().__init__(f"Control '{control_id}': {message}")


class EnumOption(BaseModel):
    """An option for enum controls."""

    value: str
    label: str
    description: str = ""


class ControlBase(BaseModel, ABC):
    """Base class for all control types."""

    id: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    name: str
    description: str
    readonly: bool = False
    group: str = "general"

    @property
    @abstractmethod
    def type(self) -> str:
        """Return the control type string."""
        ...

    @abstractmethod
    def validate_value(self, value: Any) -> Any:
        """Validate and potentially coerce a value. Returns the validated value."""
        ...

    def to_dict(self) -> dict[str, Any]:
        """Convert control definition to dictionary for protocol transmission."""
        data = self.model_dump(exclude_none=True)
        data["type"] = self.type
        return data


class BooleanControl(ControlBase):
    """Boolean toggle control."""

    value: bool = False

    @property
    def type(self) -> str:
        return "boolean"

    def validate_value(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.lower() in ("true", "1", "yes", "on"):
                return True
            if value.lower() in ("false", "0", "no", "off"):
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        raise ControlValidationError(self.id, f"Cannot convert {type(value).__name__} to boolean")


class NumberControl(ControlBase):
    """Numeric control with optional range."""

    value: float = 0.0
    min: float | None = None
    max: float | None = None
    step: float = 1.0
    unit: str = ""

    @property
    def type(self) -> str:
        return "number"

    def validate_value(self, value: Any) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError) as e:
            raise ControlValidationError(
                self.id, f"Cannot convert {type(value).__name__} to number"
            ) from e

        if self.min is not None and num < self.min:
            raise ControlValidationError(
                self.id, f"Value {num} is below minimum {self.min}"
            )
        if self.max is not None and num > self.max:
            raise ControlValidationError(
                self.id, f"Value {num} exceeds maximum {self.max}"
            )
        return num


class StringControl(ControlBase):
    """Free-form string control."""

    value: str = ""
    min_length: int | None = Field(default=None, alias="minLength")
    max_length: int | None = Field(default=None, alias="maxLength")
    pattern: str | None = None

    @property
    def type(self) -> str:
        return "string"

    def validate_value(self, value: Any) -> str:
        s = str(value)

        if self.min_length is not None and len(s) < self.min_length:
            raise ControlValidationError(
                self.id, f"String length {len(s)} is below minimum {self.min_length}"
            )
        if self.max_length is not None and len(s) > self.max_length:
            raise ControlValidationError(
                self.id, f"String length {len(s)} exceeds maximum {self.max_length}"
            )
        if self.pattern is not None and not re.match(self.pattern, s):
            raise ControlValidationError(
                self.id, f"String does not match pattern '{self.pattern}'"
            )
        return s


class EnumControl(ControlBase):
    """Selection from predefined options."""

    value: str
    options: list[EnumOption]

    @property
    def type(self) -> str:
        return "enum"

    def validate_value(self, value: Any) -> str:
        s = str(value)
        valid_values = {opt.value for opt in self.options}
        if s not in valid_values:
            raise ControlValidationError(
                self.id, f"Value '{s}' not in allowed options: {valid_values}"
            )
        return s


class ColorControl(ControlBase):
    """RGB or RGBA color control."""

    value: str = "#000000"
    alpha: bool = False

    @property
    def type(self) -> str:
        return "color"

    @field_validator("value")
    @classmethod
    def validate_color_format(cls, v: str) -> str:
        """Validate color string format."""
        pattern = r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"
        if not re.match(pattern, v):
            raise ValueError(f"Invalid color format: {v}")
        return v.upper()

    def validate_value(self, value: Any) -> str:
        s = str(value).upper()
        if self.alpha:
            pattern = r"^#[0-9A-Fa-f]{8}$"
            if not re.match(pattern, s):
                # Allow 6-char colors, add FF alpha
                if re.match(r"^#[0-9A-Fa-f]{6}$", s):
                    return s + "FF"
                raise ControlValidationError(
                    self.id, f"Invalid RGBA color format: {value}"
                )
        else:
            pattern = r"^#[0-9A-Fa-f]{6}$"
            if not re.match(pattern, s):
                raise ControlValidationError(
                    self.id, f"Invalid RGB color format: {value}"
                )
        return s


class ActionControl(ControlBase):
    """Trigger action control (button)."""

    confirm: bool = False

    # Actions don't have persistent values
    @property
    def value(self) -> None:
        return None

    @property
    def type(self) -> str:
        return "action"

    def validate_value(self, value: Any) -> bool:
        # Actions just need a truthy trigger
        return bool(value)

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        # Remove 'value' from actions
        data.pop("value", None)
        return data


class ArrayItemType(BaseModel):
    """Type specification for array items."""

    type: str
    min: float | None = None
    max: float | None = None


class ArrayControl(ControlBase):
    """Array of values control."""

    value: list[Any] = Field(default_factory=list)
    items: ArrayItemType
    min_items: int | None = Field(default=None, alias="minItems")
    max_items: int | None = Field(default=None, alias="maxItems")

    @property
    def type(self) -> str:
        return "array"

    def validate_value(self, value: Any) -> list[Any]:
        if not isinstance(value, list):
            raise ControlValidationError(self.id, f"Expected list, got {type(value).__name__}")

        if self.min_items is not None and len(value) < self.min_items:
            raise ControlValidationError(
                self.id, f"Array length {len(value)} is below minimum {self.min_items}"
            )
        if self.max_items is not None and len(value) > self.max_items:
            raise ControlValidationError(
                self.id, f"Array length {len(value)} exceeds maximum {self.max_items}"
            )

        # Validate individual items based on item type
        validated = []
        for i, item in enumerate(value):
            try:
                if self.items.type == "number":
                    num = float(item)
                    if self.items.min is not None and num < self.items.min:
                        raise ControlValidationError(
                            self.id, f"Item {i} value {num} below minimum {self.items.min}"
                        )
                    if self.items.max is not None and num > self.items.max:
                        raise ControlValidationError(
                            self.id, f"Item {i} value {num} exceeds maximum {self.items.max}"
                        )
                    validated.append(num)
                elif self.items.type == "string":
                    validated.append(str(item))
                elif self.items.type == "boolean":
                    validated.append(bool(item))
                else:
                    validated.append(item)
            except (TypeError, ValueError) as e:
                raise ControlValidationError(
                    self.id, f"Invalid item at index {i}: {e}"
                ) from e

        return validated


# Type alias for any control
Control = (
    BooleanControl
    | NumberControl
    | StringControl
    | EnumControl
    | ColorControl
    | ActionControl
    | ArrayControl
)


class ControlRegistry:
    """Registry of controls for a device."""

    def __init__(self) -> None:
        self._controls: dict[str, Control] = {}
        self._callbacks: dict[str, list[Callable[[str, Any, Any], None]]] = {}

    def register(self, control: Control) -> None:
        """Register a control."""
        self._controls[control.id] = control

    def unregister(self, control_id: str) -> None:
        """Unregister a control."""
        self._controls.pop(control_id, None)
        self._callbacks.pop(control_id, None)

    def get(self, control_id: str) -> Control | None:
        """Get a control by ID."""
        return self._controls.get(control_id)

    def get_all(self) -> list[Control]:
        """Get all registered controls."""
        return list(self._controls.values())

    def get_value(self, control_id: str) -> Any:
        """Get the current value of a control."""
        control = self._controls.get(control_id)
        if control is None:
            raise KeyError(f"Unknown control: {control_id}")
        return control.value

    def get_values(self, control_ids: list[str] | None = None) -> dict[str, Any]:
        """Get values for multiple controls."""
        if control_ids is None:
            control_ids = list(self._controls.keys())

        return {
            cid: self._controls[cid].value
            for cid in control_ids
            if cid in self._controls
        }

    def set_value(self, control_id: str, value: Any) -> Any:
        """Set the value of a control. Returns the validated value."""
        control = self._controls.get(control_id)
        if control is None:
            raise KeyError(f"Unknown control: {control_id}")

        if control.readonly:
            raise ControlValidationError(control_id, "Control is read-only")

        old_value = control.value
        validated = control.validate_value(value)

        # Update the control's value using model assignment
        # We need to handle this carefully since controls are immutable models
        # Create a new control with the updated value
        control_dict = control.model_dump()
        control_dict["value"] = validated
        new_control = type(control)(**control_dict)
        self._controls[control_id] = new_control

        # Notify callbacks
        if control_id in self._callbacks:
            for callback in self._callbacks[control_id]:
                callback(control_id, old_value, validated)

        return validated

    def set_values(
        self, values: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        """Set multiple control values.

        Returns:
            Tuple of (applied_values, errors)
        """
        applied: dict[str, Any] = {}
        errors: dict[str, dict[str, Any]] = {}

        for control_id, value in values.items():
            try:
                applied[control_id] = self.set_value(control_id, value)
            except KeyError:
                errors[control_id] = {"code": 4, "message": f"Unknown control: {control_id}"}
            except ControlValidationError as e:
                errors[control_id] = {"code": 6, "message": e.message}

        return applied, errors

    def on_change(
        self, control_id: str, callback: Callable[[str, Any, Any], None]
    ) -> None:
        """Register a callback for control value changes.

        Callback signature: (control_id, old_value, new_value) -> None
        """
        if control_id not in self._callbacks:
            self._callbacks[control_id] = []
        self._callbacks[control_id].append(callback)

    def to_list(self) -> list[dict[str, Any]]:
        """Export all controls as a list of dictionaries."""
        return [control.to_dict() for control in self._controls.values()]

    def groups(self) -> dict[str, list[Control]]:
        """Get controls organized by group."""
        result: dict[str, list[Control]] = {}
        for control in self._controls.values():
            if control.group not in result:
                result[control.group] = []
            result[control.group].append(control)
        return result


def control_from_dict(data: dict[str, Any]) -> Control:
    """Create a control from a dictionary specification."""
    control_type = data.get("type")

    type_map: dict[str, type[Control]] = {
        "boolean": BooleanControl,
        "number": NumberControl,
        "string": StringControl,
        "enum": EnumControl,
        "color": ColorControl,
        "action": ActionControl,
        "array": ArrayControl,
    }

    if control_type not in type_map:
        raise ValueError(f"Unknown control type: {control_type}")

    # Remove 'type' from data before passing to model
    data = {k: v for k, v in data.items() if k != "type"}

    return type_map[control_type](**data)


def controls_from_list(data: list[dict[str, Any]]) -> list[Control]:
    """Create controls from a list of dictionaries."""
    return [control_from_dict(item) for item in data]
