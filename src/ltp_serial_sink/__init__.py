"""LTP Serial Sink - LED data sink with serial output backend.

Uses LTP Serial Protocol v2 for communication with microcontrollers.
"""

from ltp_serial_sink.v2_renderer import V2Renderer, V2RendererConfig, DeviceControl
from ltp_serial_sink.sink import SerialSink, SerialSinkConfig

__all__ = [
    "V2Renderer",
    "V2RendererConfig",
    "DeviceControl",
    "SerialSink",
    "SerialSinkConfig",
]
