"""Direct sink control for fills and painting without routes."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from libltp import (
    ControlClient,
    DataSender,
    stream_control,
    stream_setup,
)
from libltp.types import ColorFormat, Encoding, StreamAction

from ltp_controller.controller import Controller, DeviceState

logger = logging.getLogger(__name__)


@dataclass
class SinkStream:
    """Active stream to a sink."""

    sink_id: str
    client: ControlClient
    sender: DataSender
    stream_id: str
    udp_port: int
    pixel_count: int


class SinkController:
    """Manages direct sink control without routes.

    Allows filling sinks with solid colors, gradients, or section-based
    patterns without requiring a source device.
    """

    def __init__(self, controller: Controller):
        self.controller = controller
        self._streams: dict[str, SinkStream] = {}
        self._lock = asyncio.Lock()

    async def _get_or_create_stream(self, sink: DeviceState) -> SinkStream:
        """Get existing stream or create new one to sink."""
        sink_id = sink.id

        if sink_id in self._streams:
            stream = self._streams[sink_id]
            # Verify stream is still valid
            try:
                # Quick check - if client is closed, recreate
                if stream.client._writer is None or stream.client._writer.is_closing():
                    raise ConnectionError("Stream closed")
                return stream
            except Exception:
                # Clean up old stream
                await self._cleanup_stream(sink_id)

        # Create new stream
        client = ControlClient(sink.host, sink.port)
        await client.connect()

        # Set up stream
        setup_req = stream_setup(0, ColorFormat.RGB, Encoding.RAW)
        setup_resp = await client.request(setup_req)

        if setup_resp.data.get("status") != "ok":
            await client.close()
            raise ValueError(f"Stream setup failed: {setup_resp.data}")

        udp_port = setup_resp.data["udp_port"]
        stream_id = setup_resp.data["stream_id"]

        # Start sender
        sender = DataSender(sink.host, udp_port)
        await sender.start()

        # Start stream
        start_req = stream_control(0, stream_id, StreamAction.START)
        await client.request(start_req)

        # Get pixel count from sink
        pixel_count = self._get_pixel_count(sink)

        stream = SinkStream(
            sink_id=sink_id,
            client=client,
            sender=sender,
            stream_id=stream_id,
            udp_port=udp_port,
            pixel_count=pixel_count,
        )
        self._streams[sink_id] = stream

        logger.info(f"Created stream to sink {sink.name} ({pixel_count} pixels)")
        return stream

    async def _cleanup_stream(self, sink_id: str) -> None:
        """Clean up a stream."""
        stream = self._streams.pop(sink_id, None)
        if not stream:
            return

        try:
            # Stop stream
            stop_req = stream_control(0, stream.stream_id, StreamAction.STOP)
            await stream.client.request(stop_req, timeout=2.0)
        except Exception:
            pass

        try:
            await stream.sender.stop()
        except Exception:
            pass

        try:
            await stream.client.close()
        except Exception:
            pass

        logger.info(f"Cleaned up stream to sink {sink_id}")

    def _get_pixel_count(self, sink: DeviceState) -> int:
        """Get pixel count from sink device."""
        props = sink.device.properties

        if "pixels" in props:
            return int(props["pixels"])

        if "dim" in props:
            dims = [int(d) for d in props["dim"].split("x")]
            return int(np.prod(dims))

        # Check capabilities
        if sink.capabilities and "pixels" in sink.capabilities:
            return sink.capabilities["pixels"]

        # Default
        return 60

    async def fill_solid(
        self, sink_id: str, color: tuple[int, int, int]
    ) -> dict[str, Any]:
        """Fill entire sink with a solid color.

        Args:
            sink_id: Sink device ID
            color: RGB color tuple (0-255 each)

        Returns:
            Status dict with success/error info
        """
        sink = self.controller.get_sink(sink_id)
        if not sink:
            return {"status": "error", "message": "Sink not found"}

        if not sink.online:
            return {"status": "error", "message": "Sink is offline"}

        try:
            async with self._lock:
                stream = await self._get_or_create_stream(sink)

                # Create solid color buffer
                pixels = np.full(
                    (stream.pixel_count, 3),
                    color,
                    dtype=np.uint8
                )

                # Send frame
                stream.sender.send(pixels, ColorFormat.RGB, Encoding.RAW)

            logger.info(f"Filled sink {sink.name} with color {color}")
            return {"status": "ok", "pixels": stream.pixel_count}

        except Exception as e:
            logger.error(f"Error filling sink {sink_id}: {e}")
            return {"status": "error", "message": str(e)}

    async def fill_gradient(
        self,
        sink_id: str,
        colors: list[tuple[int, int, int]],
    ) -> dict[str, Any]:
        """Fill sink with a gradient between colors.

        Args:
            sink_id: Sink device ID
            colors: List of RGB color tuples for gradient stops

        Returns:
            Status dict with success/error info
        """
        if len(colors) < 2:
            return {"status": "error", "message": "At least 2 colors required"}

        sink = self.controller.get_sink(sink_id)
        if not sink:
            return {"status": "error", "message": "Sink not found"}

        if not sink.online:
            return {"status": "error", "message": "Sink is offline"}

        try:
            async with self._lock:
                stream = await self._get_or_create_stream(sink)

                # Create gradient buffer
                pixels = np.zeros((stream.pixel_count, 3), dtype=np.uint8)

                # Calculate gradient
                num_segments = len(colors) - 1
                segment_length = stream.pixel_count / num_segments

                for i in range(stream.pixel_count):
                    # Find which segment we're in
                    segment = min(int(i / segment_length), num_segments - 1)
                    segment_pos = (i - segment * segment_length) / segment_length

                    # Interpolate between colors
                    c1 = np.array(colors[segment])
                    c2 = np.array(colors[segment + 1])
                    pixels[i] = (c1 * (1 - segment_pos) + c2 * segment_pos).astype(
                        np.uint8
                    )

                # Send frame
                stream.sender.send(pixels, ColorFormat.RGB, Encoding.RAW)

            logger.info(f"Filled sink {sink.name} with gradient ({len(colors)} colors)")
            return {"status": "ok", "pixels": stream.pixel_count}

        except Exception as e:
            logger.error(f"Error filling sink {sink_id}: {e}")
            return {"status": "error", "message": str(e)}

    async def fill_sections(
        self,
        sink_id: str,
        sections: list[dict[str, Any]],
        background: tuple[int, int, int] = (0, 0, 0),
    ) -> dict[str, Any]:
        """Fill specific sections of a sink with colors.

        Args:
            sink_id: Sink device ID
            sections: List of section dicts with "start", "end", "color"
            background: Background color for unfilled areas

        Returns:
            Status dict with success/error info
        """
        sink = self.controller.get_sink(sink_id)
        if not sink:
            return {"status": "error", "message": "Sink not found"}

        if not sink.online:
            return {"status": "error", "message": "Sink is offline"}

        try:
            async with self._lock:
                stream = await self._get_or_create_stream(sink)

                # Create buffer with background
                pixels = np.full(
                    (stream.pixel_count, 3),
                    background,
                    dtype=np.uint8
                )

                # Fill sections
                for section in sections:
                    start = max(0, int(section.get("start", 0)))
                    end = min(stream.pixel_count, int(section.get("end", stream.pixel_count)))
                    color = section.get("color", [255, 255, 255])

                    if start < end:
                        pixels[start:end] = color

                # Send frame
                stream.sender.send(pixels, ColorFormat.RGB, Encoding.RAW)

            logger.info(f"Filled sink {sink.name} with {len(sections)} sections")
            return {"status": "ok", "pixels": stream.pixel_count, "sections": len(sections)}

        except Exception as e:
            logger.error(f"Error filling sink {sink_id}: {e}")
            return {"status": "error", "message": str(e)}

    async def clear(self, sink_id: str) -> dict[str, Any]:
        """Clear sink (fill with black).

        Args:
            sink_id: Sink device ID

        Returns:
            Status dict with success/error info
        """
        return await self.fill_solid(sink_id, (0, 0, 0))

    async def set_pixel(
        self,
        sink_id: str,
        index: int,
        color: tuple[int, int, int],
    ) -> dict[str, Any]:
        """Set a single pixel on a sink.

        Note: This reads current state if available, or starts with black.

        Args:
            sink_id: Sink device ID
            index: Pixel index
            color: RGB color tuple

        Returns:
            Status dict with success/error info
        """
        return await self.fill_sections(sink_id, [{"start": index, "end": index + 1, "color": color}])

    async def cleanup_all(self) -> None:
        """Clean up all active streams."""
        for sink_id in list(self._streams.keys()):
            await self._cleanup_stream(sink_id)
