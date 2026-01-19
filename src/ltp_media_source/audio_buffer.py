"""Thread-safe ring buffer for audio samples.

Used by SharedMediaContext to store decoded audio samples that can be
read by multiple audio visualizer sources.
"""

import threading
import numpy as np


class AudioRingBuffer:
    """Thread-safe ring buffer for audio samples.

    Stores audio samples in a circular buffer that can be written to
    by the media decoder and read from by audio analyzers.

    Samples are stored as float32 in the range [-1.0, 1.0].
    """

    def __init__(
        self,
        duration_sec: float = 2.0,
        sample_rate: int = 44100,
        channels: int = 2,
    ):
        """Initialize the audio ring buffer.

        Args:
            duration_sec: How many seconds of audio to buffer
            sample_rate: Audio sample rate (e.g., 44100)
            channels: Number of audio channels (1=mono, 2=stereo)
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self._duration_sec = duration_sec

        # Calculate buffer size
        self._buffer_samples = int(duration_sec * sample_rate)

        # Buffer storage: shape (buffer_samples, channels)
        self._buffer = np.zeros((self._buffer_samples, channels), dtype=np.float32)

        # Write position (next sample to be written)
        self._write_pos = 0

        # Total samples written (for tracking timeline)
        self._total_written = 0

        # Thread safety
        self._lock = threading.Lock()

    @property
    def buffer_samples(self) -> int:
        """Total number of samples the buffer can hold."""
        return self._buffer_samples

    @property
    def total_written(self) -> int:
        """Total samples written since creation/reset."""
        with self._lock:
            return self._total_written

    def write(self, samples: np.ndarray) -> None:
        """Write samples to the buffer.

        Args:
            samples: Audio samples, shape (num_samples,) for mono or
                    (num_samples, channels) for multi-channel.
                    Values should be float32 in range [-1.0, 1.0].
        """
        if samples.size == 0:
            return

        # Ensure 2D shape (samples, channels)
        if samples.ndim == 1:
            samples = samples.reshape(-1, 1)
            if self.channels == 2:
                # Duplicate mono to stereo
                samples = np.column_stack([samples, samples])

        # Handle channel mismatch
        if samples.shape[1] != self.channels:
            if samples.shape[1] > self.channels:
                # Downmix: take first N channels
                samples = samples[:, : self.channels]
            else:
                # Upmix: duplicate last channel
                extra = np.tile(
                    samples[:, -1:], (1, self.channels - samples.shape[1])
                )
                samples = np.column_stack([samples, extra])

        num_samples = samples.shape[0]

        with self._lock:
            if num_samples >= self._buffer_samples:
                # Input larger than buffer - just keep the last buffer_samples
                self._buffer[:] = samples[-self._buffer_samples :]
                self._write_pos = 0
                self._total_written += num_samples
            else:
                # Calculate write positions
                end_pos = self._write_pos + num_samples

                if end_pos <= self._buffer_samples:
                    # Simple case: no wrap
                    self._buffer[self._write_pos : end_pos] = samples
                else:
                    # Wrap around
                    first_part = self._buffer_samples - self._write_pos
                    self._buffer[self._write_pos :] = samples[:first_part]
                    self._buffer[: num_samples - first_part] = samples[first_part:]

                self._write_pos = end_pos % self._buffer_samples
                self._total_written += num_samples

    def read_recent(self, num_samples: int) -> np.ndarray:
        """Read the most recent samples from the buffer.

        Args:
            num_samples: Number of samples to read

        Returns:
            Audio samples, shape (num_samples, channels), float32.
            Returns zeros if not enough samples have been written.
        """
        num_samples = min(num_samples, self._buffer_samples)

        with self._lock:
            if self._total_written < num_samples:
                # Not enough data yet - return what we have, padded with zeros
                result = np.zeros((num_samples, self.channels), dtype=np.float32)
                if self._total_written > 0:
                    available = min(self._total_written, self._buffer_samples)
                    start = max(0, self._write_pos - available)
                    if start < self._write_pos:
                        result[-available:] = self._buffer[start : self._write_pos]
                return result

            # Calculate read start position
            start_pos = (self._write_pos - num_samples) % self._buffer_samples

            if start_pos + num_samples <= self._buffer_samples:
                # Simple case: no wrap
                return self._buffer[start_pos : start_pos + num_samples].copy()
            else:
                # Wrap around
                first_part = self._buffer_samples - start_pos
                result = np.empty((num_samples, self.channels), dtype=np.float32)
                result[:first_part] = self._buffer[start_pos:]
                result[first_part:] = self._buffer[: num_samples - first_part]
                return result

    def read_at_time(self, time_sec: float, num_samples: int) -> np.ndarray:
        """Read samples at a specific time position.

        This is approximate - it calculates the sample offset from the
        current write position based on time difference.

        Args:
            time_sec: Time in seconds (from start of media)
            num_samples: Number of samples to read

        Returns:
            Audio samples, shape (num_samples, channels), float32.
        """
        # Convert time to sample position
        target_sample = int(time_sec * self.sample_rate)

        with self._lock:
            # How far back from current position?
            samples_ago = self._total_written - target_sample

            if samples_ago < 0:
                # Requested time is in the future - return zeros
                return np.zeros((num_samples, self.channels), dtype=np.float32)

            if samples_ago >= self._buffer_samples:
                # Requested time is too far in the past - return oldest available
                samples_ago = self._buffer_samples - 1

            # Calculate read start position
            start_pos = (self._write_pos - samples_ago - 1) % self._buffer_samples

            # Clamp num_samples to available data
            available_forward = min(num_samples, samples_ago + 1)

            if start_pos + available_forward <= self._buffer_samples:
                result = self._buffer[start_pos : start_pos + available_forward].copy()
            else:
                first_part = self._buffer_samples - start_pos
                result = np.empty((available_forward, self.channels), dtype=np.float32)
                result[:first_part] = self._buffer[start_pos:]
                result[first_part:] = self._buffer[: available_forward - first_part]

            # Pad with zeros if we need more samples than available
            if available_forward < num_samples:
                padded = np.zeros((num_samples, self.channels), dtype=np.float32)
                padded[:available_forward] = result
                return padded

            return result

    def clear(self) -> None:
        """Clear the buffer and reset write position."""
        with self._lock:
            self._buffer.fill(0)
            self._write_pos = 0
            self._total_written = 0

    def get_mono(self, samples: np.ndarray) -> np.ndarray:
        """Convert stereo samples to mono by averaging channels.

        Args:
            samples: Audio samples, shape (num_samples, channels)

        Returns:
            Mono samples, shape (num_samples,)
        """
        if samples.shape[1] == 1:
            return samples[:, 0]
        return samples.mean(axis=1)
