"""Audio playback module for playing audio through speakers.

Allows playing audio from media files while simultaneously driving
visualizations. Uses sounddevice for cross-platform audio output.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ltp_media_source.shared_context import SharedMediaContext

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False
    logger.warning("sounddevice not available - audio playback disabled")


def list_output_devices() -> list[dict]:
    """List available audio output devices.

    Returns:
        List of device info dictionaries
    """
    if not HAS_SOUNDDEVICE:
        return []

    devices = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] > 0:
            devices.append({
                "index": i,
                "name": dev["name"],
                "channels": dev["max_output_channels"],
                "sample_rate": dev["default_samplerate"],
            })
    return devices


def get_default_output_device() -> int | None:
    """Get the default output device index."""
    if not HAS_SOUNDDEVICE:
        return None
    try:
        return sd.default.device[1]  # Output device
    except Exception:
        return None


class AudioPlayback:
    """Audio playback for playing audio through speakers.

    This class plays audio while also providing samples for visualization.
    It can be used with SharedMediaContext to play audio from video files
    or standalone audio files.

    Example:
        # Play audio from video file
        context = SharedMediaContext(video_input)
        playback = AudioPlayback(sample_rate=44100)
        playback.start()

        # In the render loop:
        samples = get_audio_samples()
        playback.write(samples)  # Play through speakers

        # When done:
        playback.stop()
    """

    def __init__(
        self,
        sample_rate: int = 44100,
        channels: int = 2,
        device: int | str | None = None,
        buffer_size: int = 2048,
        latency: str = "low",
    ):
        """Initialize audio playback.

        Args:
            sample_rate: Audio sample rate in Hz
            channels: Number of output channels (1 or 2)
            device: Output device index, name, or None for default
            buffer_size: Audio buffer size in samples
            latency: Latency setting ('low', 'high', or seconds)
        """
        if not HAS_SOUNDDEVICE:
            raise RuntimeError(
                "sounddevice required for audio playback. "
                "Install with: pip install sounddevice"
            )

        self._sample_rate = sample_rate
        self._channels = channels
        self._device_spec = device
        self._buffer_size = buffer_size
        self._latency = latency

        # Playback state
        self._stream: Any = None
        self._audio_queue: queue.Queue = queue.Queue(maxsize=50)
        self._running = False
        self._paused = False
        self._volume = 1.0
        self._device_index: int | None = None
        self._device_name = "Unknown"

        # Underrun handling
        self._underrun_count = 0
        self._last_samples: np.ndarray | None = None

    @property
    def sample_rate(self) -> int:
        """Audio sample rate."""
        return self._sample_rate

    @property
    def channels(self) -> int:
        """Number of output channels."""
        return self._channels

    @property
    def is_running(self) -> bool:
        """True if playback is active."""
        return self._running

    @property
    def is_paused(self) -> bool:
        """True if playback is paused."""
        return self._paused

    @property
    def volume(self) -> float:
        """Output volume (0.0 to 1.0)."""
        return self._volume

    @volume.setter
    def volume(self, value: float) -> None:
        """Set output volume."""
        self._volume = max(0.0, min(1.0, value))

    @property
    def device_name(self) -> str:
        """Name of the output device."""
        return self._device_name

    def _resolve_device(self) -> int | None:
        """Resolve device specification to device index."""
        if self._device_spec is None:
            return get_default_output_device()

        if isinstance(self._device_spec, int):
            return self._device_spec

        if isinstance(self._device_spec, str):
            devices = list_output_devices()
            for dev in devices:
                if self._device_spec.lower() in dev["name"].lower():
                    return dev["index"]
            logger.warning(f"Output device not found: {self._device_spec}")
            return None

        return None

    def _audio_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        """Callback for audio output stream."""
        if status:
            if status.output_underflow:
                self._underrun_count += 1
                logger.debug(f"Audio underrun (total: {self._underrun_count})")

        if self._paused:
            # Output silence when paused
            outdata.fill(0)
            return

        try:
            # Get samples from queue
            samples = self._audio_queue.get_nowait()

            # Apply volume
            if self._volume < 1.0:
                samples = samples * self._volume

            # Ensure correct shape
            if samples.ndim == 1:
                samples = np.column_stack((samples, samples))

            # Handle size mismatch
            if len(samples) >= frames:
                outdata[:] = samples[:frames]
            else:
                # Pad with zeros
                outdata[:len(samples)] = samples
                outdata[len(samples):] = 0

            self._last_samples = samples

        except queue.Empty:
            # No samples available - output silence or repeat last samples
            if self._last_samples is not None and len(self._last_samples) >= frames:
                # Fade out to avoid clicking
                outdata[:] = self._last_samples[:frames] * 0.5
                self._last_samples = self._last_samples * 0.5
            else:
                outdata.fill(0)

    def start(self) -> None:
        """Start audio playback."""
        if self._running:
            return

        # Resolve device
        self._device_index = self._resolve_device()
        if self._device_index is None:
            raise RuntimeError("No audio output device available")

        try:
            # Get device info
            dev_info = sd.query_devices(self._device_index)
            self._device_name = dev_info["name"]
            actual_channels = min(self._channels, dev_info["max_output_channels"])

            # Create output stream
            self._stream = sd.OutputStream(
                device=self._device_index,
                channels=actual_channels,
                samplerate=self._sample_rate,
                blocksize=self._buffer_size,
                dtype=np.float32,
                callback=self._audio_callback,
                latency=self._latency,
            )
            self._stream.start()
            self._running = True
            self._paused = False

            logger.info(
                f"Started audio playback: {self._device_name} "
                f"({self._sample_rate}Hz, {actual_channels}ch)"
            )

        except Exception as e:
            self._stream = None
            raise RuntimeError(f"Failed to start audio playback: {e}") from e

    def stop(self) -> None:
        """Stop audio playback."""
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

        logger.debug(f"Stopped audio playback: {self._device_name}")

    def pause(self) -> None:
        """Pause audio playback."""
        self._paused = True

    def resume(self) -> None:
        """Resume audio playback."""
        self._paused = False

    def write(self, samples: np.ndarray) -> bool:
        """Write audio samples to playback buffer.

        Args:
            samples: Audio samples as float32 array, shape (n,) or (n, 2)

        Returns:
            True if samples were queued, False if buffer is full
        """
        if not self._running:
            return False

        try:
            # Ensure float32
            if samples.dtype != np.float32:
                samples = samples.astype(np.float32)

            # Ensure stereo
            if samples.ndim == 1:
                samples = np.column_stack((samples, samples))
            elif samples.shape[1] == 1:
                samples = np.column_stack((samples[:, 0], samples[:, 0]))

            self._audio_queue.put_nowait(samples)
            return True

        except queue.Full:
            return False

    def get_queue_size(self) -> int:
        """Get number of sample blocks waiting to be played."""
        return self._audio_queue.qsize()


class AudioPlaybackContext:
    """Audio playback integrated with SharedMediaContext.

    This class wraps AudioPlayback and integrates it with the media source
    system, automatically playing audio samples as they're decoded.
    """

    def __init__(
        self,
        context: SharedMediaContext,
        device: int | str | None = None,
        volume: float = 1.0,
        enabled: bool = True,
    ):
        """Initialize playback context.

        Args:
            context: The shared media context
            device: Output device index, name, or None for default
            volume: Initial volume (0.0 to 1.0)
            enabled: Whether to start playback enabled
        """
        self._context = context
        self._enabled = enabled
        self._playback: AudioPlayback | None = None

        if enabled and HAS_SOUNDDEVICE:
            self._playback = AudioPlayback(
                sample_rate=context.audio_sample_rate,
                channels=context.audio_channels,
                device=device,
            )
            self._playback.volume = volume

    @property
    def enabled(self) -> bool:
        """True if audio playback is enabled."""
        return self._enabled and self._playback is not None

    @property
    def volume(self) -> float:
        """Playback volume."""
        return self._playback.volume if self._playback else 0.0

    @volume.setter
    def volume(self, value: float) -> None:
        """Set playback volume."""
        if self._playback:
            self._playback.volume = value

    def start(self) -> None:
        """Start audio playback."""
        if self._playback and self._enabled:
            self._playback.start()

    def stop(self) -> None:
        """Stop audio playback."""
        if self._playback:
            self._playback.stop()

    def pause(self) -> None:
        """Pause playback."""
        if self._playback:
            self._playback.pause()

    def resume(self) -> None:
        """Resume playback."""
        if self._playback:
            self._playback.resume()

    def write_samples(self, samples: np.ndarray) -> None:
        """Write samples to playback (called by media decoder)."""
        if self._playback and self._enabled:
            self._playback.write(samples)


def create_playback_for_context(
    context: SharedMediaContext,
    device: int | str | None = None,
    volume: float = 1.0,
) -> AudioPlaybackContext | None:
    """Create an audio playback context if sounddevice is available.

    Args:
        context: The shared media context
        device: Output device index, name, or None for default
        volume: Initial volume

    Returns:
        AudioPlaybackContext if available, None otherwise
    """
    if not HAS_SOUNDDEVICE:
        logger.warning("Audio playback not available (sounddevice not installed)")
        return None

    return AudioPlaybackContext(context, device=device, volume=volume)
