"""Thermal camera source - AMG8833 Pattern and Source setup."""

import logging
from typing import Any

import numpy as np

from ltp_source.patterns.base import Pattern, PatternRegistry
from ltp_source.source import Source, SourceConfig
from ltp_thermal_source.amg8833 import AMG8833
from ltp_thermal_source.palettes import PALETTES, apply_palette

logger = logging.getLogger(__name__)


class ThermalParams:
    """Stored as plain attributes on the pattern (not using PatternParams to
    avoid pulling pydantic into the sensor read path)."""


@PatternRegistry.register
class ThermalPattern(Pattern):
    """Pattern that reads an AMG8833 thermal sensor and renders a heatmap."""

    name = "thermal"
    description = "AMG8833 infrared thermal camera"

    def __init__(self, params: dict[str, Any] | None = None):
        super().__init__(params)
        p = params or {}

        # Sensor
        self._sensor: AMG8833 | None = None
        self._bus = p.get("bus", 1)
        self._address = p.get("address", 0x69)

        # Palette
        self._palette: str = p.get("palette", "ironbow")

        # Range
        self._auto_range: bool = p.get("auto_range", True)
        self._range_min: float = p.get("range_min", 20.0)
        self._range_max: float = p.get("range_max", 35.0)

        # Auto-range tracking (exponential decay)
        self._tracked_min: float = 100.0
        self._tracked_max: float = -40.0
        self._decay = 0.995  # per-frame decay toward new extremes

        # Cached pixel data for when sensor read fails
        self._last_temps: list[float] | None = None

    def _ensure_sensor(self) -> bool:
        """Open sensor if not already open. Returns True if sensor is ready."""
        if self._sensor is not None:
            return True
        try:
            self._sensor = AMG8833(bus=self._bus, address=self._address)
            return True
        except Exception as e:
            logger.error(f"Failed to open AMG8833: {e}")
            return False

    def render(self, buffer: np.ndarray) -> None:
        """Read thermal sensor and write heatmap to buffer.

        Buffer is expected to be shape (8, 8, 3) for 2D or (64, 3) for 1D.
        """
        # Read sensor
        temps = None
        if self._ensure_sensor():
            try:
                temps = self._sensor.read_pixels()
                self._last_temps = temps
            except Exception as e:
                logger.warning(f"Sensor read failed: {e}")
                temps = self._last_temps

        if temps is None:
            # No data yet - fill black
            buffer[:] = 0
            return

        temps_arr = np.array(temps, dtype=np.float32)

        # Determine range
        if self._auto_range:
            scene_min = float(temps_arr.min())
            scene_max = float(temps_arr.max())
            # Exponential decay tracking
            self._tracked_min = min(self._tracked_min, scene_min) * self._decay + scene_min * (1 - self._decay)
            self._tracked_max = max(self._tracked_max, scene_max) * self._decay + scene_max * (1 - self._decay)
            t_min = self._tracked_min
            t_max = self._tracked_max
        else:
            t_min = self._range_min
            t_max = self._range_max

        # Normalize to 0-1
        if t_max - t_min < 0.5:
            t_max = t_min + 0.5  # avoid division by zero / degenerate range
        normalized = np.clip((temps_arr - t_min) / (t_max - t_min), 0.0, 1.0)

        # Apply palette
        rgb = apply_palette(normalized, self._palette)

        # Write to buffer
        if buffer.ndim == 3:
            # 2D buffer (height, width, channels) - reshape 64 pixels to 8x8
            buffer[:] = rgb.reshape(8, 8, 3)
        else:
            # 1D buffer (pixels, channels)
            buffer[:] = rgb

    def get_controls(self) -> list[dict[str, Any]]:
        """Return controls for the thermal pattern."""
        return [
            {
                "id": "palette",
                "name": "Palette",
                "description": "Thermal color palette",
                "type": "enum",
                "value": self._palette,
                "options": [
                    {"value": name, "label": name.capitalize(), "description": ""}
                    for name in PALETTES
                ],
                "group": "pattern",
            },
            {
                "id": "auto_range",
                "name": "Auto Range",
                "description": "Automatically adapt to scene temperature range",
                "type": "boolean",
                "value": self._auto_range,
                "group": "pattern",
            },
            {
                "id": "range_min",
                "name": "Range Min",
                "description": "Fixed range minimum temperature (°C)",
                "type": "number",
                "value": self._range_min,
                "min": -20.0,
                "max": 100.0,
                "step": 1.0,
                "group": "pattern",
            },
            {
                "id": "range_max",
                "name": "Range Max",
                "description": "Fixed range maximum temperature (°C)",
                "type": "number",
                "value": self._range_max,
                "min": -20.0,
                "max": 200.0,
                "step": 1.0,
                "group": "pattern",
            },
        ]

    def set_param(self, name: str, value: Any) -> None:
        """Update a parameter value."""
        if name == "palette" and value in PALETTES:
            self._palette = value
            self._reset_range()
        elif name == "auto_range":
            self._auto_range = bool(value)
        elif name == "range_min":
            self._range_min = float(value)
        elif name == "range_max":
            self._range_max = float(value)

    def _reset_range(self) -> None:
        """Reset auto-range tracking."""
        self._tracked_min = 100.0
        self._tracked_max = -40.0

    def close(self) -> None:
        """Close the sensor."""
        if self._sensor:
            self._sensor.close()
            self._sensor = None


def create_thermal_source(
    name: str = "AMG8833 Thermal",
    bus: int = 1,
    address: int = 0x69,
    rate: int = 10,
    palette: str = "ironbow",
    range_min: float | None = None,
    range_max: float | None = None,
    control_port: int = 0,
) -> Source:
    """Create a Source configured for the AMG8833 thermal camera.

    Returns a standard ltp_source.Source with the 'thermal' pattern registered.
    """
    pattern_params: dict[str, Any] = {
        "bus": bus,
        "address": address,
        "palette": palette,
    }
    if range_min is not None and range_max is not None:
        pattern_params["auto_range"] = False
        pattern_params["range_min"] = range_min
        pattern_params["range_max"] = range_max

    config = SourceConfig(
        name=name,
        description="AMG8833 8x8 infrared thermal camera",
        dimensions=[8, 8],
        rate=rate,
        pattern="thermal",
        pattern_params=pattern_params,
        control_port=control_port,
    )

    return Source(config)
