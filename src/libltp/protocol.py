"""Protocol message serialization and deserialization."""

import json
import struct
from typing import Any
from uuid import UUID

import numpy as np

from libltp.types import (
    ColorFormat,
    Encoding,
    ErrorCode,
    MessageType,
    PACKET_MAGIC,
    StreamAction,
)


class ProtocolError(Exception):
    """Protocol-related error."""

    def __init__(self, code: ErrorCode, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code.name}: {message}")


# JSON Control Channel Messages


class Message:
    """Base class for control channel messages."""

    def __init__(self, msg_type: MessageType, seq: int | None = None, **kwargs: Any):
        self.type = msg_type
        self.seq = seq
        self.data = kwargs

    def to_dict(self) -> dict[str, Any]:
        """Convert message to dictionary."""
        result: dict[str, Any] = {"type": self.type.value}
        if self.seq is not None:
            result["seq"] = self.seq
        result.update(self._serialize_values(self.data))
        return result

    def to_json(self) -> str:
        """Serialize message to JSON string with newline delimiter."""
        return json.dumps(self.to_dict()) + "\n"

    def to_bytes(self) -> bytes:
        """Serialize message to bytes."""
        return self.to_json().encode("utf-8")

    @staticmethod
    def _serialize_values(data: dict[str, Any]) -> dict[str, Any]:
        """Recursively serialize special types in data."""
        result = {}
        for key, value in data.items():
            if isinstance(value, UUID):
                result[key] = str(value)
            elif isinstance(value, dict):
                result[key] = Message._serialize_values(value)
            elif isinstance(value, list):
                result[key] = [
                    Message._serialize_values(v) if isinstance(v, dict) else v for v in value
                ]
            else:
                result[key] = value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        """Create message from dictionary."""
        msg_type = MessageType(data.pop("type"))
        seq = data.pop("seq", None)
        return cls(msg_type, seq, **data)

    @classmethod
    def from_json(cls, json_str: str) -> "Message":
        """Parse message from JSON string."""
        data = json.loads(json_str.strip())
        return cls.from_dict(data)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Message":
        """Parse message from bytes."""
        return cls.from_json(data.decode("utf-8"))


# Convenience message constructors


def capability_request(seq: int) -> Message:
    """Create a capability request message."""
    return Message(MessageType.CAPABILITY_REQUEST, seq)


def capability_response(seq: int, device: dict[str, Any]) -> Message:
    """Create a capability response message."""
    return Message(MessageType.CAPABILITY_RESPONSE, seq, device=device)


def stream_setup(
    seq: int,
    color: ColorFormat = ColorFormat.RGB,
    encoding: Encoding = Encoding.RAW,
    udp_port: int | None = None,
) -> Message:
    """Create a stream setup request."""
    return Message(
        MessageType.STREAM_SETUP,
        seq,
        format={"color": color.name.lower(), "encoding": encoding.name.lower()},
        udp_port=udp_port,
    )


def stream_setup_response(
    seq: int, status: str, udp_port: int, stream_id: str
) -> Message:
    """Create a stream setup response."""
    return Message(
        MessageType.STREAM_SETUP_RESPONSE,
        seq,
        status=status,
        udp_port=udp_port,
        stream_id=stream_id,
    )


def stream_control(seq: int, stream_id: str, action: StreamAction) -> Message:
    """Create a stream control message."""
    return Message(
        MessageType.STREAM_CONTROL, seq, stream_id=stream_id, action=action.value
    )


def control_get(seq: int, ids: list[str] | None = None) -> Message:
    """Create a control get request."""
    kwargs: dict[str, Any] = {}
    if ids is not None:
        kwargs["ids"] = ids
    return Message(MessageType.CONTROL_GET, seq, **kwargs)


def control_get_response(
    seq: int, status: str, values: dict[str, Any]
) -> Message:
    """Create a control get response."""
    return Message(MessageType.CONTROL_GET_RESPONSE, seq, status=status, values=values)


