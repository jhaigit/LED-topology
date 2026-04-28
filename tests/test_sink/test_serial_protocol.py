"""Unit tests for ltp_serial_cli LtpProtocol packet building and parsing."""

import struct

import pytest

from ltp_serial_cli.protocol import (
    CMD_ACK,
    CMD_FRAME_ACK,
    CMD_GET_CONTROL,
    CMD_GET_INFO,
    CMD_GET_PIXELS,
    CMD_HELLO,
    CMD_NOP,
    CMD_PIXEL_FRAME,
    CMD_PIXEL_SET_ALL,
    CMD_PIXEL_SET_RANGE,
    CMD_RESET,
    CMD_SET_CONTROL,
    CMD_SHOW,
    FLAG_ACK_REQ,
    FLAG_ERROR,
    FLAG_RESPONSE,
    INFO_ALL,
    INFO_BUILD,
    INFO_CONTROLS,
    LTP_MAX_PAYLOAD,
    LTP_START_BYTE,
    LtpPacket,
    LtpProtocol,
    STRIP_ALL,
)


class TestLtpPacket:
    def test_is_response(self):
        pkt = LtpPacket(cmd=CMD_ACK, flags=FLAG_RESPONSE)
        assert pkt.is_response is True

        pkt2 = LtpPacket(cmd=CMD_ACK, flags=0)
        assert pkt2.is_response is False

    def test_is_error(self):
        pkt = LtpPacket(cmd=CMD_ACK, flags=FLAG_ERROR)
        assert pkt.is_error is True

    def test_ack_requested(self):
        pkt = LtpPacket(cmd=CMD_NOP, flags=FLAG_ACK_REQ)
        assert pkt.ack_requested is True

    def test_command_name(self):
        pkt = LtpPacket(cmd=CMD_HELLO)
        assert pkt.command_name == "HELLO"

    def test_unknown_command_name(self):
        pkt = LtpPacket(cmd=0xFE)
        assert "UNKNOWN" in pkt.command_name

    def test_repr(self):
        pkt = LtpPacket(cmd=CMD_HELLO, payload=b"\x01\x02", flags=FLAG_RESPONSE)
        r = repr(pkt)
        assert "HELLO" in r
        assert "RSP" in r


class TestBuildPacket:
    def test_minimal_packet(self):
        data = LtpProtocol.build_packet(CMD_NOP)
        assert data[0] == LTP_START_BYTE
        assert data[4] == CMD_NOP
        assert len(data) == 6  # start + flags + len(2) + cmd + checksum

    def test_packet_with_payload(self):
        payload = bytes([0x01, 0x02, 0x03])
        data = LtpProtocol.build_packet(CMD_PIXEL_FRAME, payload)
        length_lo = data[2]
        length_hi = data[3]
        length = length_lo | (length_hi << 8)
        assert length == 3

    def test_packet_with_flags(self):
        data = LtpProtocol.build_packet(CMD_NOP, flags=FLAG_ACK_REQ)
        assert data[1] == FLAG_ACK_REQ

    def test_checksum_is_xor(self):
        data = LtpProtocol.build_packet(CMD_NOP)
        checksum = 0
        for b in data[1:-1]:
            checksum ^= b
        assert data[-1] == checksum

    def test_checksum_with_payload(self):
        payload = bytes([0xFF, 0x80, 0x01])
        data = LtpProtocol.build_packet(CMD_PIXEL_FRAME, payload)
        checksum = 0
        for b in data[1:-1]:
            checksum ^= b
        assert data[-1] == checksum

    def test_payload_too_large(self):
        with pytest.raises(ValueError, match="too large"):
            LtpProtocol.build_packet(CMD_NOP, b"\x00" * (LTP_MAX_PAYLOAD + 1))

    def test_max_payload_ok(self):
        data = LtpProtocol.build_packet(CMD_NOP, b"\x00" * LTP_MAX_PAYLOAD)
        assert len(data) == 6 + LTP_MAX_PAYLOAD


