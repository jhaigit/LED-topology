"""Art-Net UDP Sender.

Sends ArtDmx packets to Art-Net devices over UDP.
Handles multi-universe splitting and sync.
"""

import asyncio
import logging
import socket
from dataclasses import dataclass, field

import numpy as np

from ltp_artnet.protocol import (
    ARTNET_PORT,
    build_artdmx,
    build_artsync,
    pixels_to_universes,
)

logger = logging.getLogger(__name__)


@dataclass
class ArtNetTarget:
    """Target Art-Net device configuration."""

    host: str
    port: int = ARTNET_PORT
    start_universe: int = 0
    pixel_offset: int = 0  # Offset into source pixel buffer


@dataclass
class ArtNetSenderConfig:
    """Configuration for Art-Net sender."""

    # Target device(s)
    targets: list[ArtNetTarget] = field(default_factory=list)

    # Default target (if no targets specified)
    host: str = "255.255.255.255"  # Broadcast by default
    port: int = ARTNET_PORT
    start_universe: int = 0

    # Pixel configuration
    pixels: int = 170  # Pixels per target
    bytes_per_pixel: int = 3  # RGB=3, RGBW=4

    # Sync options
    enable_sync: bool = False  # Send ArtSync after all universes

    # Rate limiting
    max_fps: int = 44  # Art-Net spec recommends max 44fps


class ArtNetSender:
    """Sends pixel data to Art-Net devices.

    Handles:
    - Splitting pixel data across multiple universes
    - Multiple target devices
    - Optional ArtSync for frame synchronization
    - Sequence number management
    """

    def __init__(self, config: ArtNetSenderConfig | None = None):
        self.config = config or ArtNetSenderConfig()

        self._socket: socket.socket | None = None
        self._sequence: int = 1  # 0 = disabled, 1-255 wrapping
        self._running = False

        # Statistics
        self._packets_sent = 0
        self._bytes_sent = 0
        self._frames_sent = 0

        # Rate limiting
        self._min_frame_interval = 1.0 / self.config.max_fps
        self._last_frame_time = 0.0

    def open(self) -> None:
        """Open UDP socket for sending."""
        if self._socket is not None:
            return

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Enable broadcast
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # Don't block
        self._socket.setblocking(False)

        self._running = True
        logger.info("Art-Net sender socket opened")

    def close(self) -> None:
        """Close UDP socket."""
        self._running = False
        if self._socket:
            self._socket.close()
            self._socket = None
        logger.info(
            f"Art-Net sender closed. Stats: {self._frames_sent} frames, "
            f"{self._packets_sent} packets, {self._bytes_sent} bytes"
        )

    def _get_targets(self) -> list[ArtNetTarget]:
        """Get list of targets to send to."""
        if self.config.targets:
            return self.config.targets
        else:
            # Use default target
            return [
                ArtNetTarget(
                    host=self.config.host,
                    port=self.config.port,
                    start_universe=self.config.start_universe,
                )
            ]

    def send_pixels(self, pixels: np.ndarray) -> int:
        """Send pixel data to all Art-Net targets.

        Args:
            pixels: Numpy array of shape (N, 3) for RGB or (N, 4) for RGBW

        Returns:
            Number of bytes sent
        """
        if self._socket is None:
            raise RuntimeError("Socket not open. Call open() first.")

        if len(pixels) == 0:
            return 0

        bytes_per_pixel = pixels.shape[1] if len(pixels.shape) > 1 else 3
        total_bytes = 0

        for target in self._get_targets():
            # Get pixel slice for this target
            start_idx = target.pixel_offset
            end_idx = start_idx + self.config.pixels
            target_pixels = pixels[start_idx:end_idx]

            if len(target_pixels) == 0:
                continue

            # Convert to flat bytes (RGB or RGBW order)
            pixel_bytes = target_pixels.flatten().tobytes()

            # Calculate universes needed
            num_universes = pixels_to_universes(len(target_pixels), bytes_per_pixel)

            # Send each universe
            for uni_idx in range(num_universes):
                universe = target.start_universe + uni_idx

                # Calculate byte range for this universe
                start_byte = uni_idx * 512
                end_byte = min(start_byte + 512, len(pixel_bytes))
                dmx_data = pixel_bytes[start_byte:end_byte]

                # Pad to even length if needed
                if len(dmx_data) % 2 != 0:
                    dmx_data = dmx_data + b"\x00"

                # Build and send packet
                packet = build_artdmx(
                    universe=universe,
                    data=dmx_data,
                    sequence=self._sequence,
                )

                try:
                    sent = self._socket.sendto(packet, (target.host, target.port))
                    total_bytes += sent
                    self._packets_sent += 1
                    self._bytes_sent += sent

                    logger.debug(
                        f"Sent universe {universe} to {target.host}:{target.port}, "
                        f"{len(dmx_data)} channels"
                    )
                except Exception as e:
                    logger.error(f"Failed to send to {target.host}:{target.port}: {e}")

        # Send sync if enabled
        if self.config.enable_sync:
            sync_packet = build_artsync()
            for target in self._get_targets():
                try:
                    self._socket.sendto(sync_packet, (target.host, target.port))
                    self._packets_sent += 1
                except Exception as e:
                    logger.debug(f"Failed to send sync to {target.host}: {e}")

        # Update sequence (1-255 wrapping)
        self._sequence = (self._sequence % 255) + 1

        self._frames_sent += 1
        return total_bytes

    async def send_pixels_async(self, pixels: np.ndarray) -> int:
        """Async version of send_pixels with rate limiting.

        Args:
            pixels: Numpy array of pixel data

        Returns:
            Number of bytes sent
        """
        import time

        # Rate limiting
        now = time.monotonic()
        elapsed = now - self._last_frame_time
        if elapsed < self._min_frame_interval:
            await asyncio.sleep(self._min_frame_interval - elapsed)

        self._last_frame_time = time.monotonic()

        # Run sync send in executor to avoid blocking
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.send_pixels, pixels)

    def send_blackout(self) -> None:
        """Send all-black frame to all universes."""
        if self._socket is None:
            return

        for target in self._get_targets():
            num_universes = pixels_to_universes(
                self.config.pixels, self.config.bytes_per_pixel
            )

            for uni_idx in range(num_universes):
                universe = target.start_universe + uni_idx
                packet = build_artdmx(
                    universe=universe,
                    data=bytes(512),
                    sequence=self._sequence,
                )

                try:
                    self._socket.sendto(packet, (target.host, target.port))
                except Exception as e:
                    logger.debug(f"Failed to send blackout: {e}")

        self._sequence = (self._sequence % 255) + 1

    def get_stats(self) -> dict:
        """Get sender statistics."""
        return {
            "running": self._running,
            "frames_sent": self._frames_sent,
            "packets_sent": self._packets_sent,
            "bytes_sent": self._bytes_sent,
            "sequence": self._sequence,
        }

    @property
    def is_open(self) -> bool:
        return self._socket is not None
