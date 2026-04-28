"""Unit tests for libltp protocol Message, DataPacket, and ScalarDataPacket."""

import json
from uuid import UUID, uuid4

import numpy as np
import pytest

from libltp.protocol import (
    DataPacket,
    Message,
    ProtocolError,
    ScalarDataPacket,
    capability_request,
    capability_response,
    control_get,
    control_set,
    error_message,
    input_event,
    pixel_read,
    stream_control,
    stream_setup,
    subscribe,
)
from libltp.types import (
    ColorFormat,
    Encoding,
    ErrorCode,
    MessageType,
    PACKET_MAGIC,
    ScalarFormat,
    StreamAction,
)


# --- Message tests ---


class TestMessage:
    def test_basic_roundtrip(self):
        msg = Message(MessageType.CAPABILITY_REQUEST, seq=1)
        d = msg.to_dict()
        assert d["type"] == "capability_request"
        assert d["seq"] == 1

        restored = Message.from_dict(d)
        assert restored.type == MessageType.CAPABILITY_REQUEST
        assert restored.seq == 1

    def test_json_roundtrip(self):
        msg = Message(MessageType.CONTROL_SET, seq=5, values={"brightness": 200})
        json_str = msg.to_json()
        assert json_str.endswith("\n")

        restored = Message.from_json(json_str)
        assert restored.type == MessageType.CONTROL_SET
        assert restored.seq == 5
        assert restored.data["values"] == {"brightness": 200}

    def test_bytes_roundtrip(self):
        msg = Message(MessageType.ERROR, seq=3, code=1, message="test error")
        data = msg.to_bytes()
        restored = Message.from_bytes(data)
        assert restored.type == MessageType.ERROR
        assert restored.data["message"] == "test error"

    def test_no_seq(self):
        msg = Message(MessageType.CONTROL_CHANGED, None, values={"brightness": 100})
        d = msg.to_dict()
        assert "seq" not in d

    def test_uuid_serialization(self):
        uid = uuid4()
        msg = Message(MessageType.CAPABILITY_RESPONSE, seq=1, device_id=uid)
        d = msg.to_dict()
        assert d["device_id"] == str(uid)

    def test_nested_dict_serialization(self):
        uid = uuid4()
        msg = Message(
            MessageType.CAPABILITY_RESPONSE,
            seq=1,
            device={"id": uid, "name": "test"},
        )
        d = msg.to_dict()
        assert d["device"]["id"] == str(uid)
        assert d["device"]["name"] == "test"


class TestMessageFactories:
    def test_capability_request(self):
        msg = capability_request(1)
        assert msg.type == MessageType.CAPABILITY_REQUEST
        assert msg.seq == 1

    def test_capability_response(self):
        msg = capability_response(1, {"name": "test", "pixels": 60})
        assert msg.type == MessageType.CAPABILITY_RESPONSE
        assert msg.data["device"]["pixels"] == 60

    def test_stream_setup(self):
        msg = stream_setup(2, color=ColorFormat.RGB, encoding=Encoding.RAW, udp_port=5000)
        d = msg.to_dict()
        assert d["format"]["color"] == "rgb"
        assert d["udp_port"] == 5000

    def test_stream_control(self):
        msg = stream_control(3, "stream-123", StreamAction.START)
        d = msg.to_dict()
        assert d["stream_id"] == "stream-123"
        assert d["action"] == "start"

    def test_control_get(self):
        msg = control_get(4, ids=["brightness", "gamma"])
        d = msg.to_dict()
        assert d["ids"] == ["brightness", "gamma"]

    def test_control_get_no_ids(self):
        msg = control_get(4)
        d = msg.to_dict()
        assert "ids" not in d

    def test_control_set(self):
        msg = control_set(5, values={"brightness": 200})
        d = msg.to_dict()
        assert d["values"] == {"brightness": 200}

    def test_input_event(self):
        msg = input_event(0, "button1", "button", True, timestamp=12345)
        d = msg.to_dict()
        assert d["input_id"] == 0
        assert d["input_name"] == "button1"
        assert d["value"] is True
        assert d["timestamp"] == 12345

    def test_pixel_read(self):
        msg = pixel_read(6, start=0, count=10)
        d = msg.to_dict()
        assert d["start"] == 0
        assert d["count"] == 10

    def test_error_message(self):
        msg = error_message(7, ErrorCode.NOT_FOUND, "sink not found")
        d = msg.to_dict()
        assert d["code"] == 4
        assert d["error"] == "NOT_FOUND"
        assert d["message"] == "sink not found"

    def test_subscribe(self):
        msg = subscribe(8, dimensions=[60], color="rgb", rate=30,
                        callback_host="192.168.1.10", callback_port=9000)
        d = msg.to_dict()
        assert d["target"]["dimensions"] == [60]
        assert d["callback"]["host"] == "192.168.1.10"

    def test_subscribe_no_callback(self):
        msg = subscribe(8, dimensions=[60])
        d = msg.to_dict()
        assert "callback" not in d


