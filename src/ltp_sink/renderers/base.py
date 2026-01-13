"""Base renderer interface."""

from abc import ABC, abstractmethod
from typing import Any

import numpy as np
from pydantic import BaseModel


class RendererConfig(BaseModel):
    """Base configuration for renderers."""

    type: str = "base"


class Renderer(ABC):
    """Abstract base class for LED display renderers."""

    def __init__(self, config: RendererConfig | None = None):
        self.config = config or RendererConfig()
        self._running = False
        self._frame_count = 0
        self._fps = 0.0

    @property
    def is_running(self) -> bool:
        """Check if renderer is running."""
        return self._running

    @property
    def frame_count(self) -> int:
        """Get total frames rendered."""
        return self._frame_count

    @property
    def fps(self) -> float:
        """Get current frames per second."""
        return self._fps

    @abstractmethod
    async def start(self) -> None:
        """Start the renderer."""
        self._running = True

    @abstractmethod
    async def stop(self) -> None:
        """Stop the renderer."""
        self._running = False

    @abstractmethod
    def render(self, pixels: np.ndarray, dimensions: tuple[int, ...]) -> None:
        """Render a frame of pixel data.

        Args:
            pixels: Pixel data as numpy array (pixels, channels) or (height, width, channels)
            dimensions: Display dimensions (length,) for 1D or (width, height) for 2D
        """
        self._frame_count += 1

    def get_stats(self) -> dict[str, Any]:
        """Get renderer statistics."""
        return {
            "running": self._running,
            "frame_count": self._frame_count,
            "fps": self._fps,
        }
