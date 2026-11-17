"""The serial CLI's `control` command: read, set or trigger any device control.

It is the standalone way to drive firmware whose only interface is its
controls (ltp_stream_serial).
"""

from types import SimpleNamespace

import pytest

from ltp_serial_cli.__main__ import (
    cmd_control,
    find_control,
    parse_control_value,
)
from ltp_serial_cli.exceptions import LtpError
from ltp_serial_cli.protocol import CTRL_ACTION, CTRL_BOOL, CTRL_ENUM, CTRL_UINT8, CTRL_UINT16

CONTROLS = [
    {"id": 0, "type": CTRL_UINT8, "name": "brightness", "value": 64, "min": 0, "max": 255},
    {"id": 6, "type": CTRL_ENUM, "name": "local_mode", "value": 2, "min": 0, "max": 255,
     "enum_values": [{"value": 2, "label": "Rainbow"}, {"value": 3, "label": "Fire"},
                     {"value": 255, "label": "Cycle"}]},
    {"id": 8, "type": CTRL_UINT16, "name": "num_pixels", "value": 60, "min": 1, "max": 1000},
    {"id": 20, "type": CTRL_BOOL, "name": "reverse", "value": 0, "min": 0, "max": 1},
    {"id": 0xF0, "type": CTRL_ACTION, "name": "save", "value": 0, "min": 0, "max": 0},
]


class FakeDevice:
    def __init__(self):
        self.sets = []

    def get_controls(self):
        return CONTROLS

    def set_control(self, control_id, value, control_type):
        self.sets.append((control_id, value, control_type))


def run(control, value=None):
    device = FakeDevice()
    cmd_control(device, SimpleNamespace(control=control, value=value))
    return device.sets


def test_find_by_name_and_by_id():
    assert find_control(CONTROLS, "num_pixels")["id"] == 8
    assert find_control(CONTROLS, "6")["name"] == "local_mode"
    assert find_control(CONTROLS, "0xF0")["name"] == "save"


def test_unknown_control_lists_the_names():
    with pytest.raises(LtpError, match="brightness, local_mode"):
        find_control(CONTROLS, "sparkliness")


def test_values_are_numbers_or_enum_labels():
    mode = find_control(CONTROLS, "local_mode")
    assert parse_control_value(mode, "fire") == 3
    assert parse_control_value(mode, "255") == 255
    assert parse_control_value(find_control(CONTROLS, "reverse"), "on") == 1
    assert parse_control_value(find_control(CONTROLS, "brightness"), "0x40") == 64


def test_bad_value_lists_the_options():
    with pytest.raises(LtpError, match="Rainbow, Fire, Cycle"):
        parse_control_value(find_control(CONTROLS, "local_mode"), "Disco")


def test_set_sends_the_control_with_its_own_type(capsys):
    assert run("local_mode", "Fire") == [(6, 3, CTRL_ENUM)]
    assert "local_mode = 3 (Fire)" in capsys.readouterr().out
    assert run("num_pixels", "144") == [(8, 144, CTRL_UINT16)]


def test_without_a_value_it_only_reads(capsys):
    assert run("local_mode") == []
    assert "local_mode = 2 (Rainbow)" in capsys.readouterr().out


def test_an_action_is_triggered_without_a_value(capsys):
    assert run("save") == [(0xF0, 1, CTRL_ACTION)]
    assert "save: done" in capsys.readouterr().out