# --- DataPacket tests ---


class TestDataPacket:
    def test_raw_roundtrip_rgb(self):
        pixels = np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255]], dtype=np.uint8)
        pkt = DataPacket(sequence=1, color_format=ColorFormat.RGB, pixel_data=pixels)

        data = pkt.to_bytes()
        restored = DataPacket.from_bytes(data)

        assert restored.sequence == 1
        assert restored.color_format == ColorFormat.RGB
        assert restored.encoding == Encoding.RAW
        assert restored.pixel_count == 3
        np.testing.assert_array_equal(restored.pixel_data, pixels)

    def test_raw_roundtrip_rgbw(self):
        pixels = np.array([[255, 0, 0, 128], [0, 255, 0, 64]], dtype=np.uint8)
        pkt = DataPacket(sequence=2, color_format=ColorFormat.RGBW, pixel_data=pixels)

        data = pkt.to_bytes()
        restored = DataPacket.from_bytes(data)

        assert restored.color_format == ColorFormat.RGBW
        assert restored.pixel_count == 2
        np.testing.assert_array_equal(restored.pixel_data, pixels)

    def test_rle_encoding(self):
        pixels = np.array(
            [[255, 0, 0]] * 10 + [[0, 255, 0]] * 5, dtype=np.uint8
        )
        pkt = DataPacket(
            sequence=3,
            color_format=ColorFormat.RGB,
            pixel_data=pixels,
            encoding=Encoding.RLE,
        )

        data = pkt.to_bytes()
        restored = DataPacket.from_bytes(data)

        assert restored.pixel_count == 15
        np.testing.assert_array_equal(restored.pixel_data, pixels)

    def test_rle_is_smaller_for_uniform_data(self):
        pixels = np.full((100, 3), 128, dtype=np.uint8)

        raw_pkt = DataPacket(1, ColorFormat.RGB, pixels, encoding=Encoding.RAW)
        rle_pkt = DataPacket(1, ColorFormat.RGB, pixels, encoding=Encoding.RLE)

        assert len(rle_pkt.to_bytes()) < len(raw_pkt.to_bytes())

    def test_magic_header(self):
        pixels = np.array([[1, 2, 3]], dtype=np.uint8)
        pkt = DataPacket(0, ColorFormat.RGB, pixels)
        data = pkt.to_bytes()

        import struct
        magic = struct.unpack(">H", data[:2])[0]
        assert magic == PACKET_MAGIC

    def test_invalid_magic_raises(self):
        pixels = np.array([[1, 2, 3]], dtype=np.uint8)
        pkt = DataPacket(0, ColorFormat.RGB, pixels)
        data = bytearray(pkt.to_bytes())
        data[0] = 0x00
        data[1] = 0x00

        with pytest.raises(ProtocolError, match="Invalid magic"):
            DataPacket.from_bytes(bytes(data))

    def test_packet_too_small_raises(self):
        with pytest.raises(ProtocolError, match="too small"):
            DataPacket.from_bytes(b"\x00\x01\x02")

    def test_chunk_index(self):
        pixels = np.array([[1, 2, 3]], dtype=np.uint8)
        pkt = DataPacket(10, ColorFormat.RGB, pixels, chunk_index=5)
        data = pkt.to_bytes()
        restored = DataPacket.from_bytes(data)
        assert restored.chunk_index == 5

    def test_sequence_wraps(self):
        pixels = np.array([[1, 2, 3]], dtype=np.uint8)
        pkt = DataPacket(0xFFFFFFFF, ColorFormat.RGB, pixels)
        data = pkt.to_bytes()
        restored = DataPacket.from_bytes(data)
        assert restored.sequence == 0xFFFFFFFF

    def test_pixel_count_override(self):
        pixels = np.array([[1, 2, 3]], dtype=np.uint8)
        pkt = DataPacket(0, ColorFormat.RGB, pixels, pixel_count_override=99)
        assert pkt.pixel_count == 99

    def test_pixel_count_from_shape(self):
        pixels = np.zeros((50, 3), dtype=np.uint8)
        pkt = DataPacket(0, ColorFormat.RGB, pixels)
        assert pkt.pixel_count == 50

    def test_pixel_count_from_1d(self):
        pixels = np.zeros(9, dtype=np.uint8)  # 3 RGB pixels
        pkt = DataPacket(0, ColorFormat.RGB, pixels)
        assert pkt.pixel_count == 3

    def test_unsupported_encoding_raises(self):
        pixels = np.array([[1, 2, 3]], dtype=np.uint8)
        pkt = DataPacket(0, ColorFormat.RGB, pixels, encoding=Encoding.DELTA)
        with pytest.raises(ProtocolError, match="Unsupported encoding"):
            pkt.to_bytes()

    def test_rle_single_pixel_runs(self):
        pixels = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.uint8)
        pkt = DataPacket(0, ColorFormat.RGB, pixels, encoding=Encoding.RLE)
        data = pkt.to_bytes()
        restored = DataPacket.from_bytes(data)
        np.testing.assert_array_equal(restored.pixel_data, pixels)


