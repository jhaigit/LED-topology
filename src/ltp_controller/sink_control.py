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
from ltp_controller.virtual_sources.text_renderer import (
    TextRenderer,
    TextAlign,
    VerticalAlign,
    ScrollDirection,
    hex_to_rgb,
)
from ltp_controller.virtual_sources.fonts import (
    list_fonts,
    list_all_fonts,
    DEFAULT_FONT,
    is_ttf_font,
    HAS_PIL,
)

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
        self._paint_buffers: dict[str, np.ndarray] = {}
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

        # Get pixel count and dimensions from sink
        pixel_count = self._get_pixel_count(sink)
        dimensions = self._get_dimensions(sink)

        stream = SinkStream(
            sink_id=sink_id,
            client=client,
            sender=sender,
            stream_id=stream_id,
            udp_port=udp_port,
            pixel_count=pixel_count,
        )
        self._streams[sink_id] = stream

        dim_str = "x".join(str(d) for d in dimensions)
        logger.info(f"Created stream to sink {sink.name} ({dim_str}, {pixel_count} pixels)")
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

    def _get_dimensions(self, sink: DeviceState) -> list[int]:
        """Get dimensions from sink device."""
        props = sink.device.properties

        if "dim" in props:
            return [int(d) for d in props["dim"].split("x")]

        # Check capabilities
        if sink.capabilities and "dimensions" in sink.capabilities:
            return sink.capabilities["dimensions"]

        # Fall back to pixel count as 1D
        return [self._get_pixel_count(sink)]

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

    async def paint_pixels(
        self, sink_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Paint pixels directly on a sink.

        Supports multiple paint modes:
        - {"pixels": {"0": [255,0,0], "5": [0,255,0]}}  # Sparse pixel map
        - {"x": 5, "y": 2, "color": [255,0,0]}  # Single pixel by coordinate
        - {"index": 10, "color": [255,0,0]}  # Single pixel by index
        - {"range": [0, 10], "color": [255,0,0]}  # Fill a range

        Args:
            sink_id: Sink device ID
            data: Paint data with mode-specific fields

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
                pixel_count = stream.pixel_count
                dimensions = self._get_dimensions(sink)
                width = dimensions[0]

                # Get current buffer or create black one
                if sink_id not in self._paint_buffers:
                    self._paint_buffers[sink_id] = np.zeros((pixel_count, 3), dtype=np.uint8)
                pixels = self._paint_buffers[sink_id]

                # Handle different paint modes
                if "pixels" in data:
                    # Sparse pixel map: {"pixels": {"0": [r,g,b], "5": [r,g,b]}}
                    for idx_str, color in data["pixels"].items():
                        idx = int(idx_str)
                        if 0 <= idx < pixel_count:
                            pixels[idx] = color[:3]

                elif "x" in data and "y" in data and "color" in data:
                    # Coordinate mode: {"x": 5, "y": 2, "color": [r,g,b]}
                    x, y = int(data["x"]), int(data["y"])
                    idx = y * width + x
                    if 0 <= idx < pixel_count:
                        pixels[idx] = data["color"][:3]

                elif "index" in data and "color" in data:
                    # Index mode: {"index": 10, "color": [r,g,b]}
                    idx = int(data["index"])
                    if 0 <= idx < pixel_count:
                        pixels[idx] = data["color"][:3]

                elif "range" in data and "color" in data:
                    # Range mode: {"range": [0, 10], "color": [r,g,b]}
                    start, end = int(data["range"][0]), int(data["range"][1])
                    start = max(0, start)
                    end = min(pixel_count, end)
                    pixels[start:end] = data["color"][:3]

                elif "clear" in data and data["clear"]:
                    # Clear buffer
                    pixels[:] = 0

                else:
                    return {"status": "error", "message": "Unknown paint mode"}

                # Send the frame
                stream.sender.send(
                    pixels=pixels,
                    color_format=ColorFormat.RGB,
                )

                return {"status": "ok", "pixels_set": pixel_count}

        except Exception as e:
            logger.error(f"Error painting pixels: {e}")
            return {"status": "error", "message": str(e)}

    async def paint_text(
        self, sink_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Paint text on a sink.

        Renders text at a specific position or with alignment options.

        Args:
            sink_id: Sink device ID
            data: Text paint data with fields:
                - text: String to display (required)
                - x: X position (optional, default: 0)
                - y: Y position (optional, default: 0)
                - color: Text color as hex string or RGB list (optional, default: white)
                - background: Background color (optional, default: transparent/black)
                - font: Font name (optional, "5x7", "4x6", "3x5")
                - align: Horizontal alignment ("left", "center", "right")
                - vertical_align: Vertical alignment ("top", "middle", "bottom")
                - clear: If true, clear display before rendering

        Returns:
            Status dict with success/error info
        """
        sink = self.controller.get_sink(sink_id)
        if not sink:
            return {"status": "error", "message": "Sink not found"}

        if not sink.online:
            return {"status": "error", "message": "Sink is offline"}

        text = data.get("text", "")
        if not text:
            return {"status": "error", "message": "No text specified"}

        try:
            async with self._lock:
                stream = await self._get_or_create_stream(sink)
                pixel_count = stream.pixel_count
                dimensions = self._get_dimensions(sink)

                # Get dimensions
                if len(dimensions) >= 2:
                    width, height = dimensions[0], dimensions[1]
                else:
                    width = dimensions[0]
                    height = 1

                # Parse colors
                text_color = data.get("color", "#FFFFFF")
                if isinstance(text_color, str):
                    text_color = hex_to_rgb(text_color)
                else:
                    text_color = tuple(text_color[:3])

                background_color = data.get("background", "#000000")
                if isinstance(background_color, str):
                    background_color = hex_to_rgb(background_color)
                else:
                    background_color = tuple(background_color[:3])

                # Get font - supports both bitmap fonts and TTF fonts
                font = data.get("font", DEFAULT_FONT)
                ttf_size = data.get("ttf_size", 16)

                # Validate font - accept bitmap fonts or TTF fonts
                if font not in list_fonts():
                    # Check if it's a TTF font
                    if is_ttf_font(font) and HAS_PIL:
                        # Build TTF font spec with size
                        if ":" not in font:
                            font = f"{font}:{ttf_size}"
                    else:
                        font = DEFAULT_FONT

                # Get alignment
                align_str = data.get("align", "left")
                valign_str = data.get("vertical_align", "middle")

                try:
                    align = TextAlign(align_str)
                except ValueError:
                    align = TextAlign.LEFT

                try:
                    valign = VerticalAlign(valign_str)
                except ValueError:
                    valign = VerticalAlign.MIDDLE

                # Create renderer
                renderer = TextRenderer(
                    width=width,
                    height=height,
                    font_name=font,
                    text_color=text_color,
                    background_color=background_color,
                    align=align,
                    vertical_align=valign,
                )

                # Get position offsets
                x_offset = int(data.get("x", 0))
                y_offset = int(data.get("y", 0))

                # Render text
                frame = renderer.render_static(text, x_offset=x_offset, y_offset=y_offset)

                # Convert to linear and store in buffer
                pixels = renderer.to_linear(frame)

                # Handle clear or overlay modes
                if data.get("clear", True):
                    # Replace buffer entirely
                    self._paint_buffers[sink_id] = pixels.copy()
                else:
                    # Overlay: only copy non-background pixels
                    if sink_id not in self._paint_buffers:
                        self._paint_buffers[sink_id] = np.zeros((pixel_count, 3), dtype=np.uint8)
                    buffer = self._paint_buffers[sink_id]

                    # Find text pixels (non-background)
                    bg_array = np.array(background_color)
                    text_mask = ~np.all(pixels == bg_array, axis=1)
                    buffer[text_mask] = pixels[text_mask]
                    pixels = buffer

                # Send frame
                stream.sender.send(
                    pixels=pixels,
                    color_format=ColorFormat.RGB,
                )

                logger.info(f"Painted text '{text[:20]}...' on sink {sink.name}")
                return {
                    "status": "ok",
                    "text": text,
                    "dimensions": [width, height],
                    "font": font,
                }

        except Exception as e:
            logger.error(f"Error painting text: {e}")
            return {"status": "error", "message": str(e)}

    async def get_paint_info(self, sink_id: str) -> dict[str, Any]:
        """Get paint-related information for a sink.

        Args:
            sink_id: Sink device ID

        Returns:
            Dict with dimensions, pixel count, available fonts, etc.
        """
        sink = self.controller.get_sink(sink_id)
        if not sink:
            return {"status": "error", "message": "Sink not found"}

        dimensions = self._get_dimensions(sink)
        pixel_count = self._get_pixel_count(sink)

        return {
            "status": "ok",
            "sink_id": sink_id,
            "name": sink.name,
            "dimensions": dimensions,
            "width": dimensions[0] if dimensions else 0,
            "height": dimensions[1] if len(dimensions) > 1 else 1,
            "pixels": pixel_count,  # For backwards compatibility
            "type": "matrix" if len(dimensions) > 1 else "strip",  # For paint UI
            "fonts": list_all_fonts(),  # Both bitmap and TTF fonts
            "bitmap_fonts": list_fonts(),  # Just bitmap fonts
            "default_font": DEFAULT_FONT,
            "has_ttf": HAS_PIL,
            "has_buffer": sink_id in self._paint_buffers,
        }

    async def cleanup_all(self) -> None:
        """Clean up all active streams."""
        for sink_id in list(self._streams.keys()):
            await self._cleanup_stream(sink_id)
