"""Common data types and enums for LTP."""

from enum import Enum, IntEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ColorFormat(IntEnum):
    """Color format identifiers for visual data channel."""

    RGB = 0x01
    RGBW = 0x02
    HSV = 0x03
    GRAYSCALE = 0x04

    @property
    def bytes_per_pixel(self) -> int:
        """Return the number of bytes per pixel for this format."""
        return {
            ColorFormat.RGB: 3,
            ColorFormat.RGBW: 4,
            ColorFormat.HSV: 3,
            ColorFormat.GRAYSCALE: 1,
        }[self]


class ScalarFormat(IntEnum):
    """Data format identifiers for scalar data channels."""

    FLOAT32 = 0x10  # 4 bytes per channel, IEEE 754 float
    INT16 = 0x11    # 2 bytes per channel, signed integer
    UINT8 = 0x12    # 1 byte per channel, unsigned integer
    BOOLEAN = 0x13  # 1 bit per channel, packed into bytes

    @property
    def bytes_per_channel(self) -> int:
        """Return the number of bytes per channel for this format."""
        return {
            ScalarFormat.FLOAT32: 4,
            ScalarFormat.INT16: 2,
            ScalarFormat.UINT8: 1,
            ScalarFormat.BOOLEAN: 0,  # Special: bit-packed, use bits_per_channel
        }[self]

    @property
    def bits_per_channel(self) -> int:
        """Return the number of bits per channel (for BOOLEAN)."""
        return {
            ScalarFormat.FLOAT32: 32,
            ScalarFormat.INT16: 16,
            ScalarFormat.UINT8: 8,
            ScalarFormat.BOOLEAN: 1,
        }[self]


# Combined data format type for protocol use
DataFormat = ColorFormat | ScalarFormat


class DataType(str, Enum):
    """Data type category for sources and sinks."""

    VISUAL = "visual"   # LED pixel data (RGB, RGBW, etc.)
    SCALAR = "scalar"   # Sensor/control data (float, int, bool)


class Encoding(IntEnum):
    """Data encoding types for pixel data."""

    RAW = 0x00
    RLE = 0x01
    DELTA = 0x02


class DeviceType(str, Enum):
    """Types of display devices."""

    SINGLE = "single"
    STRING = "string"
    ARRAY = "array"
    MATRIX = "matrix"
    CUSTOM = "custom"


class TopologyType(str, Enum):
    """Topology types for device layout."""

    LINEAR = "linear"
    MATRIX = "matrix"
    CUSTOM = "custom"


class SourceMode(str, Enum):
    """Data source output modes."""

    STREAM = "stream"
    STATIC = "static"
    INTERACTIVE = "interactive"


class SourceType(str, Enum):
    """Built-in source type categories."""

    AUDIO = "audio"
    VIDEO = "video"
    PATTERN = "pattern"
    CLOCK = "clock"
    SENSOR = "sensor"
    NETWORK = "network"
    CUSTOM = "custom"


class MessageType(str, Enum):
    """Control channel message types."""

    # Capability
    CAPABILITY_REQUEST = "capability_request"
    CAPABILITY_RESPONSE = "capability_response"

    # Stream management
    STREAM_SETUP = "stream_setup"
    STREAM_SETUP_RESPONSE = "stream_setup_response"
    STREAM_CONTROL = "stream_control"
    STREAM_CONTROL_RESPONSE = "stream_control_response"

    # Controls
    CONTROL_GET = "control_get"
    CONTROL_GET_RESPONSE = "control_get_response"
    CONTROL_SET = "control_set"
    CONTROL_SET_RESPONSE = "control_set_response"
    CONTROL_CHANGED = "control_changed"

    # Source subscription
    SUBSCRIBE = "subscribe"
    SUBSCRIBE_RESPONSE = "subscribe_response"

    # Routing (controller)
    ROUTE_CREATE = "route_create"
    ROUTE_CREATE_RESPONSE = "route_create_response"
    ROUTE_DELETE = "route_delete"
    ROUTE_DELETE_RESPONSE = "route_delete_response"

    # Error
    ERROR = "error"


class StreamAction(str, Enum):
    """Stream control actions."""

    START = "start"
    STOP = "stop"
    PAUSE = "pause"


class ErrorCode(IntEnum):
    """Protocol error codes."""

    OK = 0
    INVALID_FORMAT = 1
    BUSY = 2
    RATE_LIMIT = 3
    NOT_FOUND = 4
    INTERNAL = 5
    INVALID_VALUE = 6
    READONLY = 7


class MatrixOrigin(str, Enum):
    """Starting corner for matrix topology."""

    TOP_LEFT = "top-left"
    TOP_RIGHT = "top-right"
    BOTTOM_LEFT = "bottom-left"
    BOTTOM_RIGHT = "bottom-right"


class MatrixOrder(str, Enum):
    """Pixel ordering for matrix topology."""

    ROW_MAJOR = "row-major"
    COLUMN_MAJOR = "column-major"


class ScaleMode(str, Enum):
    """Scaling modes for transforms."""

    NONE = "none"
    FIT = "fit"
    FILL = "fill"
    STRETCH = "stretch"
    PAD_BLACK = "pad_black"  # Pad shorter source with black pixels
    PAD_REPEAT = "pad_repeat"  # Repeat/tile source pattern to fill sink
    TRUNCATE = "truncate"  # Truncate longer source to fit sink


class MirrorMode(str, Enum):
    """Mirroring modes for transforms."""

    NONE = "none"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    BOTH = "both"


