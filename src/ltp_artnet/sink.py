"""LTP Art-Net Sink.

LTP sink that outputs to Art-Net devices (WLED, commercial controllers).
Receives LTP data and sends it as Art-Net UDP packets.
"""

import asyncio
import logging
import socket
from typing import Any
from uuid import UUID, uuid4

import numpy as np
from pydantic import BaseModel, Field

from libltp import (
    BooleanControl,
    ColorFormat,
    ControlRegistry,
    DataPacket,
    DeviceType,
    Message,
    MessageType,
    NumberControl,
    SinkAdvertiser,
    capability_response,
    control_get_response,
    control_set_response,
    create_linear_topology,
    stream_setup_response,
)
from libltp.types import StreamAction
from libltp.transport import ControlServer, DataReceiver, StreamManager

from ltp_artnet.sender import ArtNetSender, ArtNetSenderConfig, ArtNetTarget
from ltp_artnet.protocol import ARTNET_PORT, pixels_to_universes

logger = logging.getLogger(__name__)


class ArtNetSinkConfig(BaseModel):
    """Configuration for Art-Net LTP sink."""

    # Device identity
    device_id: UUID = Field(default_factory=uuid4)
    name: str = "Art-Net Output"
    description: str = ""

    # Display configuration
    device_type: DeviceType = DeviceType.STRING
    pixels: int = 170  # Default to 1 universe RGB
    dimensions: list[int] = Field(default_factory=list)
    color_format: ColorFormat = ColorFormat.RGB
    max_refresh_hz: int = 44  # Art-Net max recommended

    # LTP Network
    control_port: int = 0  # 0 = auto
    data_port: int = 0

    # Art-Net output
    artnet_host: str = "255.255.255.255"  # Broadcast by default
    artnet_port: int = ARTNET_PORT
    start_universe: int = 0
    enable_sync: bool = False

    # Multiple targets (optional)
    targets: list[dict] = Field(default_factory=list)

    # Discovery
    enable_artpoll: bool = True  # Respond to ArtPoll requests

    model_config = {"arbitrary_types_allowed": True}


