"""Microphone/line-in audio input using sounddevice.

Provides real-time audio capture from microphones, line-in, or other
audio input devices for visualization.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, TYPE_CHECKING, Callable

import numpy as np

from ltp_media_source.inputs.base import MediaInput, FitMode

if TYPE_CHECKING:
    from ltp_media_source.shared_context import SharedMediaContext

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False
    logger.warning("sounddevice not available - microphone input disabled")


def list_audio_devices() -> list[dict]:
    """List available audio input devices.

    Returns:
        List of device info dictionaries with 'index', 'name', 'channels', 'sample_rate'
    """
    if not HAS_SOUNDDEVICE:
        return []

    devices = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            devices.append({
                "index": i,
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "sample_rate": dev["default_samplerate"],
            })
    return devices


def get_default_input_device() -> int | None:
    """Get the default input device index."""
    if not HAS_SOUNDDEVICE:
        return None
    try:
        return sd.default.device[0]  # Input device
    except Exception:
        return None


class MicrophoneInput(MediaInput):
    """Live audio input from microphone or line-in.

    This input captures real-time audio from the system's audio input devices.
    Since there's no video, it provides audio samples for visualization while
    returning None for video frames.

    Example:
        # Use default microphone
        mic = MicrophoneInput()
        mic.open()

        # Use specific device
        mic = MicrophoneInput(device=2)  # Device index 2

        # Use device by name
        mic = MicrophoneInput(device="USB Audio")  # Partial name match
    """

    input_type = "microphone"

    def __init__(
        self,
        device: int | str | None = None,
        sample_rate: int = 44100,
        channels: int = 2,
        block_size: int = 1024,
        frame_rate: float = 30.0,
        **kwargs: Any,
    ):
        """Initialize microphone input.

        Args:
            device: Device index (int), device name (str), or None for default
            sample_rate: Audio sample rate in Hz
            channels: Number of channels (1 for mono, 2 for stereo)
            block_size: Audio block size (samples per callback)
            frame_rate: Frame rate for visualization updates
        """
        if not HAS_SOUNDDEVICE:
            raise RuntimeError(
                "sounddevice required for microphone input. "
                "Install with: pip install sounddevice"
            )

        super().__init__(
            path=None, fit_mode=FitMode.CONTAIN, loop=False, speed=1.0, **kwargs
        )

        self._device_spec = device
        self._device_index: int | None = None
        self._sample_rate = sample_rate
        self._channels = channels
        self._block_size = block_size
        self._frame_rate = frame_rate

        # Capture state
        self._stream: Any = None  # sd.InputStream
        self._audio_queue: queue.Queue = queue.Queue(maxsize=100)
        self._shared_context: SharedMediaContext | None = None
        self._running = False
        self._last_frame_time = 0.0
        self._samples_per_frame = 0

        # Device info
        self._device_name = "Unknown"

    @property
    def has_audio(self) -> bool:
        """Always True for microphone input."""
        return True

    @property
    def frame_rate(self) -> float:
        """Frame rate for visualization."""
        return self._frame_rate

    @property
    def duration(self) -> float | None:
        """Live sources have no duration."""
        return None

    @property
    def native_dimensions(self) -> tuple[int, int]:
        """No video dimensions for audio-only input."""
        return (0, 0)

    @property
    def is_live(self) -> bool:
        """Microphone is a live source."""
        return True

    @property
    def sample_rate(self) -> int:
        """Audio sample rate."""
        return self._sample_rate

    @property
    def device_name(self) -> str:
        """Name of the audio device."""
        return self._device_name

    def set_shared_context(self, context: SharedMediaContext) -> None:
        """Set the shared context for audio buffer access."""
        self._shared_context = context

    def _resolve_device(self) -> int | None:
        """Resolve device specification to device index."""
        if self._device_spec is None:
            # Use default input device
            return get_default_input_device()

        if isinstance(self._device_spec, int):
            return self._device_spec

        if isinstance(self._device_spec, str):
            # Search by name (partial match)
            devices = list_audio_devices()
            for dev in devices:
                if self._device_spec.lower() in dev["name"].lower():
                    return dev["index"]
            logger.warning(f"Device not found: {self._device_spec}")
            return None

        return None

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        """Callback for audio stream - runs in separate thread."""
        if status:
            logger.debug(f"Audio input status: {status}")

        # Convert to float32 if needed and put in queue
        try:
            samples = indata.copy().astype(np.float32)

            # Convert mono to stereo if needed
            if samples.ndim == 1:
                samples = np.column_stack((samples, samples))
            elif samples.shape[1] == 1:
                samples = np.column_stack((samples[:, 0], samples[:, 0]))

            self._audio_queue.put_nowait(samples)
        except queue.Full:
            # Drop samples if queue is full
            pass

    def open(self) -> None:
        """Start audio capture."""
        if self._opened:
            return

        # Resolve device
        self._device_index = self._resolve_device()
        if self._device_index is None:
            raise RuntimeError("No audio input device available")

        # Get device info
        try:
            dev_info = sd.query_devices(self._device_index)
            self._device_name = dev_info["name"]
            actual_channels = min(self._channels, dev_info["max_input_channels"])

            # Check sample rate support
            try:
                sd.check_input_settings(
                    device=self._device_index,
                    channels=actual_channels,
                    samplerate=self._sample_rate,
                )
            except Exception:
                # Fall back to device default
                self._sample_rate = int(dev_info["default_samplerate"])
                logger.debug(f"Using device default sample rate: {self._sample_rate}")

        except Exception as e:
            raise RuntimeError(f"Failed to query device: {e}") from e

        # Calculate samples per frame
        self._samples_per_frame = int(self._sample_rate / self._frame_rate)

        try:
            self._stream = sd.InputStream(
                device=self._device_index,
                channels=actual_channels,
                samplerate=self._sample_rate,
                blocksize=self._block_size,
                dtype=np.float32,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._running = True
            self._opened = True
            self._last_frame_time = time.monotonic()

            logger.info(
                f"Opened microphone: {self._device_name} "
                f"({self._sample_rate}Hz, {actual_channels}ch)"
            )

        except Exception as e:
            self._stream = None
            raise RuntimeError(f"Failed to open audio stream: {e}") from e

    def read_frame(self) -> np.ndarray | None:
        """Process audio samples since last call.

        Returns None for video frame but writes audio samples to shared context.
        """
        if not self._opened or not self._running:
            return None

        # Collect all available audio samples from queue
        samples_list = []
        try:
            while True:
                samples = self._audio_queue.get_nowait()
                samples_list.append(samples)
        except queue.Empty:
            pass

        # Write samples to shared context
        if samples_list and self._shared_context is not None:
            all_samples = np.vstack(samples_list)
            self._shared_context.write_audio_samples(all_samples)

        # Update position (time since start)
        now = time.monotonic()
        self._position = now - self._last_frame_time

        # Return None since there's no video
        return None

    def seek(self, position: float) -> bool:
        """Seeking not supported for live input."""
        return False

    def close(self) -> None:
        """Stop audio capture."""
        self._running = False

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        # Clear queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        self._opened = False
        logger.debug(f"Closed microphone: {self._device_name}")


class SystemAudioInput(MediaInput):
    """Capture system audio output (loopback).

    Note: This requires platform-specific setup:
    - Linux: PulseAudio monitor source or JACK
    - macOS: BlackHole, Soundflower, or similar virtual audio device
    - Windows: WASAPI loopback (may require specific device selection)

    This class uses the same interface as MicrophoneInput but is intended
    for capturing system audio playback.
    """

    input_type = "system_audio"

    def __init__(
        self,
        device: int | str | None = None,
        sample_rate: int = 44100,
        channels: int = 2,
        block_size: int = 1024,
        frame_rate: float = 30.0,
        **kwargs: Any,
    ):
        """Initialize system audio capture.

        Args:
            device: Loopback device index or name (e.g., "Monitor" on Linux)
            sample_rate: Audio sample rate in Hz
            channels: Number of channels
            block_size: Audio block size
            frame_rate: Frame rate for visualization updates
        """
        # Use MicrophoneInput implementation
        self._mic_input = MicrophoneInput(
            device=device,
            sample_rate=sample_rate,
            channels=channels,
            block_size=block_size,
            frame_rate=frame_rate,
            **kwargs,
        )

    @property
    def has_audio(self) -> bool:
        return True

    @property
    def frame_rate(self) -> float:
        return self._mic_input.frame_rate

    @property
    def duration(self) -> float | None:
        return None

    @property
    def native_dimensions(self) -> tuple[int, int]:
        return (0, 0)

    @property
    def is_live(self) -> bool:
        return True

    def set_shared_context(self, context: SharedMediaContext) -> None:
        self._mic_input.set_shared_context(context)

    def open(self) -> None:
        self._mic_input.open()
        self._opened = True

    def read_frame(self) -> np.ndarray | None:
        return self._mic_input.read_frame()

    def seek(self, position: float) -> bool:
        return False

    def close(self) -> None:
        self._mic_input.close()
        self._opened = False
