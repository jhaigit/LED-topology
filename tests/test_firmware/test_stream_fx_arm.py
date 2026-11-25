"""The pattern engine as the Teensy compiler builds it, run in a CPU emulator.

No Teensy is simulated here - only its processor. The engine (which has no
hardware dependency) is compiled with the Teensy core's own compiler and the
board's flags, and executed in Unicorn. See arm_emu.py.

- The frames must equal the host build's, byte for byte. That covers what
  differs between the two: `char` is unsigned on ARM, `long` is 32 bits, and
  it is a different compiler at a different optimisation level.
- On the Teensy 4.0 every pattern must fit the time a WS2812 pixel takes on
  the wire, with room to spare. Measured in instructions, since the emulator
  does not model time; `python tests/test_firmware/arm_emu.py` prints the
  full table.

Skipped unless the Teensy core is installed (make setup-teensy in
arduino/ltp_stream_serial) and Unicorn is (pip install unicorn).
"""

import shutil

import pytest

from . import arm_emu
from .fx_host import build_harness, run_job

pytest.importorskip("unicorn", reason="Unicorn CPU emulator not installed (pip install unicorn)")

pytestmark = [
    pytest.mark.skipif(arm_emu.find_compiler() is None, reason="Teensy core (arm-none-eabi-g++) not installed"),
    pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed"),
]

BASE_EFFECTS = list(range(0, 13))
EXTENDED_EFFECTS = list(range(13, 19))
TIME_PER_MS = 256

# Strip and matrix, default and awkward settings, a few moments each.
SERPENTINE_FROM_BOTTOM = 5
COMPARE_JOBS = [
    # n, width, layout, brightness, scale, palette, hue, saturation, intensity, count, reverse
    (97, 1, 0, 255, 32, 0, 0, 255, 128, 2, 0),
    (96, 12, SERPENTINE_FROM_BOTTOM, 200, 90, 0, 40, 255, 255, 8, 1),
    (300, 1, 0, 255, 8, 1, 170, 200, 30, 5, 0),
    (64, 8, 2, 77, 255, 7, 0, 255, 200, 1, 0),
]
COMPARE_FRAMES = 5
COMPARE_STEP = 613 * TIME_PER_MS
COMPARE_START = 31337 * TIME_PER_MS

# A pattern may use this share of the pixel time when its instructions are
# counted at the pessimistic rate in arm_emu.py. Half: twice as much again
# in hand for what an instruction count cannot show.
MOST_OF_BUDGET = 0.5


def jobs(effect):
    for n, width, layout, *rest in COMPARE_JOBS:
        yield [n, width, layout, effect, *rest, COMPARE_START, COMPARE_STEP, COMPARE_FRAMES]


@pytest.fixture(scope="module")
def teensy40(tmp_path_factory):
    return arm_emu.Image(arm_emu.build("teensy40", tmp_path_factory.mktemp("arm40")))


@pytest.fixture(scope="module")
def teensy32(tmp_path_factory):
    return arm_emu.Image(arm_emu.build("teensy32", tmp_path_factory.mktemp("arm32")))


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    return build_harness(tmp_path_factory.mktemp("fx_host"), extended=False)


@pytest.fixture(scope="module")
def host_extended(tmp_path_factory):
    return build_harness(tmp_path_factory.mktemp("fx_host_ext"), extended=True)


@pytest.mark.parametrize("effect", BASE_EFFECTS + EXTENDED_EFFECTS)
def test_teensy40_build_draws_the_same_frames_as_the_host_build(teensy40, host_extended, effect):
    for job in jobs(effect):
        out, _ = arm_emu.run(teensy40, "cortex-m7", job)
        assert out == run_job(host_extended, job), job


@pytest.mark.parametrize("effect", BASE_EFFECTS)
def test_teensy32_build_draws_the_same_frames_as_the_host_build(teensy32, host, effect):
    for job in jobs(effect):
        out, _ = arm_emu.run(teensy32, "cortex-m4", job)
        assert out == run_job(host, job), job


def test_teensy40_build_has_the_extended_patterns_and_teensy32_has_not(teensy40, teensy32):
    """The board macro alone decides: nothing in a sketch has to ask for them."""
    job = [60, 1, 0, 14, 255, 32, 0, 0, 255, 128, 3, 0, COMPARE_START, COMPARE_STEP, 4]
    with_them, _ = arm_emu.run(teensy40, "cortex-m7", job)
    without, _ = arm_emu.run(teensy32, "cortex-m4", job)
    assert any(with_them)
    assert not any(without)


@pytest.mark.parametrize("geometry", list(arm_emu.MEASURE_GEOMETRIES))
@pytest.mark.parametrize("effect", list(arm_emu.EFFECT_NAMES))
def test_teensy40_pattern_fits_the_pixel_time(teensy40, effect, geometry):
    """Heaviest settings, heaviest frame: at most half the 18 000 cycles a pixel allows."""
    result = arm_emu.measure(teensy40, "cortex-m7", effect, geometry,
                             settings_list=arm_emu.HEAVY_SETTINGS[:2], frames=4)
    cycles = arm_emu.pessimistic_cycles(result)
    assert cycles <= MOST_OF_BUDGET * arm_emu.TEENSY40_CYCLES_PER_PIXEL, (
        f"{arm_emu.EFFECT_NAMES[effect]} on a {geometry}: {result['instructions']:.0f} instructions "
        f"and {result['divides']:.1f} divides per pixel")
    # One pixel may take longer than its share - the driver's ring holds 21 -
    # but not by so much that a single pixel could empty it.
    assert result["worst_pixel"] * arm_emu.PESSIMISTIC_CYCLES_PER_INSTRUCTION \
        <= 4 * arm_emu.TEENSY40_CYCLES_PER_PIXEL
