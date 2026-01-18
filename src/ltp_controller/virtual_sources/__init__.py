"""Virtual sources for controller-generated effects."""

from ltp_controller.virtual_sources.base import VirtualSource, VirtualSourceManager
from ltp_controller.virtual_sources.patterns import (
    RainbowPattern,
    ChasePattern,
    CylonPattern,
    FlamePattern,
    SparklePattern,
    SolidPattern,
    GradientPattern,
    BreathePattern,
    StrobePattern,
)
from ltp_controller.virtual_sources.matrix_patterns import (
    GridPattern,
    CornerMarkers,
    RowColumnSweep,
    Checkerboard,
    PixelIndex,
    CoordinateDisplay,
    TestCard,
)
from ltp_controller.virtual_sources.visualizers import (
    BarGraph,
    MultiBar,
    VUMeter,
)
from ltp_controller.virtual_sources.monitors import (
    SystemMonitor,
    CPUCoreMonitor,
)

__all__ = [
    "VirtualSource",
    "VirtualSourceManager",
    # Patterns
    "RainbowPattern",
    "ChasePattern",
    "CylonPattern",
    "FlamePattern",
    "SparklePattern",
    "SolidPattern",
    "GradientPattern",
    "BreathePattern",
    "StrobePattern",
    # Matrix Test Patterns
    "GridPattern",
    "CornerMarkers",
    "RowColumnSweep",
    "Checkerboard",
    "PixelIndex",
    "CoordinateDisplay",
    "TestCard",
    # Visualizers
    "BarGraph",
    "MultiBar",
    "VUMeter",
    # Monitors
    "SystemMonitor",
    "CPUCoreMonitor",
]

# Registry of available virtual source types
VIRTUAL_SOURCE_TYPES: dict[str, type[VirtualSource]] = {
    # Patterns
    "rainbow": RainbowPattern,
    "chase": ChasePattern,
    "cylon": CylonPattern,
    "flame": FlamePattern,
    "sparkle": SparklePattern,
    "solid": SolidPattern,
    "gradient": GradientPattern,
    "breathe": BreathePattern,
    "strobe": StrobePattern,
    # Matrix Test Patterns (for debugging pixel addressing)
    "grid": GridPattern,
    "corners": CornerMarkers,
    "sweep": RowColumnSweep,
    "checkerboard": Checkerboard,
    "pixel_index": PixelIndex,
    "coordinates": CoordinateDisplay,
    "test_card": TestCard,
    # Visualizers
    "bar_graph": BarGraph,
    "multi_bar": MultiBar,
    "vu_meter": VUMeter,
    # Monitors
    "system_monitor": SystemMonitor,
    "cpu_cores": CPUCoreMonitor,
}