# Pydantic models for structured data


class Coordinate(BaseModel):
    """A single coordinate point for custom topologies."""

    index: int
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    z: float | None = Field(default=None, ge=0.0, le=1.0)


class Channel(BaseModel):
    """Metadata for a single scalar data channel."""

    index: int
    id: str  # Unique identifier (e.g., "temperature", "motion")
    name: str  # Human-readable name
    type: str = "float32"  # float32, int16, uint8, boolean
    unit: str = ""  # Optional unit (e.g., "°C", "%", "Hz")
    min: float | None = None  # Optional minimum value
    max: float | None = None  # Optional maximum value
    readonly: bool = True  # True for sensors, False for outputs


class ChannelArray(BaseModel):
    """Metadata for an array of homogeneous scalar data channels."""

    id: str  # Unique identifier (e.g., "cpu_cores", "zone_temps")
    name: str  # Human-readable name
    type: str = "float32"  # float32, int16, uint8, boolean
    unit: str = ""  # Optional unit
    min: float | None = None
    max: float | None = None
    count: int  # Number of channels in this array
    start_index: int = 0  # Starting index in the data packet
    readonly: bool = True


class LinearTopology(BaseModel):
    """Linear (1D) topology description."""

    topology: TopologyType = TopologyType.LINEAR
    dimensions: tuple[int]


class MatrixTopology(BaseModel):
    """Matrix (2D) topology description."""

    topology: TopologyType = TopologyType.MATRIX
    dimensions: tuple[int, int]
    origin: MatrixOrigin = MatrixOrigin.TOP_LEFT
    order: MatrixOrder = MatrixOrder.ROW_MAJOR
    serpentine: bool = False


class CustomTopology(BaseModel):
    """Custom topology with explicit coordinates."""

    topology: TopologyType = TopologyType.CUSTOM
    pixels: int
    coordinates: list[Coordinate]


Topology = LinearTopology | MatrixTopology | CustomTopology


class DeviceInfo(BaseModel):
    """Basic device information."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = ""
    protocol_version: str = "0.1"


class SinkCapabilities(BaseModel):
    """Sink device capabilities."""

    device: DeviceInfo
    type: DeviceType
    pixels: int
    dimensions: list[int]
    topology: Topology
    color_formats: list[ColorFormat]
    max_refresh_hz: int = 60
    controls: list[Any] = Field(default_factory=list)

    # Scalar data support (for dimmers, switches, relays)
    data_type: DataType = DataType.VISUAL  # visual or scalar
    scalar_formats: list[ScalarFormat] = Field(default_factory=list)  # Supported scalar formats
    channels: list[Channel] = Field(default_factory=list)  # Individual channel metadata
    channel_arrays: list[ChannelArray] = Field(default_factory=list)  # Array channel metadata


class SourceCapabilities(BaseModel):
    """Source device capabilities."""

    device: DeviceInfo
    output_dimensions: list[int]
    color_format: ColorFormat
    rate: int
    mode: SourceMode
    source_type: SourceType = SourceType.PATTERN
    controls: list[Any] = Field(default_factory=list)

    # Scalar data support (for sensors)
    data_type: DataType = DataType.VISUAL  # visual or scalar
    scalar_format: ScalarFormat | None = None  # Primary scalar format if data_type is scalar
    channels: list[Channel] = Field(default_factory=list)  # Individual channel metadata
    channel_arrays: list[ChannelArray] = Field(default_factory=list)  # Array channel metadata


class StreamConfig(BaseModel):
    """Stream configuration for setup."""

    color: ColorFormat = ColorFormat.RGB
    encoding: Encoding = Encoding.RAW


class Transform(BaseModel):
    """Data transformation configuration."""

    scale: ScaleMode = ScaleMode.FIT
    color_map: str = "none"
    brightness: float = Field(default=1.0, ge=0.0, le=1.0)
    mirror: MirrorMode = MirrorMode.NONE


class Route(BaseModel):
    """Routing configuration between source and sink."""

    name: str
    source_id: UUID
    sink_id: UUID
    enabled: bool = True
    transform: Transform = Field(default_factory=Transform)


# Data channel packet structures


class PacketHeader(BaseModel):
    """Data channel packet header."""

    magic: int = 0x4C54  # "LT"
    version: int = 0
    flags: int = 0
    sequence: int


class FrameHeader(BaseModel):
    """Frame header within data packet (visual data)."""

    color_format: ColorFormat
    encoding: Encoding
    pixel_count: int


class ScalarFrameHeader(BaseModel):
    """Frame header for scalar data packets."""

    scalar_format: ScalarFormat
    encoding: Encoding = Encoding.RAW
    channel_count: int

    def data_size(self) -> int:
        """Calculate the data payload size in bytes."""
        if self.scalar_format == ScalarFormat.BOOLEAN:
            # Bit-packed: ceil(channels / 8)
            return (self.channel_count + 7) // 8
        return self.channel_count * self.scalar_format.bytes_per_channel


# Service type constants
SERVICE_TYPE_SINK = "_ltp-sink._tcp.local."
SERVICE_TYPE_SOURCE = "_ltp-source._tcp.local."
SERVICE_TYPE_CONTROLLER = "_ltp-controller._tcp.local."

# Protocol constants
PROTOCOL_VERSION = "0.1"
PACKET_MAGIC = 0x4C54
MAX_PACKET_SIZE = 1400
DEFAULT_REFRESH_HZ = 30