def control_set(seq: int, values: dict[str, Any]) -> Message:
    """Create a control set request."""
    return Message(MessageType.CONTROL_SET, seq, values=values)


def control_set_response(
    seq: int,
    status: str,
    applied: dict[str, Any],
    errors: dict[str, Any] | None = None,
) -> Message:
    """Create a control set response."""
    kwargs: dict[str, Any] = {"status": status, "applied": applied}
    if errors:
        kwargs["errors"] = errors
    return Message(MessageType.CONTROL_SET_RESPONSE, seq, **kwargs)


def control_changed(values: dict[str, Any]) -> Message:
    """Create a control changed notification."""
    return Message(MessageType.CONTROL_CHANGED, None, values=values)


def subscribe(
    seq: int, dimensions: list[int], color: str = "rgb", rate: int = 30
) -> Message:
    """Create a subscribe request."""
    return Message(
        MessageType.SUBSCRIBE,
        seq,
        target={"dimensions": dimensions, "color": color, "rate": rate},
    )


def subscribe_response(
    seq: int,
    status: str,
    actual: dict[str, Any],
    stream_id: str,
) -> Message:
    """Create a subscribe response."""
    return Message(
        MessageType.SUBSCRIBE_RESPONSE,
        seq,
        status=status,
        actual=actual,
        stream_id=stream_id,
    )


def error_message(seq: int | None, code: ErrorCode, message: str) -> Message:
    """Create an error message."""
    return Message(
        MessageType.ERROR, seq, code=code.value, error=code.name, message=message
    )


# Binary Data Channel


