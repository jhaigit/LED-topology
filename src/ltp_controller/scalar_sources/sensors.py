"""Sensor implementations for scalar data sources."""

import logging
import time
from typing import Any

import numpy as np

from libltp import BooleanControl, EnumControl, NumberControl, ScalarFormat

from ltp_controller.scalar_sources.base import ScalarSource, ScalarSourceConfig

# Try to import psutil for system metrics
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None

logger = logging.getLogger(__name__)


class SystemMetricsSensor(ScalarSource):
    """Sensor that provides system metrics (CPU, memory, network, disk).

    Channels:
    - cpu_percent: Overall CPU usage (0-100)
    - cpu_cores[N]: Per-core CPU usage (0-100 each)
    - memory_percent: Memory usage (0-100)
    - memory_used_gb: Memory used in GB
    - memory_available_gb: Memory available in GB
    - disk_percent: Root disk usage (0-100)
    - net_bytes_sent_rate: Network bytes sent per second
    - net_bytes_recv_rate: Network bytes received per second

    Requires psutil. Falls back to simulated values if not available.
    """

    source_type = "system_metrics"

    def __init__(self, config: ScalarSourceConfig | None = None):
        # Get CPU count before parent init (needed for channel setup)
        if PSUTIL_AVAILABLE:
            self._cpu_count = psutil.cpu_count() or 4
        else:
            self._cpu_count = 4

        # Network tracking for rate calculation
        self._last_net_io = None
        self._last_net_time = 0.0

        super().__init__(config)

    def _setup_controls(self) -> None:
        """Set up sensor-specific controls."""
        self._controls.register(
            BooleanControl(
                id="include_cpu_cores",
                name="Include CPU Cores",
                description="Include per-core CPU usage",
                value=True,
                group="sensors",
            )
        )
        self._controls.register(
            BooleanControl(
                id="include_memory",
                name="Include Memory",
                description="Include memory metrics",
                value=True,
                group="sensors",
            )
        )
        self._controls.register(
            BooleanControl(
                id="include_disk",
                name="Include Disk",
                description="Include disk usage",
                value=True,
                group="sensors",
            )
        )
        self._controls.register(
            BooleanControl(
                id="include_network",
                name="Include Network",
                description="Include network I/O rates",
                value=True,
                group="sensors",
            )
        )

    def _setup_channels(self) -> None:
        """Define channel metadata."""
        # Overall CPU
        self._add_channel("cpu_percent", "CPU Usage", unit="%", min_val=0, max_val=100)

        # Per-core CPU (as array)
        self._add_channel_array(
            "cpu_cores",
            "CPU Cores",
            count=self._cpu_count,
            unit="%",
            min_val=0,
            max_val=100,
        )

        # Memory
        self._add_channel("memory_percent", "Memory Usage", unit="%", min_val=0, max_val=100)
        self._add_channel("memory_used_gb", "Memory Used", unit="GB", min_val=0, max_val=1000)
        self._add_channel("memory_available_gb", "Memory Available", unit="GB", min_val=0, max_val=1000)

        # Disk
        self._add_channel("disk_percent", "Disk Usage", unit="%", min_val=0, max_val=100)

        # Network (rates in bytes/sec, will be normalized)
        self._add_channel("net_bytes_sent_rate", "Network TX", unit="B/s", min_val=0)
        self._add_channel("net_bytes_recv_rate", "Network RX", unit="B/s", min_val=0)

    def sample(self) -> np.ndarray:
        """Collect current system metrics."""
        # Build array of values
        values = []

        if PSUTIL_AVAILABLE:
            # CPU overall
            cpu_percent = psutil.cpu_percent(interval=None)
            values.append(cpu_percent)

            # CPU per-core
            cpu_cores = psutil.cpu_percent(interval=None, percpu=True)
            # Pad or trim to expected count
            while len(cpu_cores) < self._cpu_count:
                cpu_cores.append(0.0)
            values.extend(cpu_cores[:self._cpu_count])

            # Memory
            mem = psutil.virtual_memory()
            values.append(mem.percent)
            values.append(mem.used / (1024 ** 3))  # GB
            values.append(mem.available / (1024 ** 3))  # GB

            # Disk
            try:
                disk = psutil.disk_usage("/")
                values.append(disk.percent)
            except Exception:
                values.append(0.0)

            # Network rates
            net_io = psutil.net_io_counters()
            current_time = time.time()

            if self._last_net_io is not None:
                dt = current_time - self._last_net_time
                if dt > 0:
                    tx_rate = (net_io.bytes_sent - self._last_net_io.bytes_sent) / dt
                    rx_rate = (net_io.bytes_recv - self._last_net_io.bytes_recv) / dt
                else:
                    tx_rate = 0.0
                    rx_rate = 0.0
            else:
                tx_rate = 0.0
                rx_rate = 0.0

            self._last_net_io = net_io
            self._last_net_time = current_time

            values.append(tx_rate)
            values.append(rx_rate)

        else:
            # Simulated values for testing without psutil
            t = time.time()
            values.append(30 + 20 * np.sin(t * 0.5))  # CPU overall
            for i in range(self._cpu_count):
                values.append(20 + 30 * np.sin(t * 0.3 + i))  # CPU cores
            values.append(45 + 10 * np.sin(t * 0.2))  # Memory %
            values.append(8.0 + np.sin(t * 0.1))  # Memory used GB
            values.append(8.0 - np.sin(t * 0.1))  # Memory available GB
            values.append(60 + 5 * np.sin(t * 0.05))  # Disk %
            values.append(1000000 * abs(np.sin(t * 0.7)))  # TX rate
            values.append(2000000 * abs(np.sin(t * 0.9)))  # RX rate

        return np.array(values, dtype=np.float32)


