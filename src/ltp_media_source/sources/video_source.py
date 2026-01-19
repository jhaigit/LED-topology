"""Video logical source - outputs scaled video frames from shared context."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from libltp import EnumControl, EnumOption, NumberControl

from ltp_media_source.inputs.base import FitMode
from ltp_media_source.processing.scaler import FrameScaler
from ltp_media_source.processing.color import apply_gamma
from ltp_media_source.sources.base import LogicalSource, LogicalSourceConfig

if TYPE_CHECKING:
    from ltp_media_source.shared_context import SharedMediaContext

logger = logging.getLogger(__name__)


class VideoLogicalSourceConfig(LogicalSourceConfig):
    """Configuration for video logical source."""

    # Video-specific settings
    fit_mode: str = "contain"
    gamma: float = 1.0

    # Source type
    source_type: str = "video"


class VideoLogicalSource(LogicalSource):
    """Logical source that outputs scaled video frames.

    This source reads video frames from a SharedMediaContext and scales
    them to the configured output dimensions using the specified fit mode.
    """

    def __init__(
        self,
        context: SharedMediaContext,
        config: VideoLogicalSourceConfig | None = None,
    ):
        """Initialize the video logical source.

        Args:
            context: SharedMediaContext to get video frames from
            config: Source configuration
        """
        if config is None:
            config = VideoLogicalSourceConfig()

        super().__init__(context, config)

        self._video_config = config

        # Frame scaler
        self._fit_mode = FitMode(config.fit_mode)
        self._scaler = FrameScaler(self._width, self._height, self._fit_mode)

        # Gamma value
        self._gamma = config.gamma

    def _setup_controls(self) -> None:
        """Set up video source controls."""
        super()._setup_controls()

        # Gamma control
        self._controls.register(
            NumberControl(
                id="gamma",
                name="Gamma",
                description="Gamma correction",
                value=self._video_config.gamma if hasattr(self, "_video_config") else 1.0,
                min=0.5,
                max=3.0,
                step=0.1,
                group="output",
            )
        )

        # Fit mode control
        self._controls.register(
            EnumControl(
                id="fit_mode",
                name="Fit Mode",
                description="How to fit video to display",
                value=self._video_config.fit_mode if hasattr(self, "_video_config") else "contain",
                options=[
                    EnumOption(value="contain", label="Contain", description="Fit within bounds, letterbox"),
                    EnumOption(value="cover", label="Cover", description="Fill and crop edges"),
                    EnumOption(value="stretch", label="Stretch", description="Stretch to fill"),
                    EnumOption(value="tile", label="Tile", description="Repeat pattern"),
                    EnumOption(value="center", label="Center", description="Center without scaling"),
                ],
                group="display",
            )
        )

    async def _handle_control_set(self, message):
        """Handle control set with video-specific controls."""
        from libltp import control_set_response

        values = message.data.get("values", {})
        applied = {}
        errors = {}

        # Shared controls
        shared_controls = {"paused", "loop", "speed", "seek", "position", "play", "pause"}

        for control_id, value in values.items():
            try:
                if control_id in shared_controls:
                    # Forward to shared context
                    handled = await self._context.handle_control(control_id, value)
                    if handled:
                        applied[control_id] = value
                        if control_id in ("paused", "loop", "speed"):
                            self._controls.set_value(control_id, value)
                    else:
                        errors[control_id] = "Control not handled"
                elif control_id == "fit_mode":
                    self._fit_mode = FitMode(value)
                    self._scaler.set_fit_mode(self._fit_mode)
                    self._controls.set_value(control_id, value)
                    applied[control_id] = value
                elif control_id == "gamma":
                    self._gamma = float(value)
                    self._controls.set_value(control_id, value)
                    applied[control_id] = value
                else:
                    # Local control
                    self._controls.set_value(control_id, value)
                    applied[control_id] = self._controls.get_value(control_id)
            except Exception as e:
                errors[control_id] = str(e)

        status = "ok" if not errors else "partial"
        return control_set_response(message.seq, status, applied, errors or None)

    async def render_frame(self) -> np.ndarray | None:
        """Render a scaled video frame.

        Returns:
            Scaled frame as RGB uint8 array (height, width, 3), or None.
        """
        # Get video frame from shared context
        frame = await self._context.get_video_frame()

        if frame is None:
            return None

        # Scale to output dimensions
        scaled = self._scaler.scale(frame)

        # Apply gamma correction
        if self._gamma != 1.0:
            scaled = apply_gamma(scaled, self._gamma)

        return scaled

    def set_fit_mode(self, mode: str | FitMode) -> None:
        """Change the fit mode.

        Args:
            mode: Fit mode (contain, cover, stretch, tile, center)
        """
        if isinstance(mode, str):
            mode = FitMode(mode)
        self._fit_mode = mode
        self._scaler.set_fit_mode(mode)
        self._controls.set_value("fit_mode", mode.value)

    @property
    def fit_mode(self) -> FitMode:
        """Current fit mode."""
        return self._fit_mode

    @property
    def gamma(self) -> float:
        """Current gamma value."""
        return self._gamma

    @gamma.setter
    def gamma(self, value: float) -> None:
        """Set gamma value."""
        self._gamma = max(0.5, min(3.0, value))
        self._controls.set_value("gamma", self._gamma)