# --- ScalarDataPacket tests ---


class TestScalarDataPacket:
    def test_float32_roundtrip(self):
        channels = [1.0, 2.5, -0.5, 0.0]
        pkt = ScalarDataPacket(1, ScalarFormat.FLOAT32, channels)
        data = pkt.to_bytes()
        restored = ScalarDataPacket.from_bytes(data)

        assert restored.scalar_format == ScalarFormat.FLOAT32
        assert restored.channel_count == 4
        np.testing.assert_array_almost_equal(restored.channel_data, channels)

    def test_int16_roundtrip(self):
        channels = [0, 32767, -32768, 100]
        pkt = ScalarDataPacket(2, ScalarFormat.INT16, channels)
        data = pkt.to_bytes()
        restored = ScalarDataPacket.from_bytes(data)

        assert restored.scalar_format == ScalarFormat.INT16
        np.testing.assert_array_equal(restored.channel_data, channels)

    def test_uint8_roundtrip(self):
        channels = [0, 128, 255]
        pkt = ScalarDataPacket(3, ScalarFormat.UINT8, channels)
        data = pkt.to_bytes()
        restored = ScalarDataPacket.from_bytes(data)

        assert restored.scalar_format == ScalarFormat.UINT8
        np.testing.assert_array_equal(restored.channel_data, channels)

    def test_boolean_roundtrip(self):
        channels = [True, False, True, True, False, False, True, False, True]
        pkt = ScalarDataPacket(4, ScalarFormat.BOOLEAN, channels)
        data = pkt.to_bytes()
        restored = ScalarDataPacket.from_bytes(data)

        assert restored.scalar_format == ScalarFormat.BOOLEAN
        assert restored.channel_count == 9
        np.testing.assert_array_equal(restored.channel_data, channels)

    def test_scalar_flag_set(self):
        pkt = ScalarDataPacket(0, ScalarFormat.FLOAT32, [1.0])
        data = pkt.to_bytes()

        import struct
        _, ver_flags, _, _ = struct.unpack(">HBBI", data[:8])
        flags = ver_flags & 0x0F
        assert flags & ScalarDataPacket.FLAG_SCALAR

    def test_non_scalar_packet_raises(self):
        pixels = np.array([[1, 2, 3]], dtype=np.uint8)
        pkt = DataPacket(0, ColorFormat.RGB, pixels)
        data = pkt.to_bytes()

        with pytest.raises(ProtocolError, match="Not a scalar packet"):
            ScalarDataPacket.from_bytes(data)

    def test_numpy_input(self):
        arr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        pkt = ScalarDataPacket(0, ScalarFormat.FLOAT32, arr)
        assert pkt.channel_count == 3

    def test_boolean_bit_packing(self):
        channels = [True] * 8
        pkt = ScalarDataPacket(0, ScalarFormat.BOOLEAN, channels)
        data = pkt.to_bytes()
        restored = ScalarDataPacket.from_bytes(data)
        np.testing.assert_array_equal(restored.channel_data, channels)

    def test_boolean_partial_byte(self):
        channels = [True, False, True]
        pkt = ScalarDataPacket(0, ScalarFormat.BOOLEAN, channels)
        data = pkt.to_bytes()
        restored = ScalarDataPacket.from_bytes(data)
        assert restored.channel_count == 3
        np.testing.assert_array_equal(restored.channel_data, channels)