class TestFeedAndParse:
    def setup_method(self):
        self.protocol = LtpProtocol()

    def test_parse_complete_packet(self):
        raw = LtpProtocol.build_packet(CMD_HELLO, b"\x02\x00")
        packets = self.protocol.feed(raw)
        assert len(packets) == 1
        assert packets[0].cmd == CMD_HELLO
        assert packets[0].payload == b"\x02\x00"

    def test_parse_empty_payload(self):
        raw = LtpProtocol.build_packet(CMD_NOP)
        packets = self.protocol.feed(raw)
        assert len(packets) == 1
        assert packets[0].cmd == CMD_NOP
        assert packets[0].payload == b""

    def test_parse_multiple_packets(self):
        pkt1 = LtpProtocol.build_packet(CMD_NOP)
        pkt2 = LtpProtocol.build_packet(CMD_HELLO, b"\x01")
        pkt3 = LtpProtocol.build_packet(CMD_ACK)

        packets = self.protocol.feed(pkt1 + pkt2 + pkt3)
        assert len(packets) == 3
        assert packets[0].cmd == CMD_NOP
        assert packets[1].cmd == CMD_HELLO
        assert packets[2].cmd == CMD_ACK

    def test_parse_partial_then_complete(self):
        raw = LtpProtocol.build_packet(CMD_HELLO, b"\x01\x02\x03")

        packets = self.protocol.feed(raw[:3])
        assert len(packets) == 0

        packets = self.protocol.feed(raw[3:])
        assert len(packets) == 1
        assert packets[0].cmd == CMD_HELLO

    def test_garbage_before_packet(self):
        garbage = bytes([0x55, 0x66, 0x77])
        raw = LtpProtocol.build_packet(CMD_NOP)
        packets = self.protocol.feed(garbage + raw)
        assert len(packets) == 1
        assert packets[0].cmd == CMD_NOP

    def test_bad_checksum_discarded(self):
        raw = bytearray(LtpProtocol.build_packet(CMD_NOP))
        raw[-1] ^= 0xFF  # corrupt checksum
        packets = self.protocol.feed(bytes(raw))
        assert len(packets) == 0

    def test_flags_preserved(self):
        raw = LtpProtocol.build_packet(CMD_ACK, flags=FLAG_RESPONSE | FLAG_ACK_REQ)
        packets = self.protocol.feed(raw)
        assert len(packets) == 1
        assert packets[0].flags == (FLAG_RESPONSE | FLAG_ACK_REQ)
        assert packets[0].is_response is True
        assert packets[0].ack_requested is True

    def test_reset_clears_buffer(self):
        partial = LtpProtocol.build_packet(CMD_HELLO, b"\x01")[:3]
        self.protocol.feed(partial)
        self.protocol.reset()

        full = LtpProtocol.build_packet(CMD_NOP)
        packets = self.protocol.feed(full)
        assert len(packets) == 1
        assert packets[0].cmd == CMD_NOP


class TestConvenienceBuilders:
    def setup_method(self):
        self.protocol = LtpProtocol()

    def _roundtrip(self, raw: bytes) -> LtpPacket:
        packets = self.protocol.feed(raw)
        assert len(packets) == 1
        return packets[0]

    def test_build_nop(self):
        pkt = self._roundtrip(LtpProtocol.build_nop())
        assert pkt.cmd == CMD_NOP
        assert pkt.ack_requested is False

    def test_build_nop_with_ack(self):
        pkt = self._roundtrip(LtpProtocol.build_nop(ack_request=True))
        assert pkt.cmd == CMD_NOP
        assert pkt.ack_requested is True

    def test_build_reset(self):
        pkt = self._roundtrip(LtpProtocol.build_reset())
        assert pkt.cmd == CMD_RESET

    def test_build_show(self):
        pkt = self._roundtrip(LtpProtocol.build_show(frame_number=42))
        assert pkt.cmd == CMD_SHOW
        frame_num = struct.unpack("<H", pkt.payload)[0]
        assert frame_num == 42

    def test_build_get_info(self):
        pkt = self._roundtrip(LtpProtocol.build_get_info(INFO_ALL))
        assert pkt.cmd == CMD_GET_INFO
        assert pkt.payload == bytes([INFO_ALL])

    def test_build_get_info_build(self):
        pkt = self._roundtrip(LtpProtocol.build_get_info(INFO_BUILD))
        assert pkt.payload == bytes([INFO_BUILD])

    def test_build_get_pixels(self):
        pkt = self._roundtrip(LtpProtocol.build_get_pixels(0, 10, 50))
        assert pkt.cmd == CMD_GET_PIXELS
        strip_id, start, count = struct.unpack("<BHH", pkt.payload)
        assert strip_id == 0
        assert start == 10
        assert count == 50

    def test_build_get_control(self):
        pkt = self._roundtrip(LtpProtocol.build_get_control(5))
        assert pkt.cmd == CMD_GET_CONTROL
        assert pkt.payload == bytes([5])

    def test_build_pixel_set_all(self):
        pkt = self._roundtrip(LtpProtocol.build_pixel_set_all(255, 0, 128))
        assert pkt.cmd == CMD_PIXEL_SET_ALL
        assert pkt.payload == bytes([STRIP_ALL, 255, 0, 128])

    def test_build_pixel_set_all_custom_strip(self):
        pkt = self._roundtrip(LtpProtocol.build_pixel_set_all(10, 20, 30, strip_id=2))
        assert pkt.payload[0] == 2
        assert pkt.payload[1:4] == bytes([10, 20, 30])

    def test_build_pixel_set_range(self):
        pkt = self._roundtrip(LtpProtocol.build_pixel_set_range(0, 10, 20, 255, 128, 64))
        assert pkt.cmd == CMD_PIXEL_SET_RANGE
        strip_id, start, end = struct.unpack("<BHH", pkt.payload[:5])
        assert strip_id == 0
        assert start == 10
        assert end == 20
        assert pkt.payload[5:8] == bytes([255, 128, 64])

    def test_build_pixel_frame(self):
        pixel_data = bytes([255, 0, 0, 0, 255, 0])  # 2 RGB pixels
        pkt = self._roundtrip(LtpProtocol.build_pixel_frame(0, 0, pixel_data))
        assert pkt.cmd == CMD_PIXEL_FRAME

    def test_build_set_control_uint8(self):
        pkt = self._roundtrip(LtpProtocol.build_set_control_uint8(0, 200))
        assert pkt.cmd == CMD_SET_CONTROL

    def test_build_set_control_bool(self):
        pkt = self._roundtrip(LtpProtocol.build_set_control_bool(3, True))
        assert pkt.cmd == CMD_SET_CONTROL
