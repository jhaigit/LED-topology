"""TCP and UDP transport for LTP protocol."""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Union

import numpy as np

from libltp.protocol import DataPacket, Message, ProtocolError
from libltp.types import ColorFormat, Encoding, ErrorCode, MAX_PACKET_SIZE

logger = logging.getLogger(__name__)


# Type aliases for callbacks
# MessageHandler can be sync or async
MessageHandler = Callable[[Message], Union[Message, None, Awaitable[Message | None]]]
DataHandler = Callable[[DataPacket], None]


class ControlConnection:
    """A single TCP control channel connection."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        handler: MessageHandler | None = None,
    ):
        self.reader = reader
        self.writer = writer
        self.handler = handler
        self._closed = False
        self._peer: tuple[str, int] | None = None

        # Get peer info
        try:
            self._peer = writer.get_extra_info("peername")
        except Exception:
            pass

    @property
    def peer(self) -> str:
        """Get peer address string."""
        if self._peer:
            return f"{self._peer[0]}:{self._peer[1]}"
        return "unknown"

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def send(self, message: Message) -> None:
        """Send a message."""
        if self._closed:
            raise ConnectionError("Connection closed")

        data = message.to_bytes()
        self.writer.write(data)
        await self.writer.drain()
        logger.debug(f"Sent to {self.peer}: {message.type.value}")

    async def receive(self) -> Message | None:
        """Receive a single message."""
        if self._closed:
            return None

        try:
            line = await self.reader.readline()
            if not line:
                return None
            return Message.from_bytes(line)
        except Exception as e:
            logger.error(f"Error receiving from {self.peer}: {e}")
            return None

    async def handle_messages(self) -> None:
        """Handle incoming messages until connection closes."""
        logger.info(f"Handling messages from {self.peer}")

        try:
            while not self._closed:
                message = await self.receive()
                if message is None:
                    break

                logger.debug(f"Received from {self.peer}: {message.type.value}")

                if self.handler:
                    try:
                        # Support both sync and async handlers
                        result = self.handler(message)
                        if asyncio.iscoroutine(result):
                            response = await result
                        else:
                            response = result
                        if response:
                            await self.send(response)
                    except Exception as e:
                        logger.error(f"Handler error: {e}")
                        # Send error response
                        error_msg = Message(
                            message.type,
                            message.seq,
                            status="error",
                            error=str(e),
                        )
                        await self.send(error_msg)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Connection error with {self.peer}: {e}")
        finally:
            await self.close()

    async def close(self) -> None:
        """Close the connection."""
        if self._closed:
            return

        self._closed = True
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass
        logger.info(f"Connection closed: {self.peer}")


class ControlServer:
    """TCP server for control channel connections."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 0,
        handler: MessageHandler | None = None,
    ):
        self.host = host
        self.port = port
        self.handler = handler

        self._server: asyncio.Server | None = None
        self._connections: list[ControlConnection] = []
        self._on_connect: Callable[[ControlConnection], None] | None = None
        self._on_disconnect: Callable[[ControlConnection], None] | None = None

    @property
    def actual_port(self) -> int:
        """Get the actual bound port."""
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self.port

    @property
    def connections(self) -> list[ControlConnection]:
        """Get active connections."""
        return [c for c in self._connections if not c.is_closed]

    def on_connect(self, callback: Callable[[ControlConnection], None]) -> None:
        """Set callback for new connections."""
        self._on_connect = callback

    def on_disconnect(self, callback: Callable[[ControlConnection], None]) -> None:
        """Set callback for disconnections."""
        self._on_disconnect = callback

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a new client connection."""
        conn = ControlConnection(reader, writer, self.handler)
        self._connections.append(conn)

        if self._on_connect:
            self._on_connect(conn)

        await conn.handle_messages()

        self._connections.remove(conn)
        if self._on_disconnect:
            self._on_disconnect(conn)

    async def start(self) -> None:
        """Start the server."""
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        logger.info(f"Control server listening on {self.host}:{self.actual_port}")

    async def stop(self) -> None:
        """Stop the server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        # Close all connections
        for conn in self._connections:
            await conn.close()
        self._connections.clear()

        logger.info("Control server stopped")

    async def broadcast(self, message: Message) -> None:
        """Send a message to all connected clients."""
        for conn in self.connections:
            try:
                await conn.send(message)
            except Exception as e:
                logger.warning(f"Broadcast to {conn.peer} failed: {e}")