class DataPacket:
    """Binary data packet for UDP streaming."""

    HEADER_FORMAT = ">HBBI"  # magic(2), ver+flags(1), reserved(1), seq(4)
    HEADER_SIZE = 8
    FRAME_HEADER_FORMAT = ">BBH"  # color_fmt(1), encoding(1), pixel_count(2)
    FRAME_HEADER_SIZE = 4

    def __init__(
        self,
        sequence: int,
        color_format: ColorFormat,
        pixel_data: np.ndarray,
        encoding: Encoding = Encoding.RAW,
        flags: int = 0,
    ):
        self.sequence = sequence
        self.color_format = color_format
        self.encoding = encoding
        self.flags = flags
        self.pixel_data = pixel_data

    @property
    def pixel_count(self) -> int:
        """Return the number of pixels in this packet."""
        if self.pixel_data.ndim == 1:
            return len(self.pixel_data) // self.color_format.bytes_per_pixel
        return self.pixel_data.shape[0]

    def to_bytes(self) -> bytes:
        """Serialize packet to bytes."""
        # Packet header
        ver_flags = (0 << 4) | (self.flags & 0x0F)
        header = struct.pack(
            self.HEADER_FORMAT,
            PACKET_MAGIC,
            ver_flags,
            0,  # reserved
            self.sequence & 0xFFFFFFFF,
        )

        # Frame header
        frame_header = struct.pack(
            self.FRAME_HEADER_FORMAT,
            self.color_format.value,
            self.encoding.value,
            self.pixel_count,
        )

        # Pixel data
        if self.encoding == Encoding.RAW:
            pixel_bytes = self._encode_raw()
        elif self.encoding == Encoding.RLE:
            pixel_bytes = self._encode_rle()
        else:
            raise ProtocolError(
                ErrorCode.INVALID_FORMAT, f"Unsupported encoding: {self.encoding}"
            )

        return header + frame_header + pixel_bytes

    def _encode_raw(self) -> bytes:
        """Encode pixel data as raw bytes."""
        return self.pixel_data.astype(np.uint8).tobytes()

    def _encode_rle(self) -> bytes:
        """Encode pixel data using run-length encoding."""
        result = bytearray()
        pixels = self.pixel_data.reshape(-1, self.color_format.bytes_per_pixel)

        i = 0
        while i < len(pixels):
            current = pixels[i]
            count = 1

            while (
                i + count < len(pixels)
                and count < 255
                and np.array_equal(pixels[i + count], current)
            ):
                count += 1

            result.append(count)
            result.extend(current.tobytes())
            i += count

        return bytes(result)

    @classmethod
    def from_bytes(cls, data: bytes) -> "DataPacket":
        """Parse packet from bytes."""
        if len(data) < cls.HEADER_SIZE + cls.FRAME_HEADER_SIZE:
            raise ProtocolError(ErrorCode.INVALID_FORMAT, "Packet too small")

        # Parse packet header
        magic, ver_flags, _, sequence = struct.unpack(
            cls.HEADER_FORMAT, data[: cls.HEADER_SIZE]
        )

        if magic != PACKET_MAGIC:
            raise ProtocolError(
                ErrorCode.INVALID_FORMAT, f"Invalid magic: 0x{magic:04X}"
            )

        flags = ver_flags & 0x0F

        # Parse frame header
        frame_start = cls.HEADER_SIZE
        color_fmt, encoding, pixel_count = struct.unpack(
            cls.FRAME_HEADER_FORMAT,
            data[frame_start : frame_start + cls.FRAME_HEADER_SIZE],
        )

        color_format = ColorFormat(color_fmt)
        encoding = Encoding(encoding)

        # Parse pixel data
        pixel_start = frame_start + cls.FRAME_HEADER_SIZE
        pixel_bytes = data[pixel_start:]

        if encoding == Encoding.RAW:
            pixel_data = cls._decode_raw(pixel_bytes, color_format, pixel_count)
        elif encoding == Encoding.RLE:
            pixel_data = cls._decode_rle(pixel_bytes, color_format, pixel_count)
        else:
            raise ProtocolError(
                ErrorCode.INVALID_FORMAT, f"Unsupported encoding: {encoding}"
            )

        return cls(sequence, color_format, pixel_data, encoding, flags)

    @staticmethod
    def _decode_raw(
        data: bytes, color_format: ColorFormat, pixel_count: int
    ) -> np.ndarray:
        """Decode raw pixel data."""
        bpp = color_format.bytes_per_pixel
        expected_size = pixel_count * bpp
        if len(data) < expected_size:
            raise ProtocolError(
                ErrorCode.INVALID_FORMAT,
                f"Insufficient data: expected {expected_size}, got {len(data)}",
            )
        return np.frombuffer(data[:expected_size], dtype=np.uint8).reshape(
            pixel_count, bpp
        )

    @staticmethod
    def _decode_rle(
        data: bytes, color_format: ColorFormat, pixel_count: int
    ) -> np.ndarray:
        """Decode RLE pixel data."""
        bpp = color_format.bytes_per_pixel
        result = np.zeros((pixel_count, bpp), dtype=np.uint8)

        pos = 0
        pixel_idx = 0

        while pos < len(data) and pixel_idx < pixel_count:
            count = data[pos]
            pos += 1

            if pos + bpp > len(data):
                break

            color = np.frombuffer(data[pos : pos + bpp], dtype=np.uint8)
            pos += bpp

            end_idx = min(pixel_idx + count, pixel_count)
            result[pixel_idx:end_idx] = color
            pixel_idx = end_idx

        return result


def create_pixel_buffer(
    pixel_count: int, color_format: ColorFormat = ColorFormat.RGB
) -> np.ndarray:
    """Create an empty pixel buffer."""
    return np.zeros((pixel_count, color_format.bytes_per_pixel), dtype=np.uint8)


def create_matrix_buffer(
    width: int, height: int, color_format: ColorFormat = ColorFormat.RGB
) -> np.ndarray:
    """Create an empty matrix pixel buffer."""
    return np.zeros((height, width, color_format.bytes_per_pixel), dtype=np.uint8)
