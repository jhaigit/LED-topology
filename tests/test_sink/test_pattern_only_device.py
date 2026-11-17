"""A serial device that generates its own patterns (ltp_stream_serial firmware)
says so with CAPS_NO_PIXEL_INPUT. The host must then leave pixel data out:
the device would only drop it, and on a UART board a stream of frames it
cannot use crowds out the control commands it can.
"""

from types import SimpleNamespace

import numpy as np

from libltp import Message, MessageType
from ltp_serial_cli.device import DeviceInfo
from ltp_serial_cli.protocol import (
    CAPS_BRIGHTNESS,
    CAPS_EEPROM,
    CAPS_EXTENDED,
    CAPS_NO_PIXEL_INPUT,
    CAPS_PIXEL_READBACK,
)
from ltp_serial_sink.sink import SerialSink, SerialSinkConfig
from ltp_serial_sink.v2_renderer import V2Renderer, V2RendererConfig

# What ltp_stream_serial reports in HELLO and INFO_ALL.
PATTERN_ONLY = DeviceInfo(
    total_pixels=8,
    capabilities1=CAPS_BRIGHTNESS | CAPS_EXTENDED,
    capabilities2=CAPS_PIXEL_READBACK | CAPS_EEPROM | CAPS_NO_PIXEL_INPUT,
)
# What a buffered sketch such as ltp_328p_dual reports.
ORDINARY = DeviceInfo(
    total_pixels=8,
    capabilities1=CAPS_BRIGHTNESS | CAPS_EXTENDED,
    capabilities2=CAPS_PIXEL_READBACK | CAPS_EEPROM,
)


class FakeDevice:
    """Records the pixel commands a renderer sends."""

    is_connected = True

    def __init__(self):
        self.calls = []

    def set_pixels(self, data):
        self.calls.append("set_pixels")

    def fill(self, r, g, b):
        self.calls.append("fill")

    def show(self):
        self.calls.append("show")


def _renderer(info):
    renderer = V2Renderer(V2RendererConfig(port="/dev/fake", auto_show=False))
    renderer._device = FakeDevice()
    renderer._connected = True
    renderer._device_info = info
    return renderer


class TestCapabilityFlag:
    def test_ordinary_device_accepts_pixels(self):
        assert ORDINARY.accepts_pixels
        assert DeviceInfo().accepts_pixels

    def test_pattern_only_device_does_not(self):
        assert not PATTERN_ONLY.accepts_pixels

    def test_flag_counts_only_with_the_extended_byte(self):
        # Without CAPS_EXTENDED the second capability byte is not sent, so
        # whatever sits there means nothing.
        assert DeviceInfo(capabilities1=CAPS_BRIGHTNESS,
                          capabilities2=CAPS_NO_PIXEL_INPUT).accepts_pixels


class TestRenderer:
    frame = np.full((8, 3), 200, dtype=np.uint8)

    def test_frames_go_to_an_ordinary_device(self):
        renderer = _renderer(ORDINARY)
        assert renderer.render(self.frame) == 24
        assert renderer.fill(1, 2, 3)
        assert renderer.show()
        assert renderer._device.calls == ["set_pixels", "show", "fill", "show", "show"]

    def test_nothing_is_sent_to_a_pattern_only_device(self):
        renderer = _renderer(PATTERN_ONLY)
        assert renderer.render(self.frame) == 0
        assert not renderer.fill(1, 2, 3)
        assert not renderer.show()
        assert renderer._device.calls == []

    def test_accepts_pixels_before_any_device_is_known(self):
        renderer = V2Renderer(V2RendererConfig(port="/dev/fake"))
        assert renderer.accepts_pixels


class FakeRenderer:
    """Just enough of V2Renderer for SerialSink's message handlers."""

    def __init__(self, accepts_pixels):
        self.accepts_pixels = accepts_pixels
        self.device_info = SimpleNamespace(device_name="LTP-Stream-328P", firmware_version="1.0")
        self.build_info = None
        self.controls = {}
        self.inputs = {}

    def is_connected(self):
        return True

    def get_control(self, ctrl_id):
        return None

    def set_input_event_callback(self, cb):
        pass


def _sink(accepts_pixels):
    config = SerialSinkConfig(name="Sink", port="/dev/fake", pixels=8)
    sink = SerialSink(config, renderer=FakeRenderer(accepts_pixels))
    sink._setup_device_controls()
    return sink


class TestSink:
    def test_stream_setup_is_refused_for_a_pattern_only_device(self):
        reply = _sink(accepts_pixels=False)._handle_stream_setup(
            Message(MessageType.STREAM_SETUP, 7))
        assert reply.type == MessageType.STREAM_SETUP_RESPONSE
        assert reply.seq == 7
        # The controller treats any status but "ok" as a failed route.
        assert reply.data["status"] == "error"
        assert reply.data["error"] == "NOT_SUPPORTED"
        assert "udp_port" not in reply.data

    def test_stream_setup_still_works_for_an_ordinary_device(self):
        reply = _sink(accepts_pixels=True)._handle_stream_setup(
            Message(MessageType.STREAM_SETUP, 8))
        assert reply.data["status"] == "ok"

    def test_capabilities_say_there_is_no_pixel_input(self):
        device = _sink(accepts_pixels=False)._handle_capability_request(
            Message(MessageType.CAPABILITY_REQUEST, 1)).data["device"]
        assert device["pixel_input"] is False

    def test_capabilities_unchanged_for_an_ordinary_device(self):
        device = _sink(accepts_pixels=True)._handle_capability_request(
            Message(MessageType.CAPABILITY_REQUEST, 1)).data["device"]
        assert "pixel_input" not in device
