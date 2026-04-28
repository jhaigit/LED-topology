"""Unit tests for libltp types and enums."""

import pytest

from libltp.types import (
    ColorFormat,
    DataType,
    DeviceType,
    Encoding,
    ErrorCode,
    MatrixOrder,
    MatrixOrigin,
    MessageType,
    MirrorMode,
    PACKET_MAGIC,
    ScalarFormat,
    ScaleMode,
    SourceMode,
    SourceType,
    StreamAction,
    TopologyType,
)


class TestColorFormat:
    def test_values(self):
        assert ColorFormat.RGB == 0x01
        assert ColorFormat.RGBW == 0x02
        assert ColorFormat.HSV == 0x03
        assert ColorFormat.GRAYSCALE == 0x04
        assert ColorFormat.MONO_PACKED == 0x05

    def test_bytes_per_pixel(self):
        assert ColorFormat.RGB.bytes_per_pixel == 3
        assert ColorFormat.RGBW.bytes_per_pixel == 4
        assert ColorFormat.HSV.bytes_per_pixel == 3
        assert ColorFormat.GRAYSCALE.bytes_per_pixel == 1
        assert ColorFormat.MONO_PACKED.bytes_per_pixel == 1

    def test_bits_per_pixel(self):
        assert ColorFormat.RGB.bits_per_pixel == 24
        assert ColorFormat.RGBW.bits_per_pixel == 32
        assert ColorFormat.GRAYSCALE.bits_per_pixel == 8
        assert ColorFormat.MONO_PACKED.bits_per_pixel == 1

    def test_packed_byte_count(self):
        assert ColorFormat.packed_byte_count(1) == 1
        assert ColorFormat.packed_byte_count(8) == 1
        assert ColorFormat.packed_byte_count(9) == 2
        assert ColorFormat.packed_byte_count(16) == 2
        assert ColorFormat.packed_byte_count(17) == 3
        assert ColorFormat.packed_byte_count(0) == 0


class TestScalarFormat:
    def test_values(self):
        assert ScalarFormat.FLOAT32 == 0x10
        assert ScalarFormat.INT16 == 0x11
        assert ScalarFormat.UINT8 == 0x12
        assert ScalarFormat.BOOLEAN == 0x13

    def test_bytes_per_channel(self):
        assert ScalarFormat.FLOAT32.bytes_per_channel == 4
        assert ScalarFormat.INT16.bytes_per_channel == 2
        assert ScalarFormat.UINT8.bytes_per_channel == 1
        assert ScalarFormat.BOOLEAN.bytes_per_channel == 0

    def test_bits_per_channel(self):
        assert ScalarFormat.FLOAT32.bits_per_channel == 32
        assert ScalarFormat.INT16.bits_per_channel == 16
        assert ScalarFormat.UINT8.bits_per_channel == 8
        assert ScalarFormat.BOOLEAN.bits_per_channel == 1


class TestEncoding:
    def test_values(self):
        assert Encoding.RAW == 0x00
        assert Encoding.RLE == 0x01
        assert Encoding.DELTA == 0x02


class TestMessageType:
    def test_capability_messages(self):
        assert MessageType.CAPABILITY_REQUEST == "capability_request"
        assert MessageType.CAPABILITY_RESPONSE == "capability_response"

    def test_stream_messages(self):
        assert MessageType.STREAM_SETUP == "stream_setup"
        assert MessageType.STREAM_CONTROL == "stream_control"

    def test_control_messages(self):
        assert MessageType.CONTROL_GET == "control_get"
        assert MessageType.CONTROL_SET == "control_set"
        assert MessageType.CONTROL_CHANGED == "control_changed"

    def test_input_event(self):
        assert MessageType.INPUT_EVENT == "input_event"


class TestStreamAction:
    def test_values(self):
        assert StreamAction.START == "start"
        assert StreamAction.STOP == "stop"
        assert StreamAction.PAUSE == "pause"


class TestErrorCode:
    def test_values(self):
        assert ErrorCode.OK == 0
        assert ErrorCode.INVALID_FORMAT == 1
        assert ErrorCode.NOT_FOUND == 4
        assert ErrorCode.INTERNAL == 5


class TestConstants:
    def test_packet_magic(self):
        assert PACKET_MAGIC == 0x4C54


class TestStringEnums:
    def test_device_type(self):
        assert DeviceType.MATRIX == "matrix"
        assert DeviceType.STRING == "string"

    def test_topology_type(self):
        assert TopologyType.LINEAR == "linear"
        assert TopologyType.MATRIX == "matrix"

    def test_source_mode(self):
        assert SourceMode.STREAM == "stream"
        assert SourceMode.STATIC == "static"

    def test_scale_mode(self):
        assert ScaleMode.NONE == "none"
        assert ScaleMode.FIT == "fit"
        assert ScaleMode.PAD_BLACK == "pad_black"

    def test_mirror_mode(self):
        assert MirrorMode.NONE == "none"
        assert MirrorMode.BOTH == "both"

    def test_matrix_origin(self):
        assert MatrixOrigin.TOP_LEFT == "top-left"
        assert MatrixOrigin.BOTTOM_RIGHT == "bottom-right"

    def test_matrix_order(self):
        assert MatrixOrder.ROW_MAJOR == "row-major"
        assert MatrixOrder.COLUMN_MAJOR == "column-major"

    def test_data_type(self):
        assert DataType.VISUAL == "visual"
        assert DataType.SCALAR == "scalar"

    def test_source_type(self):
        assert SourceType.AUDIO == "audio"
        assert SourceType.PATTERN == "pattern"
