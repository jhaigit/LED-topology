"""System monitoring virtual sources."""

import time
from typing import Any

import numpy as np

from libltp import NumberControl, BooleanControl, EnumControl, ColorControl

from ltp_controller.virtual_sources.base import VirtualSource, VirtualSourceConfig

# Try to import psutil, gracefully degrade if not available
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


class SystemMonitor(VirtualSource):
    """Displays system metrics (CPU, memory, network) as bar graphs.

    Each metric is shown as a horizontal bar graph section on the LED strip.
    The strip is divided into sections based on which metrics are enabled.

    Requires psutil for real metrics. Falls back to simulated data if unavailable.
    """

    source_type = "system_monitor"

    def __init__(self, config: VirtualSourceConfig | None = None):
        super().__init__(config)
        self._cpu_value = 0.0
        self._memory_value = 0.0
        self._network_rx_value = 0.0
        self._network_tx_value = 0.0
        self._last_net_io = None
        self._last_net_time = 0.0
        self._max_net_speed = 1_000_000  # 1 MB/s default, auto-scales
        self._update_interval = 0.5  # Update metrics every 0.5 seconds
        self._last_update = 0.0

    def _setup_controls(self) -> None:
        # Metric selection
        self._controls.register(
            BooleanControl(
                id="show_cpu",
                name="Show CPU",
                description="Display CPU usage",
                value=True,
                group="metrics",
            )
        )
        self._controls.register(
            BooleanControl(
                id="show_memory",
                name="Show Memory",
                description="Display memory usage",
                value=True,
                group="metrics",
            )
        )
        self._controls.register(
            BooleanControl(
                id="show_network",
                name="Show Network",
                description="Display network I/O (RX/TX)",
                value=True,
                group="metrics",
            )
        )

        # Update rate
        self._controls.register(
            NumberControl(
                id="update_rate",
                name="Update Rate",
                description="How often to sample metrics (seconds)",
                value=0.5,
                min=0.1,
                max=5.0,
                step=0.1,
                unit="s",
                group="metrics",
            )
        )

        # Display style
        self._controls.register(
            EnumControl(
                id="layout",
                name="Layout",
                description="How to arrange metrics on the strip",
                value="side_by_side",
                options=[
                    {"value": "side_by_side", "label": "Side by Side"},
                    {"value": "stacked", "label": "Stacked (overlay)"},
                ],
                group="display",
            )
        )
        self._controls.register(
            NumberControl(
                id="bar_gap",
                name="Bar Gap",
                description="Pixels between metric bars",
                value=2,
                min=0,
                max=10,
                step=1,
                group="display",
            )
        )
        self._controls.register(
            BooleanControl(
                id="show_labels",
                name="Show Labels",
                description="Use first pixel as color-coded label",
                value=True,
                group="display",
            )
        )

        # Colors
        self._controls.register(
            ColorControl(
                id="cpu_color",
                name="CPU Color",
                description="Color for CPU usage bar",
                value="#00FF00",  # Green
                group="colors",
            )
        )
        self._controls.register(
            ColorControl(
                id="memory_color",
                name="Memory Color",
                description="Color for memory usage bar",
                value="#0088FF",  # Blue
                group="colors",
            )
        )
        self._controls.register(
            ColorControl(
                id="network_rx_color",
                name="Network RX Color",
                description="Color for network receive bar",
                value="#FF8800",  # Orange
                group="colors",
            )
        )
        self._controls.register(
            ColorControl(
                id="network_tx_color",
                name="Network TX Color",
                description="Color for network transmit bar",
                value="#FF00FF",  # Magenta
                group="colors",
            )
        )
        self._controls.register(
            ColorControl(
                id="background",
                name="Background",
                description="Background color",
                value="#000000",
                group="colors",
            )
        )

        # Threshold coloring
        self._controls.register(
            BooleanControl(
                id="threshold_colors",
                name="Threshold Colors",
                description="Change color based on value (green/yellow/red)",
                value=False,
                group="colors",
            )
        )
        self._controls.register(
            NumberControl(
                id="warning_threshold",
                name="Warning Threshold",
                description="Value above which to show yellow",
                value=0.7,
                min=0.3,
                max=0.9,
                step=0.05,
                group="colors",
            )
        )
        self._controls.register(
            NumberControl(
                id="critical_threshold",
                name="Critical Threshold",
                description="Value above which to show red",
                value=0.9,
                min=0.5,
                max=1.0,
                step=0.05,
                group="colors",
            )
        )

    def _update_metrics(self) -> None:
        """Update system metrics from psutil."""
        current_time = time.time()

        # Rate limit updates
        if current_time - self._last_update < self._update_interval:
            return
        self._last_update = current_time
        self._update_interval = self.get_control("update_rate")

        if not PSUTIL_AVAILABLE:
            # Simulate metrics for testing when psutil is not available
            self._cpu_value = 0.3 + 0.2 * np.sin(current_time * 0.5)
            self._memory_value = 0.5 + 0.1 * np.sin(current_time * 0.3)
            self._network_rx_value = 0.2 + 0.3 * abs(np.sin(current_time * 0.7))
            self._network_tx_value = 0.1 + 0.2 * abs(np.sin(current_time * 0.9))
            return

        # CPU usage (0-100 -> 0-1)
        self._cpu_value = psutil.cpu_percent(interval=None) / 100.0

        # Memory usage (0-100 -> 0-1)
        mem = psutil.virtual_memory()
        self._memory_value = mem.percent / 100.0

        # Network I/O (bytes/sec, normalized)
        net_io = psutil.net_io_counters()
        if self._last_net_io is not None:
            dt = current_time - self._last_net_time
            if dt > 0:
                rx_speed = (net_io.bytes_recv - self._last_net_io.bytes_recv) / dt
                tx_speed = (net_io.bytes_sent - self._last_net_io.bytes_sent) / dt

                # Auto-scale based on observed max speed
                max_speed = max(rx_speed, tx_speed, self._max_net_speed)
                if max_speed > self._max_net_speed:
                    self._max_net_speed = max_speed

                self._network_rx_value = min(1.0, rx_speed / self._max_net_speed)
                self._network_tx_value = min(1.0, tx_speed / self._max_net_speed)

        self._last_net_io = net_io
        self._last_net_time = current_time

    def _get_threshold_color(self, value: float, base_color: tuple[int, int, int]) -> tuple[int, int, int]:
        """Get color based on threshold settings."""
        if not self.get_control("threshold_colors"):
            return base_color

        warning = self.get_control("warning_threshold")
        critical = self.get_control("critical_threshold")

        if value >= critical:
            return (255, 0, 0)  # Red
        elif value >= warning:
            return (255, 255, 0)  # Yellow
        else:
            return (0, 255, 0)  # Green

    def _render_bar(
        self,
        pixels: np.ndarray,
        start: int,
        length: int,
        value: float,
        color: tuple[int, int, int],
        background: tuple[int, int, int],
        show_label: bool = False,
    ) -> None:
        """Render a single bar graph into the pixel array."""
        if length <= 0:
            return

        # Apply threshold coloring
        display_color = self._get_threshold_color(value, color)

        # Calculate fill
        bar_start = start
        bar_length = length

        if show_label:
            # First pixel is the label (always the base color, dimmed)
            dim_color = tuple(c // 3 for c in color)
            pixels[start] = dim_color
            bar_start = start + 1
            bar_length = length - 1

        if bar_length <= 0:
            return

        fill_pixels = int(value * bar_length)

        for i in range(bar_length):
            idx = bar_start + i
            if idx >= len(pixels):
                break
            if i < fill_pixels:
                pixels[idx] = display_color
            else:
                # Dim background shows the bar outline
                dim_bg = tuple(max(c, display_color[j] // 10) for j, c in enumerate(background))
                pixels[idx] = dim_bg

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        # Update metrics
        self._update_metrics()

        # Get settings
        show_cpu = self.get_control("show_cpu")
        show_memory = self.get_control("show_memory")
        show_network = self.get_control("show_network")
        layout = self.get_control("layout")
        bar_gap = int(self.get_control("bar_gap"))
        show_labels = self.get_control("show_labels")
        background = hex_to_rgb(self.get_control("background"))

        # Collect enabled metrics
        metrics = []
        if show_cpu:
            metrics.append(("cpu", self._cpu_value, hex_to_rgb(self.get_control("cpu_color"))))
        if show_memory:
            metrics.append(("memory", self._memory_value, hex_to_rgb(self.get_control("memory_color"))))
        if show_network:
            metrics.append(("net_rx", self._network_rx_value, hex_to_rgb(self.get_control("network_rx_color"))))
            metrics.append(("net_tx", self._network_tx_value, hex_to_rgb(self.get_control("network_tx_color"))))

        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        pixels[:] = background

        if not metrics:
            return pixels

        if layout == "side_by_side":
            # Divide strip into sections for each metric
            num_bars = len(metrics)
            total_gaps = (num_bars - 1) * bar_gap
            bar_length = (num_pixels - total_gaps) // num_bars

            for i, (name, value, color) in enumerate(metrics):
                start = i * (bar_length + bar_gap)
                self._render_bar(pixels, start, bar_length, value, color, background, show_labels)

        elif layout == "stacked":
            # Overlay all metrics (highest value wins per pixel)
            for name, value, color in metrics:
                fill_pixels = int(value * num_pixels)
                display_color = self._get_threshold_color(value, color)
                for i in range(fill_pixels):
                    # Blend if pixel already has color
                    existing = tuple(pixels[i])
                    if existing == background:
                        pixels[i] = display_color
                    else:
                        # Additive blend (clamped)
                        pixels[i] = tuple(min(255, existing[j] + display_color[j] // 2) for j in range(3))

        return pixels


class CPUCoreMonitor(VirtualSource):
    """Displays per-core CPU usage as multiple bars.

    Each CPU core gets its own bar section, useful for monitoring
    multi-core utilization patterns.

    Requires psutil for real metrics.
    """

    source_type = "cpu_cores"

    def __init__(self, config: VirtualSourceConfig | None = None):
        super().__init__(config)
        self._core_values: list[float] = []
        self._last_update = 0.0
        self._update_interval = 0.5

    def _setup_controls(self) -> None:
        self._controls.register(
            NumberControl(
                id="update_rate",
                name="Update Rate",
                description="How often to sample CPU (seconds)",
                value=0.5,
                min=0.1,
                max=5.0,
                step=0.1,
                unit="s",
                group="metrics",
            )
        )
        self._controls.register(
            NumberControl(
                id="bar_gap",
                name="Bar Gap",
                description="Pixels between core bars",
                value=1,
                min=0,
                max=5,
                step=1,
                group="display",
            )
        )
        self._controls.register(
            EnumControl(
                id="color_mode",
                name="Color Mode",
                description="How to color the bars",
                value="threshold",
                options=[
                    {"value": "solid", "label": "Solid Green"},
                    {"value": "threshold", "label": "Threshold (G/Y/R)"},
                    {"value": "rainbow", "label": "Rainbow per Core"},
                ],
                group="display",
            )
        )
        self._controls.register(
            ColorControl(
                id="background",
                name="Background",
                description="Background color",
                value="#000000",
                group="display",
            )
        )

    def _update_metrics(self) -> None:
        """Update per-core CPU metrics."""
        current_time = time.time()

        if current_time - self._last_update < self._update_interval:
            return
        self._last_update = current_time
        self._update_interval = self.get_control("update_rate")

        if not PSUTIL_AVAILABLE:
            # Simulate 4 cores for testing
            self._core_values = [
                0.3 + 0.3 * np.sin(current_time * (0.5 + i * 0.2))
                for i in range(4)
            ]
            return

        # Get per-core CPU percentages
        core_percents = psutil.cpu_percent(interval=None, percpu=True)
        self._core_values = [p / 100.0 for p in core_percents]

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        self._update_metrics()

        bar_gap = int(self.get_control("bar_gap"))
        color_mode = self.get_control("color_mode")
        background = hex_to_rgb(self.get_control("background"))

        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        pixels[:] = background

        if not self._core_values:
            return pixels

        num_cores = len(self._core_values)
        total_gaps = (num_cores - 1) * bar_gap
        bar_length = (num_pixels - total_gaps) // num_cores

        for core_idx, value in enumerate(self._core_values):
            start = core_idx * (bar_length + bar_gap)
            fill_pixels = int(value * bar_length)

            # Determine color
            if color_mode == "threshold":
                if value >= 0.9:
                    color = (255, 0, 0)  # Red
                elif value >= 0.7:
                    color = (255, 255, 0)  # Yellow
                else:
                    color = (0, 255, 0)  # Green
            elif color_mode == "rainbow":
                hue = core_idx / max(1, num_cores - 1)
                # Simple HSV to RGB (hue only, full saturation/value)
                if hue < 1/6:
                    color = (255, int(255 * hue * 6), 0)
                elif hue < 2/6:
                    color = (int(255 * (2/6 - hue) * 6), 255, 0)
                elif hue < 3/6:
                    color = (0, 255, int(255 * (hue - 2/6) * 6))
                elif hue < 4/6:
                    color = (0, int(255 * (4/6 - hue) * 6), 255)
                elif hue < 5/6:
                    color = (int(255 * (hue - 4/6) * 6), 0, 255)
                else:
                    color = (255, 0, int(255 * (1 - hue) * 6))
            else:  # solid
                color = (0, 255, 0)

            # Fill bar
            for i in range(bar_length):
                idx = start + i
                if idx >= num_pixels:
                    break
                if i < fill_pixels:
                    pixels[idx] = color
                else:
                    # Dim indicator
                    pixels[idx] = tuple(c // 10 for c in color)

        return pixels