class EnvironmentSensor(ScalarSource):
    """Simulated environment sensor (temperature, humidity, light, motion).

    This is a mock sensor for testing and demonstration.
    In a real implementation, this would read from hardware sensors.

    Channels:
    - temperature: Temperature in Celsius
    - humidity: Relative humidity (0-100%)
    - light_level: Light level (0-100%)
    - motion: Motion detected (boolean as 0/1)
    """

    source_type = "environment"

    def __init__(self, config: ScalarSourceConfig | None = None):
        self._base_temp = 22.0
        self._base_humidity = 45.0
        super().__init__(config)

    def _setup_controls(self) -> None:
        """Set up sensor-specific controls."""
        self._controls.register(
            NumberControl(
                id="temp_offset",
                name="Temperature Offset",
                description="Offset to add to temperature reading",
                value=0.0,
                min=-10.0,
                max=10.0,
                step=0.1,
                unit="°C",
                group="calibration",
            )
        )
        self._controls.register(
            NumberControl(
                id="humidity_offset",
                name="Humidity Offset",
                description="Offset to add to humidity reading",
                value=0.0,
                min=-20.0,
                max=20.0,
                step=1.0,
                unit="%",
                group="calibration",
            )
        )
        self._controls.register(
            BooleanControl(
                id="simulate_motion",
                name="Simulate Motion",
                description="Generate random motion events",
                value=True,
                group="simulation",
            )
        )

    def _setup_channels(self) -> None:
        """Define channel metadata."""
        self._add_channel("temperature", "Temperature", unit="°C", min_val=-40, max_val=85)
        self._add_channel("humidity", "Humidity", unit="%", min_val=0, max_val=100)
        self._add_channel("light_level", "Light Level", unit="%", min_val=0, max_val=100)
        self._add_channel("motion", "Motion", type="boolean", min_val=0, max_val=1)

    def sample(self) -> np.ndarray:
        """Collect simulated environment data."""
        t = time.time()

        # Temperature with slow drift and small noise
        temp_offset = self.get_control("temp_offset")
        temperature = (
            self._base_temp
            + temp_offset
            + 2.0 * np.sin(t * 0.01)  # Slow drift
            + 0.1 * np.random.randn()  # Noise
        )

        # Humidity with inverse correlation to temperature
        humidity_offset = self.get_control("humidity_offset")
        humidity = (
            self._base_humidity
            + humidity_offset
            - 1.5 * np.sin(t * 0.01)  # Inverse of temp
            + 0.5 * np.random.randn()
        )
        humidity = np.clip(humidity, 0, 100)

        # Light level based on "time of day" simulation (faster for demo)
        # Cycle every ~60 seconds instead of 24 hours
        day_phase = (t % 60) / 60  # 0-1 over 60 seconds
        light_level = 50 + 45 * np.sin((day_phase - 0.25) * 2 * np.pi)
        light_level = np.clip(light_level, 0, 100)

        # Motion detection (random events if enabled)
        simulate_motion = self.get_control("simulate_motion")
        if simulate_motion:
            # Random motion events, more likely during "day"
            motion_prob = 0.1 if light_level > 30 else 0.02
            motion = 1.0 if np.random.random() < motion_prob else 0.0
        else:
            motion = 0.0

        return np.array([temperature, humidity, light_level, motion], dtype=np.float32)


