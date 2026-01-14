"""Base classes for virtual sources."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

import numpy as np

from libltp import ControlRegistry, NumberControl, BooleanControl, EnumControl, ColorControl

logger = logging.getLogger(__name__)


@dataclass
class VirtualSourceConfig:
    """Configuration for a virtual source."""

    id: str = field(default_factory=lambda: f"vs-{uuid4().hex[:8]}")
    name: str = ""
    source_type: str = ""
    output_dimensions: list[int] = field(default_factory=lambda: [60])
    frame_rate: float = 30.0
    adaptive_dimensions: bool = False
    enabled: bool = True
    control_values: dict[str, Any] = field(default_factory=dict)


class VirtualSource(ABC):
    """Abstract base class for virtual sources."""

    # Class-level type identifier
    source_type: str = "unknown"

    def __init__(self, config: VirtualSourceConfig | None = None):
        self.config = config or VirtualSourceConfig()
        if not self.config.name:
            self.config.name = f"{self.source_type.title()} Source"
        self.config.source_type = self.source_type

        # Controls
        self._controls = ControlRegistry()
        self._setup_base_controls()
        self._setup_controls()

        # Apply saved control values
        if self.config.control_values:
            self._controls.set_values(self.config.control_values)

        # Runtime state
        self._running = False
        self._start_time = 0.0
        self._frame_count = 0
        self._last_frame_time = 0.0

        # Render callbacks per sink
        self._render_callbacks: dict[str, Callable[[np.ndarray], None]] = {}

    def _setup_base_controls(self) -> None:
        """Set up base controls common to all virtual sources."""
        self._controls.register(
            NumberControl(
                id="speed",
                name="Speed",
                description="Animation speed multiplier",
                value=1.0,
                min=0.1,
                max=10.0,
                step=0.1,
                group="animation",
            )
        )
        self._controls.register(
            NumberControl(
                id="brightness",
                name="Brightness",
                description="Output brightness",
                value=1.0,
                min=0.0,
                max=1.0,
                step=0.05,
                group="output",
            )
        )
        self._controls.register(
            BooleanControl(
                id="reverse",
                name="Reverse",
                description="Reverse animation direction",
                value=False,
                group="animation",
            )
        )
        self._controls.register(
            BooleanControl(
                id="mirror",
                name="Mirror",
                description="Mirror pattern around center",
                value=False,
                group="animation",
            )
        )

    @abstractmethod
    def _setup_controls(self) -> None:
        """Set up source-specific controls. Override in subclasses."""
        pass

    @abstractmethod
    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        """Render a frame of pixel data.

        Args:
            num_pixels: Number of pixels to render
            time_elapsed: Time since source started (seconds)

        Returns:
            numpy array of shape (num_pixels, 3) with RGB values
        """
        pass

    def set_data(self, data: Any) -> None:
        """Set input data for data visualizers. Override in subclasses."""
        pass

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def controls(self) -> ControlRegistry:
        return self._controls

    @property
    def is_running(self) -> bool:
        return self._running

    def get_control(self, control_id: str) -> Any:
        """Get a control value."""
        return self._controls.get_value(control_id)

    def set_control(self, control_id: str, value: Any) -> bool:
        """Set a control value."""
        applied, errors = self._controls.set_values({control_id: value})
        if control_id in applied:
            self.config.control_values[control_id] = value
            return True
        return False

    def _apply_base_transforms(
        self, pixels: np.ndarray, time_elapsed: float
    ) -> np.ndarray:
        """Apply base transforms (brightness, mirror, etc.)."""
        # Apply brightness
        brightness = self.get_control("brightness")
        if brightness < 1.0:
            pixels = (pixels * brightness).astype(np.uint8)

        # Apply mirror
        if self.get_control("mirror"):
            half = len(pixels) // 2
            pixels[half:] = pixels[: len(pixels) - half][::-1]

        return pixels

    def get_time_elapsed(self) -> float:
        """Get time elapsed since start, adjusted for speed."""
        if not self._running:
            return 0.0
        real_elapsed = time.time() - self._start_time
        speed = self.get_control("speed")
        if self.get_control("reverse"):
            speed = -speed
        return real_elapsed * speed

    def start(self) -> None:
        """Start the virtual source."""
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
        self._frame_count = 0
        logger.info(f"Started virtual source: {self.name}")

    def stop(self) -> None:
        """Stop the virtual source."""
        if not self._running:
            return
        self._running = False
        logger.info(f"Stopped virtual source: {self.name}")

    def render_frame(self, num_pixels: int) -> np.ndarray:
        """Render a single frame with all transforms applied."""
        time_elapsed = self.get_time_elapsed()
        pixels = self.render(num_pixels, time_elapsed)
        pixels = self._apply_base_transforms(pixels, time_elapsed)
        self._frame_count += 1
        return pixels

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.config.id,
            "name": self.config.name,
            "type": self.source_type,
            "output_dimensions": self.config.output_dimensions,
            "frame_rate": self.config.frame_rate,
            "adaptive_dimensions": self.config.adaptive_dimensions,
            "enabled": self.config.enabled,
            "running": self._running,
            "frame_count": self._frame_count,
            "controls": self._controls.to_list(),
            "control_values": self._controls.get_values(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VirtualSource":
        """Create from dictionary."""
        config = VirtualSourceConfig(
            id=data.get("id", f"vs-{uuid4().hex[:8]}"),
            name=data.get("name", ""),
            source_type=data.get("type", cls.source_type),
            output_dimensions=data.get("output_dimensions", [60]),
            frame_rate=data.get("frame_rate", 30.0),
            adaptive_dimensions=data.get("adaptive_dimensions", False),
            enabled=data.get("enabled", True),
            control_values=data.get("control_values", {}),
        )
        return cls(config)


class VirtualSourceManager:
    """Manages virtual sources and their render loops."""

    def __init__(self) -> None:
        self._sources: dict[str, VirtualSource] = {}
        self._render_tasks: dict[str, asyncio.Task] = {}
        self._running = False

    @property
    def sources(self) -> list[VirtualSource]:
        """Get all virtual sources."""
        return list(self._sources.values())

    def get(self, source_id: str) -> VirtualSource | None:
        """Get a virtual source by ID."""
        return self._sources.get(source_id)

    def add(self, source: VirtualSource) -> None:
        """Add a virtual source."""
        self._sources[source.id] = source
        if self._running and source.config.enabled:
            source.start()
        logger.info(f"Added virtual source: {source.name} ({source.id})")

    def remove(self, source_id: str) -> bool:
        """Remove a virtual source."""
        source = self._sources.pop(source_id, None)
        if source:
            source.stop()
            # Cancel render task if running
            task = self._render_tasks.pop(source_id, None)
            if task:
                task.cancel()
            logger.info(f"Removed virtual source: {source.name}")
            return True
        return False

    def create(
        self,
        source_type: str,
        name: str | None = None,
        **kwargs: Any,
    ) -> VirtualSource | None:
        """Create a new virtual source of the given type."""
        # Import here to avoid circular imports
        from ltp_controller.virtual_sources import VIRTUAL_SOURCE_TYPES

        source_class = VIRTUAL_SOURCE_TYPES.get(source_type)
        if not source_class:
            logger.error(f"Unknown virtual source type: {source_type}")
            return None

        config = VirtualSourceConfig(
            name=name or f"{source_type.title()} Source",
            source_type=source_type,
            **kwargs,
        )
        source = source_class(config)
        self.add(source)
        return source

    def start(self) -> None:
        """Start the manager and all enabled sources."""
        self._running = True
        for source in self._sources.values():
            if source.config.enabled:
                source.start()
        logger.info("Virtual source manager started")

    def stop(self) -> None:
        """Stop the manager and all sources."""
        self._running = False
        for source in self._sources.values():
            source.stop()
        # Cancel all render tasks
        for task in self._render_tasks.values():
            task.cancel()
        self._render_tasks.clear()
        logger.info("Virtual source manager stopped")

    def to_list(self) -> list[dict[str, Any]]:
        """Convert all sources to list of dictionaries."""
        return [s.to_dict() for s in self._sources.values()]

    def to_config(self) -> list[dict[str, Any]]:
        """Export sources configuration for persistence."""
        result = []
        for source in self._sources.values():
            result.append(
                {
                    "id": source.config.id,
                    "type": source.source_type,
                    "name": source.config.name,
                    "output_dimensions": source.config.output_dimensions,
                    "frame_rate": source.config.frame_rate,
                    "adaptive_dimensions": source.config.adaptive_dimensions,
                    "enabled": source.config.enabled,
                    "control_values": source._controls.get_values(),
                }
            )
        return result

    def load_from_config(self, config: list[dict[str, Any]]) -> None:
        """Load sources from configuration."""
        from ltp_controller.virtual_sources import VIRTUAL_SOURCE_TYPES

        for item in config:
            source_type = item.get("type")
            if not source_type:
                continue

            source_class = VIRTUAL_SOURCE_TYPES.get(source_type)
            if not source_class:
                logger.warning(f"Unknown virtual source type in config: {source_type}")
                continue

            source_config = VirtualSourceConfig(
                id=item.get("id", f"vs-{uuid4().hex[:8]}"),
                name=item.get("name", ""),
                source_type=source_type,
                output_dimensions=item.get("output_dimensions", [60]),
                frame_rate=item.get("frame_rate", 30.0),
                adaptive_dimensions=item.get("adaptive_dimensions", False),
                enabled=item.get("enabled", True),
                control_values=item.get("control_values", {}),
            )
            source = source_class(source_config)
            self.add(source)
