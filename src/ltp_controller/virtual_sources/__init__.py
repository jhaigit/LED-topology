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
from ltp_controller.virtual_sources.visualizers import (
    BarGraph,
    MultiBar,
    VUMeter,
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
    # Visualizers
    "BarGraph",
    "MultiBar",
    "VUMeter",
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
    # Visualizers
    "bar_graph": BarGraph,
    "multi_bar": MultiBar,
    "vu_meter": VUMeter,
}
