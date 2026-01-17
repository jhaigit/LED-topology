"""Camera/webcam input using OpenCV."""

import logging
from typing import Any

import numpy as np

from ltp_media_source.inputs.base import MediaInput, FitMode

logger = logging.getLogger(__name__)

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False
    logger.warning("OpenCV not available - camera input disabled")


class CameraInput(MediaInput):
    """Live camera/webcam input."""

    input_type = "camera"

    def __init__(
        self,
        device: int | str = 0,
        fit_mode: FitMode = FitMode.CONTAIN,
        resolution: tuple[int, int] | None = None,
        fps: int = 30,
        **kwargs: Any,
    ):
        """Initialize camera input.

        Args:
            device: Camera device index (0, 1, ...) or device path (/dev/video0)
            fit_mode: How to fit frames to target dimensions
            resolution: Requested capture resolution (width, height)
            fps: Requested frame rate
        """
        if not HAS_OPENCV:
            raise RuntimeError("OpenCV required for camera input. Install opencv-python-headless")

        # Convert device to path string for base class
        path = str(device) if isinstance(device, int) else device
        super().__init__(path=path, fit_mode=fit_mode, loop=True, **kwargs)

        self.device = device
        self.requested_resolution = resolution
        self.requested_fps = fps

        self._cap: Any = None  # cv2.VideoCapture
        self._width = 0
        self._height = 0
        self._fps = float(fps)

    def open(self) -> None:
        """Open the camera device."""
        if self._opened:
            return

        try:
            # Open camera
            if isinstance(self.device, int):
                self._cap = cv2.VideoCapture(self.device)
            else:
                self._cap = cv2.VideoCapture(self.device)

            if not self._cap.isOpened():
                raise RuntimeError(f"Failed to open camera: {self.device}")

            # Set resolution if requested
            if self.requested_resolution:
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.requested_resolution[0])
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.requested_resolution[1])

            # Set FPS if requested
            self._cap.set(cv2.CAP_PROP_FPS, self.requested_fps)

            # Get actual properties
            self._width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self._height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self._fps = self._cap.get(cv2.CAP_PROP_FPS) or float(self.requested_fps)

            self._opened = True
            logger.info(
                f"Opened camera: {self.device} ({self._width}x{self._height}, {self._fps:.1f}fps)"
            )

        except Exception as e:
            if self._cap:
                self._cap.release()
                self._cap = None
            raise RuntimeError(f"Failed to open camera: {e}") from e

    def read_frame(self) -> np.ndarray | None:
        """Read frame from camera."""
        if not self._opened or self._cap is None:
            return None

        ret, frame = self._cap.read()

        if not ret:
            logger.warning("Failed to read frame from camera")
            return None

        # Convert BGR to RGB
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        self._frame_index += 1

        return frame_rgb

    def close(self) -> None:
        """Release camera."""
        if self._cap:
            self._cap.release()
            self._cap = None
        self._opened = False
        logger.debug(f"Closed camera: {self.device}")

    @property
    def frame_rate(self) -> float:
        """Camera frame rate."""
        return self._fps

    @property
    def duration(self) -> float | None:
        """Cameras are live sources with no duration."""
        return None

    @property
    def native_dimensions(self) -> tuple[int, int]:
        """Camera resolution."""
        return (self._width, self._height)

    @property
    def is_live(self) -> bool:
        """Camera is always a live source."""
        return True
