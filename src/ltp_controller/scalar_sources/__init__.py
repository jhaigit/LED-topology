"""Scalar data sources (sensors) for LTP Controller."""

from ltp_controller.scalar_sources.base import (
    ScalarSource,
    ScalarSourceConfig,
    ScalarSourceManager,
)
from ltp_controller.scalar_sources.sensors import (
    EnvironmentSensor,
    GPIOSensor,
    MultiZoneSensor,
    SystemMetricsSensor,
)

__all__ = [
    # Base classes
    "ScalarSource",
    "ScalarSourceConfig",
    "ScalarSourceManager",
    # Sensor implementations
    "EnvironmentSensor",
    "GPIOSensor",
    "MultiZoneSensor",
    "SystemMetricsSensor",
]

# Registry of available scalar source types
SCALAR_SOURCE_TYPES: dict[str, type[ScalarSource]] = {
    "system_metrics": SystemMetricsSensor,
    "environment": EnvironmentSensor,
    "multi_zone": MultiZoneSensor,
    "gpio_input": GPIOSensor,
}
