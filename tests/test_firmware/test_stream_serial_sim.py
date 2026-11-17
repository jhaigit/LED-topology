"""The real host code against the real AVR firmware, on a simulated ATmega328P.

arduino/ltp_stream_serial is run in the avr8js simulator with its UART
bridged to a pseudo-terminal, and ltp_serial_cli / ltp_serial_sink open that
terminal as they would a USB serial port. This is the nearest thing to
plugging a board in: it shows that the firmware and the host agree on the
protocol, including the host's connection sequence.

Skipped unless the pieces are present. To have them:

    cd arduino/ltp_stream_serial && make build-uno && (cd sim && npm install)

WS2812 timing is checked by `make sim-test` in that directory, not here.
"""

import os
import shutil
import subprocess
import threading
from pathlib import Path

import numpy as np
import pytest

from ltp_serial_cli.device import LtpDevice
from ltp_serial_cli.exceptions import LtpDeviceError
from ltp_serial_cli.protocol import CTRL_ENUM, CTRL_UINT16
from ltp_serial_sink.v2_renderer import V2Renderer, V2RendererConfig

SKETCH = Path(__file__).resolve().parents[2] / "arduino" / "ltp_stream_serial"
HEX = SKETCH / "build" / "uno" / "ltp_stream_serial.ino.hex"
SIM = SKETCH / "sim"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not HEX.exists()
    or not (SIM / "node_modules" / "avr8js").exists(),
    reason="needs node, sim/node_modules and the Uno build of ltp_stream_serial",
)

# Seconds the host waits for the HELLO that the firmware sent at power-up,
# before this test had the port open. Shortened from the 4 s default.
HELLO_WAIT = 1.0

FIRE = 3
CTRL_ID_BRIGHTNESS, CTRL_ID_LOCAL_MODE, CTRL_ID_NUM_PIXELS, CTRL_ID_HUE = 0, 6, 8, 16


@pytest.fixture
def port():
    """A serial port with the simulated firmware on the other end."""
    master, slave = os.openpty()
    sim = subprocess.Popen(["node", str(SIM / "serve_sim.js"), str(HEX)],
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def device_to_host():
        while True:
            data = sim.stdout.read1(4096)
            if not data:
                return
            os.write(master, data)

    def host_to_device():
        try:
            while True:
                data = os.read(master, 4096)
                if not data:
                    return
                sim.stdin.write(data)
                sim.stdin.flush()
        except (OSError, ValueError):
            return  # the pseudo-terminal or the simulator has gone

    threads = [threading.Thread(target=device_to_host, daemon=True),
               threading.Thread(target=host_to_device, daemon=True)]
    for t in threads:
        t.start()
    try:
        yield os.ttyname(slave)
    finally:
        sim.kill()
        sim.wait()
        os.close(slave)
        os.close(master)


def test_cli_library_drives_the_firmware(port):
    with LtpDevice(port, timeout=HELLO_WAIT) as device:
        info = device.info
        assert info.device_name == "LTP-Stream-328P"
        assert info.total_pixels == 60
        assert not info.is_matrix
        assert not info.accepts_pixels
        assert info.has_brightness

        controls = {c["name"]: c for c in device.get_controls()}
        assert len(controls) == info.control_count == 20
        assert controls["local_mode"]["type"] == CTRL_ENUM
        assert [o["label"] for o in controls["local_mode"]["enum_values"]][:4] == [
            "Off", "Scanner", "Rainbow", "Fire"]
        assert controls["num_pixels"]["value"] == 60
        assert controls["num_pixels"]["max"] == 1000

        assert device.ping()
        assert device.get_build_info().firmware_name == "ltp-stream"
        assert device.get_status().brightness == 64

        device.set_control(CTRL_ID_LOCAL_MODE, FIRE, CTRL_ENUM)
        assert device.get_control(CTRL_ID_LOCAL_MODE) == FIRE
        device.set_control(CTRL_ID_NUM_PIXELS, 144, CTRL_UINT16)
        assert device.get_control(CTRL_ID_NUM_PIXELS, CTRL_UINT16) == 144
        assert device.refresh_info().total_pixels == 144

        with pytest.raises(LtpDeviceError):
            device.set_control(CTRL_ID_LOCAL_MODE, 77, CTRL_ENUM)

        # Read-back: fire is hot at the base of the strip and dark at the far end.
        pixels = device.get_pixels(0, 144)
        assert len(pixels) == 144 * 3
        assert sum(pixels[:30]) > 0
        assert sum(pixels[-30:]) == 0

        device.set_name("Hall strip")
        assert device.refresh_info().device_name == "Hall strip"

        assert device.get_stats().frames_displayed > 0


def test_pixel_commands_are_ignored_without_upsetting_the_link(port):
    with LtpDevice(port, timeout=HELLO_WAIT) as device:
        device.set_control(CTRL_ID_LOCAL_MODE, 0, CTRL_ENUM)  # Off: no frames in the way
        before = device.get_stats().frames_received
        device.set_pixels(bytes(range(180)))
        device.fill(255, 0, 0)
        device.show()
        # No reply to any of them, and the next command is answered as usual.
        assert device.get_control(CTRL_ID_BRIGHTNESS) == 64
        assert device.get_stats().frames_received - before == 3


def test_serial_sink_renderer_connects_and_sends_no_pixels(port):
    """V2Renderer.open() is the connection sequence ltp-serial-sink runs."""
    renderer = V2Renderer(V2RendererConfig(port=port, timeout=HELLO_WAIT))
    renderer.open()
    try:
        assert renderer.is_connected()
        assert not renderer.accepts_pixels
        names = {c.name for c in renderer.controls.values()}
        assert {"brightness", "local_mode", "num_pixels", "speed", "palette", "save"} <= names
        assert renderer.controls[CTRL_ID_LOCAL_MODE].control_type == "enum"

        assert renderer.set_control(CTRL_ID_HUE, 200)
        assert renderer.get_control(CTRL_ID_HUE) == 200
        # A refused value is reported, not silently accepted.
        assert not renderer.set_control(CTRL_ID_LOCAL_MODE, 77)
    finally:
        renderer.close()


def test_renderer_drops_frames_for_the_device(port):
    renderer = V2Renderer(V2RendererConfig(port=port, timeout=HELLO_WAIT))
    renderer.open()
    try:
        before = renderer._device.get_stats().frames_received
        assert renderer.render(np.full((60, 3), 255, dtype=np.uint8)) == 0
        assert renderer._device.get_stats().frames_received == before
    finally:
        renderer.close()