class MultiZoneSensor(ScalarSource):
    """Simulated multi-zone temperature sensor.

    Demonstrates channel arrays with multiple similar sensors.

    Channel Arrays:
    - zone_temps[N]: Temperature readings from N zones
    """

    source_type = "multi_zone"

    def __init__(self, config: ScalarSourceConfig | None = None, zone_count: int = 4):
        self._zone_count = zone_count
        self._zone_base_temps = [20.0 + i * 2 for i in range(zone_count)]
        super().__init__(config)

    def _setup_controls(self) -> None:
        """Set up sensor-specific controls."""
        self._controls.register(
            NumberControl(
                id="zone_count",
                name="Zone Count",
                description="Number of temperature zones (read-only)",
                value=self._zone_count,
                min=1,
                max=16,
                step=1,
                readonly=True,
                group="sensors",
            )
        )
        self._controls.register(
            NumberControl(
                id="noise_level",
                name="Noise Level",
                description="Simulated sensor noise",
                value=0.5,
                min=0.0,
                max=2.0,
                step=0.1,
                unit="°C",
                group="simulation",
            )
        )

    def _setup_channels(self) -> None:
        """Define channel metadata."""
        self._add_channel_array(
            "zone_temps",
            "Zone Temperatures",
            count=self._zone_count,
            unit="°C",
            min_val=-40,
            max_val=85,
        )

    def sample(self) -> np.ndarray:
        """Collect simulated zone temperatures."""
        t = time.time()
        noise_level = self.get_control("noise_level")

        temps = []
        for i, base_temp in enumerate(self._zone_base_temps):
            # Each zone has slightly different drift pattern
            temp = (
                base_temp
                + 3.0 * np.sin(t * 0.02 + i * 0.5)  # Slow drift
                + noise_level * np.random.randn()  # Noise
            )
            temps.append(temp)

        return np.array(temps, dtype=np.float32)


class GPIOSensor(ScalarSource):
    """Simulated GPIO input sensor.

    Demonstrates boolean channel arrays for digital inputs.

    Channel Arrays:
    - gpio_inputs[N]: Digital input states (0 or 1)
    """

    source_type = "gpio_input"

    def __init__(self, config: ScalarSourceConfig | None = None, pin_count: int = 8):
        self._pin_count = pin_count
        self._pin_states = [False] * pin_count
        super().__init__(config)

        # Override format to boolean
        self._config.scalar_format = ScalarFormat.BOOLEAN

    def _setup_controls(self) -> None:
        """Set up sensor-specific controls."""
        self._controls.register(
            NumberControl(
                id="pin_count",
                name="Pin Count",
                description="Number of GPIO pins (read-only)",
                value=self._pin_count,
                min=1,
                max=32,
                step=1,
                readonly=True,
                group="sensors",
            )
        )
        self._controls.register(
            BooleanControl(
                id="simulate_changes",
                name="Simulate Changes",
                description="Randomly toggle pins for testing",
                value=True,
                group="simulation",
            )
        )
        self._controls.register(
            NumberControl(
                id="toggle_probability",
                name="Toggle Probability",
                description="Chance of pin toggle per sample",
                value=0.05,
                min=0.0,
                max=0.5,
                step=0.01,
                group="simulation",
            )
        )

    def _setup_channels(self) -> None:
        """Define channel metadata."""
        self._add_channel_array(
            "gpio_inputs",
            "GPIO Inputs",
            count=self._pin_count,
            type="boolean",
            min_val=0,
            max_val=1,
        )

    def sample(self) -> np.ndarray:
        """Collect GPIO input states."""
        simulate = self.get_control("simulate_changes")
        toggle_prob = self.get_control("toggle_probability")

        if simulate:
            # Randomly toggle some pins
            for i in range(self._pin_count):
                if np.random.random() < toggle_prob:
                    self._pin_states[i] = not self._pin_states[i]

        return np.array(self._pin_states, dtype=np.bool_)