class ControlClient:
    """TCP client for control channel connections."""

    def __init__(self, host: str, port: int, handler: MessageHandler | None = None):
        self.host = host
        self.port = port
        self.handler = handler

        self._connection: ControlConnection | None = None
        self._seq = 0
        self._pending: dict[int, asyncio.Future[Message]] = {}

    @property
    def is_connected(self) -> bool:
        return self._connection is not None and not self._connection.is_closed

    def _next_seq(self) -> int:
        """Get next sequence number."""
        self._seq += 1
        return self._seq

    async def connect(self) -> None:
        """Connect to the server."""
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self._connection = ControlConnection(reader, writer, self._handle_response)
        logger.info(f"Connected to {self.host}:{self.port}")

        # Start message handler
        asyncio.create_task(self._connection.handle_messages())

    def _handle_response(self, message: Message) -> None:
        """Handle incoming response messages."""
        if message.seq is not None and message.seq in self._pending:
            future = self._pending.pop(message.seq)
            future.set_result(message)
        elif self.handler:
            self.handler(message)

    async def request(self, message: Message, timeout: float = 5.0) -> Message:
        """Send a request and wait for response."""
        if not self.is_connected:
            raise ConnectionError("Not connected")

        # Set sequence number
        message.seq = self._next_seq()

        # Create future for response
        future: asyncio.Future[Message] = asyncio.Future()
        self._pending[message.seq] = future

        try:
            await self._connection.send(message)
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(message.seq, None)
            raise
        except Exception:
            self._pending.pop(message.seq, None)
            raise

    async def send(self, message: Message) -> None:
        """Send a message without waiting for response."""
        if not self.is_connected:
            raise ConnectionError("Not connected")
        await self._connection.send(message)

    async def close(self) -> None:
        """Close the connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None


class DataSender:
    """UDP sender for pixel data."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: asyncio.DatagramProtocol | None = None
        self._sequence = 0

    async def start(self) -> None:
        """Start the sender."""
        loop = asyncio.get_event_loop()

        class Protocol(asyncio.DatagramProtocol):
            pass

        self._transport, self._protocol = await loop.create_datagram_endpoint(
            Protocol, remote_addr=(self.host, self.port)
        )
        logger.info(f"Data sender targeting {self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the sender."""
        if self._transport:
            self._transport.close()
            self._transport = None
            self._protocol = None

    def send(
        self,
        pixels: np.ndarray,
        color_format: ColorFormat = ColorFormat.RGB,
        encoding: Encoding = Encoding.RAW,
    ) -> None:
        """Send pixel data."""
        if not self._transport:
            raise RuntimeError("Sender not started")

        self._sequence = (self._sequence + 1) & 0xFFFFFFFF

        packet = DataPacket(
            sequence=self._sequence,
            color_format=color_format,
            pixel_data=pixels,
            encoding=encoding,
        )

        data = packet.to_bytes()
        if len(data) > MAX_PACKET_SIZE:
            logger.warning(f"Packet size {len(data)} exceeds max {MAX_PACKET_SIZE}")

        self._transport.sendto(data)


class DataReceiver:
    """UDP receiver for pixel data."""

    def __init__(self, host: str = "0.0.0.0", port: int = 0):
        self.host = host
        self.port = port
        self.handler: DataHandler | None = None

        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: "DataReceiverProtocol | None" = None

    @property
    def actual_port(self) -> int:
        """Get the actual bound port."""
        if self._transport:
            return self._transport.get_extra_info("sockname")[1]
        return self.port

    async def start(self) -> None:
        """Start the receiver."""
        loop = asyncio.get_event_loop()

        receiver = self

        class DataReceiverProtocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                try:
                    packet = DataPacket.from_bytes(data)
                    if receiver.handler:
                        receiver.handler(packet)
                except ProtocolError as e:
                    logger.warning(f"Invalid packet from {addr}: {e}")
                except Exception as e:
                    logger.error(f"Error processing packet from {addr}: {e}")

        self._transport, self._protocol = await loop.create_datagram_endpoint(
            DataReceiverProtocol, local_addr=(self.host, self.port)
        )
        logger.info(f"Data receiver listening on {self.host}:{self.actual_port}")

    async def stop(self) -> None:
        """Stop the receiver."""
        if self._transport:
            self._transport.close()
            self._transport = None
            self._protocol = None


class StreamManager:
    """Manages active data streams."""

    def __init__(self) -> None:
        self._streams: dict[str, dict[str, Any]] = {}
        self._next_id = 0

    def create_stream(
        self,
        sender: DataSender | None = None,
        receiver: DataReceiver | None = None,
        color_format: ColorFormat = ColorFormat.RGB,
        encoding: Encoding = Encoding.RAW,
    ) -> str:
        """Create a new stream and return its ID."""
        self._next_id += 1
        stream_id = f"stream-{self._next_id:04d}"

        self._streams[stream_id] = {
            "id": stream_id,
            "sender": sender,
            "receiver": receiver,
            "color_format": color_format,
            "encoding": encoding,
            "active": False,
            "frames_sent": 0,
            "frames_received": 0,
        }

        logger.info(f"Created stream {stream_id}")
        return stream_id

    def get_stream(self, stream_id: str) -> dict[str, Any] | None:
        """Get stream info by ID."""
        return self._streams.get(stream_id)

    def start_stream(self, stream_id: str) -> None:
        """Mark a stream as active."""
        if stream_id in self._streams:
            self._streams[stream_id]["active"] = True
            logger.info(f"Started stream {stream_id}")

    def stop_stream(self, stream_id: str) -> None:
        """Mark a stream as inactive."""
        if stream_id in self._streams:
            self._streams[stream_id]["active"] = False
            logger.info(f"Stopped stream {stream_id}")

    def delete_stream(self, stream_id: str) -> None:
        """Delete a stream."""
        if stream_id in self._streams:
            del self._streams[stream_id]
            logger.info(f"Deleted stream {stream_id}")

    def is_active(self, stream_id: str) -> bool:
        """Check if a stream is active."""
        stream = self._streams.get(stream_id)
        return stream is not None and stream["active"]

    def record_frame_sent(self, stream_id: str) -> None:
        """Record a frame was sent."""
        if stream_id in self._streams:
            self._streams[stream_id]["frames_sent"] += 1

    def record_frame_received(self, stream_id: str) -> None:
        """Record a frame was received."""
        if stream_id in self._streams:
            self._streams[stream_id]["frames_received"] += 1

    @property
    def active_streams(self) -> list[str]:
        """Get list of active stream IDs."""
        return [sid for sid, s in self._streams.items() if s["active"]]
