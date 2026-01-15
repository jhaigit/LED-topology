"""Base classes for scalar data sources (sensors)."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID, uuid4

import numpy as np

from libltp import (
    Channel,
    ChannelArray,
    ControlRegistry,
    DataType,
    NumberControl,
    ScalarDataPacket,
    ScalarFormat,
    SourceAdvertiser,
    SourceMode,
)
from libltp.transport import ControlServer, DataSender

logger = logging.getLogger(__name__)


@dataclass
class ScalarSourceConfig:
    """Configuration for a scalar data source."""

    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Scalar Source"
    description: str = ""

    # Data format
    scalar_format: ScalarFormat = ScalarFormat.FLOAT32
    channel_count: int = 1

    # Update rate
    sample_rate: float = 1.0  # Hz

    # Network
    control_port: int = 0  # 0 = auto-assign

    # State
    enabled: bool = True

    # Persisted control values
    control_values: dict[str, Any] = field(default_factory=dict)


class ScalarSource(ABC):
    """Base class for scalar data sources (sensors).

    A scalar source collects data from sensors or other non-visual inputs
    and streams it over the LTP protocol using ScalarDataPacket.

    Subclasses must implement:
    - _setup_controls(): Register source-specific controls
    - _setup_channels(): Define channel metadata
    - sample(): Collect current sensor values
    """

    source_type: str = "unknown"

    def __init__(self, config: ScalarSourceConfig | None = None):
        self._config = config or ScalarSourceConfig()
        self._controls = ControlRegistry()
        self._channels: list[Channel] = []
        self._channel_arrays: list[ChannelArray] = []

        # Network components
        self._advertiser: SourceAdvertiser | None = None
        self._control_server: ControlServer | None = None
        self._data_senders: dict[str, DataSender] = {}

        # State
        self._running = False
        self._sequence = 0
        self._sample_task: asyncio.Task | None = None
        self._last_sample_time = 0.0
        self._samples_collected = 0

        # Data buffer
        self._data_buffer: np.ndarray | None = None

        # Callbacks
        self._on_data_callbacks: list[Callable[[np.ndarray], None]] = []

        # Setup
        self._setup_base_controls()
        self._setup_controls()
        self._setup_channels()
        self._restore_control_values()

    def _setup_base_controls(self) -> None:
        """Set up common controls for all scalar sources."""
        self._controls.register(
            NumberControl(
                id="sample_rate",
                name="Sample Rate",
                description="Data collection rate in Hz",
                value=self._config.sample_rate,
                min=0.1,
                max=100.0,
                step=0.1,
                unit="Hz",
                group="general",
            )
        )

    @abstractmethod
    def _setup_controls(self) -> None:
        """Set up source-specific controls. Override in subclasses."""
        pass

    @abstractmethod
    def _setup_channels(self) -> None:
        """Define channel metadata. Override in subclasses.

        Use self._add_channel() or self._add_channel_array() to register channels.
        """
        pass

    @abstractmethod
    def sample(self) -> np.ndarray:
        """Collect current sensor values.

        Returns:
            numpy array of scalar values matching the configured format and channel count
        """
        pass

    def _add_channel(
        self,
        id: str,
        name: str,
        type: str = "float32",
        unit: str = "",
        min_val: float | None = None,
        max_val: float | None = None,
    ) -> None:
        """Add a single channel definition."""
        index = len(self._channels)
        for arr in self._channel_arrays:
            index = max(index, arr.start_index + arr.count)

        self._channels.append(
            Channel(
                index=index,
                id=id,
                name=name,
                type=type,
                unit=unit,
                min=min_val,
                max=max_val,
                readonly=True,
            )
        )

    def _add_channel_array(
        self,
        id: str,
        name: str,
        count: int,
        type: str = "float32",
        unit: str = "",
        min_val: float | None = None,
        max_val: float | None = None,
    ) -> None:
        """Add an array of homogeneous channels."""
        # Calculate start index after existing channels
        start_index = len(self._channels)
        for arr in self._channel_arrays:
            start_index = max(start_index, arr.start_index + arr.count)

        self._channel_arrays.append(
            ChannelArray(
                id=id,
                name=name,
                type=type,
                unit=unit,
                min=min_val,
                max=max_val,
                count=count,
                start_index=start_index,
                readonly=True,
            )
        )

    def _restore_control_values(self) -> None:
        """Restore control values from config."""
        if self._config.control_values:
            self._controls.set_values(self._config.control_values)

    @property
    def id(self) -> str:
        return self._config.id

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def config(self) -> ScalarSourceConfig:
        return self._config

    @property
    def controls(self) -> ControlRegistry:
        return self._controls

    @property
    def channels(self) -> list[Channel]:
        return self._channels

    @property
    def channel_arrays(self) -> list[ChannelArray]:
        return self._channel_arrays

    @property
    def total_channels(self) -> int:
        """Total number of data channels."""
        count = len(self._channels)
        for arr in self._channel_arrays:
            count += arr.count
        return count

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def samples_collected(self) -> int:
        return self._samples_collected

    def get_control(self, control_id: str) -> Any:
        """Get a control value."""
        return self._controls.get_value(control_id)

    def set_control(self, control_id: str, value: Any) -> None:
        """Set a control value."""
        self._controls.set_value(control_id, value)

    def on_data(self, callback: Callable[[np.ndarray], None]) -> None:
        """Register a callback for new data samples."""
        self._on_data_callbacks.append(callback)

    async def start(self) -> None:
        """Start the scalar source."""
        if self._running:
            return

        logger.info(f"Starting scalar source: {self._config.name}")

        # Initialize data buffer
        self._init_buffer()

        # Start control server
        self._control_server = ControlServer(
            port=self._config.control_port,
            handler=self._handle_message,
        )
        await self._control_server.start()

        # Start mDNS advertisement
        self._advertiser = SourceAdvertiser(
            name=self._config.name.lower().replace(" ", "-"),
            port=self._control_server.actual_port,
            device_id=UUID(self._config.id),
            display_name=self._config.name,
            description=self._config.description,
            dimensions=[self.total_channels],
            rate=int(self._config.sample_rate),
            mode=SourceMode.STREAM,
            has_controls=True,
            data_type=DataType.SCALAR,
            scalar_format=self._config.scalar_format,
            channels=self.total_channels,
        )
        await self._advertiser.start()

        # Start sampling task
        self._running = True
        self._sample_task = asyncio.create_task(self._sample_loop())

        logger.info(
            f"Scalar source started: {self._config.name} "
            f"(port={self._control_server.actual_port}, channels={self.total_channels})"
        )

    async def stop(self) -> None:
        """Stop the scalar source."""
        if not self._running:
            return

        logger.info(f"Stopping scalar source: {self._config.name}")

        self._running = False

        if self._sample_task:
            self._sample_task.cancel()
            try:
                await self._sample_task
            except asyncio.CancelledError:
                pass
            self._sample_task = None

        if self._advertiser:
            await self._advertiser.stop()
            self._advertiser = None

        if self._control_server:
            await self._control_server.stop()
            self._control_server = None

        # Close data senders
        for sender in self._data_senders.values():
            await sender.close()
        self._data_senders.clear()

        logger.info(f"Scalar source stopped: {self._config.name}")

    def _init_buffer(self) -> None:
        """Initialize the data buffer based on format."""
        dtype_map = {
            ScalarFormat.FLOAT32: np.float32,
            ScalarFormat.INT16: np.int16,
            ScalarFormat.UINT8: np.uint8,
            ScalarFormat.BOOLEAN: np.bool_,
        }
        self._data_buffer = np.zeros(
            self.total_channels,
            dtype=dtype_map[self._config.scalar_format],
        )

    async def _sample_loop(self) -> None:
        """Main sampling loop."""
        while self._running:
            try:
                sample_rate = self.get_control("sample_rate")
                interval = 1.0 / sample_rate

                start_time = time.time()

                # Collect sample
                data = self.sample()

                # Store in buffer
                if self._data_buffer is not None and len(data) == len(self._data_buffer):
                    self._data_buffer[:] = data

                # Notify callbacks
                for callback in self._on_data_callbacks:
                    try:
                        callback(data)
                    except Exception as e:
                        logger.error(f"Data callback error: {e}")

                # Send to subscribers
                await self._send_data(data)

                self._samples_collected += 1
                self._last_sample_time = time.time()

                # Sleep for remaining interval
                elapsed = time.time() - start_time
                sleep_time = max(0, interval - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sampling error: {e}")
                await asyncio.sleep(0.1)

    async def _send_data(self, data: np.ndarray) -> None:
        """Send data to all subscribers."""
        if not self._data_senders:
            return

        packet = ScalarDataPacket(
            sequence=self._sequence,
            scalar_format=self._config.scalar_format,
            channel_data=data,
        )
        self._sequence += 1

        packet_bytes = packet.to_bytes()

        for stream_id, sender in list(self._data_senders.items()):
            try:
                await sender.send(packet_bytes)
            except Exception as e:
                logger.error(f"Send error for stream {stream_id}: {e}")

    def _handle_message(self, message: Any) -> Any:
        """Handle incoming control messages."""
        from libltp import Message, MessageType, capability_response

        if message.type == MessageType.CAPABILITY_REQUEST:
            return self._handle_capability_request(message)
        elif message.type == MessageType.SUBSCRIBE:
            return self._handle_subscribe(message)
        elif message.type == MessageType.CONTROL_GET:
            return self._handle_control_get(message)
        elif message.type == MessageType.CONTROL_SET:
            return self._handle_control_set(message)

        return None

    def _handle_capability_request(self, message: Any) -> Any:
        """Handle capability request."""
        from libltp import capability_response

        device_info = {
            "id": self._config.id,
            "name": self._config.name,
            "description": self._config.description,
            "source_type": self.source_type,
            "data_type": DataType.SCALAR.value,
            "output_dimensions": [self.total_channels],
            "scalar_format": self._config.scalar_format.name.lower(),
            "rate": int(self.get_control("sample_rate")),
            "mode": "stream",
            "channels": [ch.model_dump() for ch in self._channels],
            "channel_arrays": [arr.model_dump() for arr in self._channel_arrays],
            "controls": self._controls.to_list(),
        }
        return capability_response(message.seq, device_info)

    def _handle_subscribe(self, message: Any) -> Any:
        """Handle subscribe request."""
        from libltp import Message, MessageType
        from libltp.transport import DataSender

        callback = message.data.get("callback", {})
        host = callback.get("host")
        port = callback.get("port")

        if not host or not port:
            return Message(
                MessageType.SUBSCRIBE_RESPONSE,
                message.seq,
                status="error",
                error="Missing callback host/port",
            )

        stream_id = str(uuid4())

        # Create data sender
        sender = DataSender(host, port)
        self._data_senders[stream_id] = sender

        logger.info(f"Subscriber added: {host}:{port} (stream={stream_id})")

        return Message(
            MessageType.SUBSCRIBE_RESPONSE,
            message.seq,
            status="ok",
            stream_id=stream_id,
            actual={
                "dimensions": [self.total_channels],
                "format": self._config.scalar_format.name.lower(),
                "rate": int(self.get_control("sample_rate")),
            },
        )

    def _handle_control_get(self, message: Any) -> Any:
        """Handle control get request."""
        from libltp import control_get_response

        ids = message.data.get("ids")
        values = self._controls.get_values(ids)
        return control_get_response(message.seq, "ok", values)

    def _handle_control_set(self, message: Any) -> Any:
        """Handle control set request."""
        from libltp import control_set_response

        values = message.data.get("values", {})
        applied, errors = self._controls.set_values(values)
        status = "ok" if not errors else "partial"
        return control_set_response(message.seq, status, applied, errors or None)

    def to_dict(self) -> dict[str, Any]:
        """Convert source to dictionary for API."""
        return {
            "id": self._config.id,
            "name": self._config.name,
            "description": self._config.description,
            "source_type": self.source_type,
            "data_type": DataType.SCALAR.value,
            "scalar_format": self._config.scalar_format.name.lower(),
            "channel_count": self.total_channels,
            "sample_rate": self.get_control("sample_rate"),
            "is_running": self._running,
            "samples_collected": self._samples_collected,
            "channels": [ch.model_dump() for ch in self._channels],
            "channel_arrays": [arr.model_dump() for arr in self._channel_arrays],
            "controls": self._controls.to_list(),
        }


class ScalarSourceManager:
    """Manages multiple scalar sources."""

    def __init__(self):
        self._sources: dict[str, ScalarSource] = {}

    def add(self, source: ScalarSource) -> None:
        """Add a scalar source."""
        self._sources[source.id] = source

    def remove(self, source_id: str) -> ScalarSource | None:
        """Remove and return a scalar source."""
        return self._sources.pop(source_id, None)

    def get(self, source_id: str) -> ScalarSource | None:
        """Get a scalar source by ID."""
        return self._sources.get(source_id)

    def all(self) -> list[ScalarSource]:
        """Get all scalar sources."""
        return list(self._sources.values())

    async def start_all(self) -> None:
        """Start all enabled sources."""
        for source in self._sources.values():
            if source.config.enabled:
                await source.start()

    async def stop_all(self) -> None:
        """Stop all sources."""
        for source in self._sources.values():
            await source.stop()

    def to_list(self) -> list[dict[str, Any]]:
        """Convert all sources to list of dicts."""
        return [s.to_dict() for s in self._sources.values()]
