"""The Sinks page renders string controls (e.g. a serial device's EEPROM
`device_name`) as a text input with an explicit Apply button, wired to the
existing PUT /api/sinks/<id>/controls endpoint."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from libltp.controls import StringControl
from ltp_controller.web.app import create_app

DEV = "dev-1"


def _make_client(controls, control_values):
    sink = SimpleNamespace(
        id=DEV,
        auth_state="none",
        online=True,
        properties={},
        name="Hall Strip",
        description="serial device on /dev/ttyUSB0",
        host="10.0.1.9",
        port=5000,
        backend_connected=True,
        device=SimpleNamespace(host="10.0.1.9", properties={"pixels": "80", "dim": "80"}),
        capabilities={},
        controls=controls,
        control_values=control_values,
        to_dict=lambda: {"id": DEV, "name": "Hall Strip"},
    )
    controller = MagicMock()
    controller.sinks = [sink]
    controller.sources = []
    controller.fleets = []
    controller.get_sink = lambda sid: sink if sid == DEV else None
    controller._connection_pool = None
    app = create_app(controller=controller, router=MagicMock(routes=[]))
    return app.test_client()


@pytest.fixture()
def client():
    ctrl = StringControl(
        id="device_name",
        name="Device Name",
        description="Name stored in EEPROM",
        value="Hall Strip",
        maxLength=15,
        group="hardware",
    ).to_dict()
    return _make_client([ctrl], {"device_name": "Hall Strip"})


def test_string_control_renders_text_input_with_apply(client):
    page = client.get("/sinks").get_data(as_text=True)
    assert 'type="text"' in page
    assert 'value="Hall Strip"' in page
    assert 'maxlength="15"' in page
    assert ">Apply<" in page
    assert "applyStringControl(this, 'dev-1', 'device_name', 'Device Name')" in page
    # The description is surfaced as a tooltip on the label.
    assert 'title="Name stored in EEPROM"' in page


def test_string_control_without_limits_renders(client):
    ctrl = StringControl(id="label", name="Label", description="", value="x").to_dict()
    c = _make_client([ctrl], {})
    page = c.get("/sinks").get_data(as_text=True)
    assert 'value="x"' in page
    assert "maxlength" not in page


def test_bounded_number_control_renders_live_slider():
    from libltp.controls import NumberControl

    bounded = NumberControl(
        id="brightness", name="brightness", description="", value=128, min=0, max=255, step=1
    ).to_dict()
    free = NumberControl(id="free", name="free", description="", value=5).to_dict()
    c = _make_client([bounded, free], {"brightness": 128, "free": 5})
    page = c.get("/sinks").get_data(as_text=True)
    # Bounded: a range input driven on every input event through the
    # coalescing sender, with the integer bounds intact.
    assert 'type="range"' in page
    assert 'max="255"' in page
    assert "setControlLive('dev-1', 'brightness', parseInt(this.value))" in page
    # Unbounded: still a plain number box, applied on change.
    assert "setControl('dev-1', 'free', parseInt(this.value))" in page