class ArtNetSink:
    """LTP Sink with Art-Net output.

    This sink:
    - Receives LED data via LTP network protocol
    - Outputs to Art-Net devices via UDP
    - Supports multiple universes and targets
    - Optionally responds to ArtPoll discovery
    """

    def __init__(self, config: ArtNetSinkConfig | None = None):
        self.config = config or ArtNetSinkConfig()

        # Network components
        self._advertiser: SinkAdvertiser | None = None
        self._control_server: ControlServer | None = None
        self._data_receiver: DataReceiver | None = None
        self._stream_manager = StreamManager()

        # Art-Net sender
        sender_config = ArtNetSenderConfig(
            host=self.config.artnet_host,
            port=self.config.artnet_port,
            start_universe=self.config.start_universe,
            pixels=self.config.pixels,
            bytes_per_pixel=self.config.color_format.bytes_per_pixel,
            enable_sync=self.config.enable_sync,
            max_fps=self.config.max_refresh_hz,
            targets=[
                ArtNetTarget(
                    host=t.get("host", self.config.artnet_host),
                    port=t.get("port", ARTNET_PORT),
                    start_universe=t.get("start_universe", 0),
                    pixel_offset=t.get("pixel_offset", 0),
                )
                for t in self.config.targets
            ] if self.config.targets else [],
        )
        self._sender = ArtNetSender(sender_config)

        # Controls registry
        self._controls = ControlRegistry()
        self._setup_controls()

        # Configuration
        self._pixel_count = self.config.pixels
        self._dimensions = self.config.dimensions.copy() if self.config.dimensions else [self._pixel_count]
        self._topology = create_linear_topology(self._pixel_count)

        # State
        self._running = False
        self._pixel_buffer: np.ndarray | None = None

        # ArtPoll listener
        self._artpoll_socket: socket.socket | None = None
        self._artpoll_task: asyncio.Task | None = None

        # Statistics
        self._packet_count = 0
        self._packet_bytes = 0
        self._frames_sent = 0
        self._last_stats_time = 0.0
        self._stats_task: asyncio.Task | None = None

    def _setup_controls(self) -> None:
        """Set up sink controls."""
        self._controls.register(
            BooleanControl(
                id="enabled",
                name="Output Enabled",
                description="Enable Art-Net output",
                value=True,
                group="general",
            )
        )

        self._controls.register(
            NumberControl(
                id="brightness",
                name="Brightness",
                description="Output brightness (0-255)",
                value=255.0,
                min=0.0,
                max=255.0,
                step=1.0,
                group="general",
            )
        )

    def _handle_message(self, message: Message) -> Message | None:
        """Handle incoming control channel messages."""
        logger.debug(f"Handling message: {message.type}")

        if message.type == MessageType.CAPABILITY_REQUEST:
            return self._handle_capability_request(message)
        elif message.type == MessageType.STREAM_SETUP:
            return self._handle_stream_setup(message)
        elif message.type == MessageType.STREAM_CONTROL:
            return self._handle_stream_control(message)
        elif message.type == MessageType.CONTROL_GET:
            return self._handle_control_get(message)
        elif message.type == MessageType.CONTROL_SET:
            return self._handle_control_set(message)

        return None

    def _handle_capability_request(self, message: Message) -> Message:
        """Handle capability request."""
        from libltp.topology import TopologyMapper

        topology_dict = {}
        if self._topology:
            mapper = TopologyMapper(self._topology)
            topology_dict = mapper.to_dict()

        num_universes = pixels_to_universes(
            self._pixel_count, self.config.color_format.bytes_per_pixel
        )

        device_info = {
            "id": str(self.config.device_id),
            "name": self.config.name,
            "description": self.config.description,
            "type": self.config.device_type.value,
            "pixels": self._pixel_count,
            "dimensions": self._dimensions,
            "topology": topology_dict,
            "color_formats": [self.config.color_format.name.lower()],
            "max_refresh_hz": self.config.max_refresh_hz,
            "protocol_version": "0.1",
            "controls": self._controls.to_list(),
            "backend": {
                "type": "artnet",
                "host": self.config.artnet_host,
                "port": self.config.artnet_port,
                "start_universe": self.config.start_universe,
                "universes": num_universes,
                "enable_sync": self.config.enable_sync,
            },
        }

        return capability_response(message.seq, device_info)

    def _handle_stream_setup(self, message: Message) -> Message:
        """Handle stream setup request."""
        if self._data_receiver is None:
            logger.warning("Data receiver not initialized")

        # Create stream
        stream_id = self._stream_manager.create_stream(
            receiver=self._data_receiver,
            color_format=self.config.color_format,
        )
        self._stream_manager.start_stream(stream_id)

        return stream_setup_response(
            message.seq,
            status="ok",
            udp_port=self._data_receiver.actual_port if self._data_receiver else 0,
            stream_id=stream_id,
        )

    def _handle_stream_control(self, message: Message) -> Message:
        """Handle stream control request."""
        stream_id = message.data.get("stream_id")
        action_str = message.data.get("action", "start")
        logger.info(f"STREAM_CONTROL: stream_id={stream_id}, action={action_str}")
        action = StreamAction(action_str)

        if action == StreamAction.START:
            self._stream_manager.start_stream(stream_id)
            logger.info(f"Started stream: {stream_id}")
        elif action == StreamAction.STOP:
            self._stream_manager.stop_stream(stream_id)
            self._sender.send_blackout()
            logger.info(f"Stopped stream: {stream_id}, sent blackout")
        elif action == StreamAction.PAUSE:
            logger.info(f"Paused stream: {stream_id}")

        return Message(
            MessageType.STREAM_CONTROL_RESPONSE,
            message.seq,
            status="ok",
            stream_id=stream_id,
        )

    def _handle_control_get(self, message: Message) -> Message:
        """Handle control get request."""
        ids = message.data.get("ids")
        values = self._controls.get_values(ids)
        return control_get_response(message.seq, "ok", values)

    def _handle_control_set(self, message: Message) -> Message:
        """Handle control set request."""
        values = message.data.get("values", {})
        applied = {}
        errors = {}

        for control_id, value in values.items():
            try:
                self._controls.set_value(control_id, value)
                applied[control_id] = self._controls.get_value(control_id)
            except Exception as e:
                errors[control_id] = str(e)

        status = "ok" if not errors else "partial"
        return control_set_response(message.seq, status, applied, errors or None)

    def _handle_data_packet(self, packet: DataPacket) -> None:
        """Handle incoming data packet."""
        from libltp import scale_buffer

        # Only process if there's an active stream
        if not self._stream_manager.active_streams:
            logger.debug("Ignoring data packet - no active streams")
            return

        # Check if output is enabled
        if not self._controls.get_value("enabled"):
            return

        if self._pixel_buffer is None:
            return

        # Update statistics
        pixel_count = packet.pixel_count
        packet_bytes = len(packet.pixel_data) * packet.pixel_data.itemsize if hasattr(packet.pixel_data, 'itemsize') else len(packet.pixel_data)
        self._packet_count += 1
        self._packet_bytes += packet_bytes

        logger.debug(
            f"Data packet #{self._packet_count}: {pixel_count} pixels, "
            f"{packet_bytes} bytes, format={packet.color_format.name}"
        )

        # Scale if pixel count differs
        incoming_pixels = packet.pixel_data
        if len(incoming_pixels) != self._pixel_count:
            if self._packet_count == 1:
                logger.info(
                    f"Scaling incoming data: {len(incoming_pixels)} -> {self._pixel_count} pixels"
                )
            incoming_pixels = scale_buffer(incoming_pixels, (self._pixel_count,), mode="fit")

        # Apply brightness
        brightness = int(self._controls.get_value("brightness"))
        if brightness < 255:
            pixels = (incoming_pixels.astype(np.uint16) * brightness) // 255
            pixels = pixels.astype(np.uint8)
        else:
            pixels = incoming_pixels

        # Store and send
        self._pixel_buffer[:] = pixels

        try:
            bytes_sent = self._sender.send_pixels(pixels)
            self._frames_sent += 1
            logger.debug(f"Sent frame {self._frames_sent}, {bytes_sent} bytes")
        except Exception as e:
            logger.error(f"Error sending Art-Net: {e}")

    async def _artpoll_listener(self) -> None:
        """Listen for ArtPoll requests and respond."""
        from ltp_artnet.protocol import (
            build_artpoll_reply,
            parse_artnet_packet,
            ArtPollPacket,
        )

        # Create UDP socket for ArtPoll
        self._artpoll_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._artpoll_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._artpoll_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._artpoll_socket.setblocking(False)

        try:
            self._artpoll_socket.bind(("", ARTNET_PORT))
            logger.info(f"ArtPoll listener bound to port {ARTNET_PORT}")
        except OSError as e:
            logger.warning(f"Could not bind to Art-Net port {ARTNET_PORT}: {e}")
            logger.info("ArtPoll responses disabled (port in use)")
            return

        loop = asyncio.get_event_loop()

        while self._running:
            try:
                # Non-blocking receive
                data, addr = await loop.run_in_executor(
                    None, lambda: self._artpoll_socket.recvfrom(1024)
                )

                packet = parse_artnet_packet(data)

                if isinstance(packet, ArtPollPacket):
                    logger.debug(f"Received ArtPoll from {addr}")

                    # Get local IP
                    local_ip = self._get_local_ip()
                    ip_tuple = tuple(int(x) for x in local_ip.split("."))

                    # Calculate universes
                    num_universes = pixels_to_universes(
                        self._pixel_count, self.config.color_format.bytes_per_pixel
                    )
                    universes = [
                        self.config.start_universe + i for i in range(min(num_universes, 4))
                    ]

                    # Build and send reply
                    reply = build_artpoll_reply(
                        ip_address=ip_tuple,
                        short_name=self.config.name[:17],
                        long_name=f"LTP Art-Net Sink: {self.config.name}"[:63],
                        universes=universes,
                    )

                    self._artpoll_socket.sendto(reply, addr)
                    logger.debug(f"Sent ArtPollReply to {addr}")

            except BlockingIOError:
                await asyncio.sleep(0.1)
            except Exception as e:
                if self._running:
                    logger.debug(f"ArtPoll listener error: {e}")
                await asyncio.sleep(0.1)

    def _get_local_ip(self) -> str:
        """Get local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    async def _stats_monitor(self) -> None:
        """Periodically log statistics."""
        import time

        self._last_stats_time = time.time()
        last_packets = 0
        last_bytes = 0
        stats_interval = 5.0

        while self._running:
            await asyncio.sleep(stats_interval)

            now = time.time()
            elapsed = now - self._last_stats_time

            if elapsed > 0:
                packets_delta = self._packet_count - last_packets
                bytes_delta = self._packet_bytes - last_bytes

                packets_per_sec = packets_delta / elapsed
                bytes_per_sec = bytes_delta / elapsed

                if bytes_per_sec > 1024:
                    rate_str = f"{bytes_per_sec / 1024:.2f} KB/s"
                else:
                    rate_str = f"{bytes_per_sec:.0f} B/s"

                if packets_delta > 0:
                    sender_stats = self._sender.get_stats()
                    logger.info(
                        f"Stats: {packets_per_sec:.1f} packets/s, {rate_str}, "
                        f"Art-Net frames: {sender_stats['frames_sent']}"
                    )

                self._last_stats_time = now
                last_packets = self._packet_count
                last_bytes = self._packet_bytes

    async def start(self) -> None:
        """Start the Art-Net sink."""
        if self._running:
            return

        logger.info(f"Starting Art-Net sink: {self.config.name}")
        logger.info(
            f"Art-Net target: {self.config.artnet_host}:{self.config.artnet_port}, "
            f"universe {self.config.start_universe}, {self._pixel_count} pixels"
        )

        # Initialize pixel buffer
        self._pixel_buffer = np.zeros(
            (self._pixel_count, self.config.color_format.bytes_per_pixel),
            dtype=np.uint8,
        )

        # Open Art-Net sender
        self._sender.open()

        # Start data receiver
        self._data_receiver = DataReceiver(port=self.config.data_port)
        self._data_receiver.handler = self._handle_data_packet
        await self._data_receiver.start()

        # Start control server
        self._control_server = ControlServer(
            port=self.config.control_port, handler=self._handle_message
        )
        await self._control_server.start()

        # Start mDNS advertisement
        self._advertiser = SinkAdvertiser(
            name=self.config.name.lower().replace(" ", "-"),
            port=self._control_server.actual_port,
            device_id=self.config.device_id,
            display_name=self.config.name,
            description=self.config.description,
            device_type=self.config.device_type,
            pixels=self._pixel_count,
            dimensions=self._dimensions,
            color_format=self.config.color_format,
            max_rate=self.config.max_refresh_hz,
            has_controls=True,
        )
        await self._advertiser.start()

        self._running = True

        # Start ArtPoll listener
        if self.config.enable_artpoll:
            self._artpoll_task = asyncio.create_task(self._artpoll_listener())

        # Start stats monitor
        self._stats_task = asyncio.create_task(self._stats_monitor())

        num_universes = pixels_to_universes(
            self._pixel_count, self.config.color_format.bytes_per_pixel
        )

        logger.info(
            f"Art-Net sink started - Control: {self._control_server.actual_port}, "
            f"Data: {self._data_receiver.actual_port}, "
            f"Universes: {self.config.start_universe}-{self.config.start_universe + num_universes - 1}"
        )

    async def stop(self) -> None:
        """Stop the Art-Net sink."""
        if not self._running:
            return

        logger.info("Stopping Art-Net sink")
        self._running = False

        # Cancel tasks
        if self._artpoll_task:
            self._artpoll_task.cancel()
            try:
                await self._artpoll_task
            except asyncio.CancelledError:
                pass
            self._artpoll_task = None

        if self._stats_task:
            self._stats_task.cancel()
            try:
                await self._stats_task
            except asyncio.CancelledError:
                pass
            self._stats_task = None

        # Close ArtPoll socket
        if self._artpoll_socket:
            self._artpoll_socket.close()
            self._artpoll_socket = None

        # Send blackout and close sender
        self._sender.send_blackout()
        self._sender.close()

        logger.info(
            f"Final stats: {self._packet_count} packets received, "
            f"{self._frames_sent} frames sent"
        )

        if self._advertiser:
            await self._advertiser.stop()

        if self._data_receiver:
            await self._data_receiver.stop()

        if self._control_server:
            await self._control_server.stop()

        logger.info("Art-Net sink stopped")

    async def run(self) -> None:
        """Run the sink until interrupted."""
        await self.start()
        try:
            while self._running:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def control_port(self) -> int:
        return self._control_server.actual_port if self._control_server else 0

    @property
    def data_port(self) -> int:
        return self._data_receiver.actual_port if self._data_receiver else 0

    @property
    def pixel_count(self) -> int:
        return self._pixel_count

    def get_stats(self) -> dict[str, Any]:
        """Get sink statistics."""
        return {
            "running": self._running,
            "control_port": self.control_port,
            "data_port": self.data_port,
            "pixels": self._pixel_count,
            "packets_received": self._packet_count,
            "bytes_received": self._packet_bytes,
            "frames_sent": self._frames_sent,
            "sender": self._sender.get_stats(),
        }
