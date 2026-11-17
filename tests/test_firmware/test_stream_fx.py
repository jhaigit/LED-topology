"""Tests for the frame-buffer-free pattern engine (arduino/libraries/LtpStreamFx).

The engine is plain C++ with no Arduino dependency, so the same source the
firmware compiles is built here for the host and driven through
fx_harness.cpp. What is checked is what the firmware relies on:

- pixels sent in wiring order and pixels read back by logical index are the
  same frame, for every matrix wiring layout;
- a frame is a function of its inputs only, so nothing needs remembering;
- brightness, palette and geometry settings do what the controls promise.

The cycle-accurate behaviour on the ATmega328P is covered by the simulator
kit in arduino/ltp_stream_serial/sim (make sim-test), not here.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "arduino" / "libraries" / "LtpStreamFx" / "src"
HARNESS_SRC = Path(__file__).with_name("fx_harness.cpp")

# Effect numbers, as in ltp_fx.h.
OFF, SCANNER, RAINBOW, FIRE, SPARKLE, CHASE, SOLID = 0, 1, 2, 3, 4, 5, 6
COMET, WAVES, TWINKLE, NOISE, PLASMA, RIPPLE = 7, 8, 9, 10, 11, 12
ALL_EFFECTS = list(range(1, 13))
STATIC_EFFECTS = {SOLID}

# Palette numbers, as in ltp_fx.h.
PAL_DEFAULT, PAL_COLOR, PAL_RAINBOW = 0, 1, 2

# Matrix layout bits, as in ltp_protocol.h.
SERPENTINE, VERTICAL_FIRST, ORIGIN_BOTTOM = 1, 2, 4

# fxTime units per millisecond at the nominal speed setting.
TIME_PER_MS = 256

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    exe = tmp_path_factory.mktemp("fx") / "fx_harness"
    subprocess.run(
        ["g++", "-std=gnu++11", "-O1", "-Wall", "-Wextra", "-Werror",
         "-fsanitize=undefined", "-fno-sanitize-recover=undefined",
         f"-I{LIB}", str(HARNESS_SRC), str(LIB / "ltp_fx.cpp"), "-o", str(exe)],
        check=True,
    )
    return exe


def render(harness, effect, *, n=60, width=1, layout=0, brightness=255, scale=32,
           palette=PAL_DEFAULT, hue=0, saturation=255, intensity=128, count=2,
           reverse=0, time=5000 * TIME_PER_MS, step=0, frames=1):
    """Frames from the engine: a list of (wire_order, logical_order) pixel lists."""
    args = [n, width, layout, effect, brightness, scale, palette, hue, saturation,
            intensity, count, reverse, time, step, frames]
    raw = subprocess.run([str(harness), *map(str, args)], check=True,
                         capture_output=True).stdout
    assert len(raw) == frames * 2 * n * 3
    out = []
    for f in range(frames):
        base = f * 2 * n * 3
        wire = [tuple(raw[base + i * 3: base + i * 3 + 3]) for i in range(n)]
        logical = [tuple(raw[base + (n + i) * 3: base + (n + i) * 3 + 3]) for i in range(n)]
        out.append((wire, logical))
    return out


def map_pixel(idx, n, width, layout):
    """Logical index to wired position: mapPixel() of ltp_328p_dual.ino."""
    if width <= 1:
        return idx
    height = n // width
    if layout & VERTICAL_FIRST:
        col, row = divmod(idx, height)
        if layout & ORIGIN_BOTTOM:
            row = height - 1 - row
        if (layout & SERPENTINE) and (col & 1):
            row = height - 1 - row
        return col * height + row
    row, col = divmod(idx, width)
    if layout & ORIGIN_BOTTOM:
        row = height - 1 - row
    if (layout & SERPENTINE) and (row & 1):
        col = width - 1 - col
    return row * width + col


@pytest.mark.parametrize("layout", range(8))
@pytest.mark.parametrize("n,width", [(256, 16), (96, 12), (96, 8), (60, 1), (35, 7)])
def test_wire_order_matches_logical_order(harness, layout, n, width):
    """The frame sent down the wire is the frame read back, for every wiring."""
    for effect in ALL_EFFECTS:
        wire, logical = render(harness, effect, n=n, width=width, layout=layout, scale=90)[0]
        for idx in range(n):
            assert wire[map_pixel(idx, n, width, layout)] == logical[idx], (effect, idx)


def test_matrix_layout_moves_pixels(harness):
    """A serpentine matrix really is wired differently from a plain one."""
    plain, _ = render(harness, RAINBOW, n=64, width=8, layout=0, scale=128)[0]
    serpentine, _ = render(harness, RAINBOW, n=64, width=8, layout=SERPENTINE, scale=128)[0]
    assert plain[:8] == serpentine[:8]
    assert plain[8:16] == serpentine[8:16][::-1]


def test_width_that_does_not_divide_is_a_strip(harness):
    strip = render(harness, PLASMA, n=60, width=1)[0]
    assert render(harness, PLASMA, n=60, width=7, layout=SERPENTINE)[0] == strip


def test_off_is_black(harness):
    wire, logical = render(harness, OFF)[0]
    assert set(wire) == {(0, 0, 0)} and set(logical) == {(0, 0, 0)}


@pytest.mark.parametrize("effect", ALL_EFFECTS)
def test_zero_brightness_is_black(harness, effect):
    wire, _ = render(harness, effect, brightness=0)[0]
    assert set(wire) == {(0, 0, 0)}


@pytest.mark.parametrize("effect", ALL_EFFECTS)
def test_effect_lights_and_moves(harness, effect):
    frames = [wire for wire, _ in render(harness, effect, n=120, step=137 * TIME_PER_MS, frames=6)]
    assert all(any(p != (0, 0, 0) for p in f) for f in frames), "a frame is all black"
    if effect in STATIC_EFFECTS:
        assert all(f == frames[0] for f in frames)
    else:
        assert any(f != frames[0] for f in frames[1:]), "the pattern never changes"


@pytest.mark.parametrize("effect", ALL_EFFECTS)
def test_frame_depends_only_on_its_inputs(harness, effect):
    """No hidden state: rendering other frames in between changes nothing."""
    t = 123456789
    alone = render(harness, effect, n=100, time=t)[0]
    after_others = render(harness, effect, n=100, time=t - 3 * 999, step=999, frames=4)[3]
    assert alone == after_others


@pytest.mark.parametrize("effect", ALL_EFFECTS)
def test_brightness_only_dims(harness, effect):
    full, _ = render(harness, effect, n=100, brightness=255)[0]
    half, _ = render(harness, effect, n=100, brightness=128)[0]
    assert all(h <= f for hp, fp in zip(half, full) for h, f in zip(hp, fp))
    assert sum(map(sum, half)) < sum(map(sum, full))


def test_solid_colour_follows_hue_and_saturation(harness):
    red, _ = render(harness, SOLID, hue=0, saturation=255)[0]
    assert set(red) == {(255, 0, 0)}
    green, _ = render(harness, SOLID, hue=85, saturation=255)[0]
    (r, g, b), = set(green)
    assert g == 255 and r < 20 and b < 20
    white, _ = render(harness, SOLID, hue=0, saturation=0)[0]
    assert set(white) == {(255, 255, 255)}


def test_one_colour_palette_keeps_every_effect_in_that_colour(harness):
    """With the Color palette nothing but the chosen colour appears."""
    for effect in ALL_EFFECTS:
        wire, _ = render(harness, effect, n=120, palette=PAL_COLOR, hue=0, saturation=255)[0]
        assert all(g == 0 and b == 0 for _, g, b in wire), effect
        assert any(r > 0 for r, _, _ in wire), effect


def test_reverse_runs_the_rainbow_backwards(harness):
    """Reversed at time t is the forward frame at the mirrored phase."""
    shift = 4  # FX_SHIFT_RAINBOW in ltp_fx.cpp
    t = 77777 * TIME_PER_MS
    mirrored = ((-(t >> shift)) & 0xFFFF) << shift
    assert render(harness, RAINBOW, reverse=1, time=t)[0] == render(harness, RAINBOW, time=mirrored)[0]


def test_reverse_moves_the_fire_base(harness):
    forward, _ = render(harness, FIRE, n=100, intensity=60)[0]
    reverse, _ = render(harness, FIRE, n=100, intensity=60, reverse=1)[0]
    assert sum(map(sum, forward[:30])) > sum(map(sum, forward[-30:]))
    assert sum(map(sum, reverse[:30])) < sum(map(sum, reverse[-30:]))


def test_scanner_is_one_eye_that_stays_on_the_strip(harness):
    for wire, _ in render(harness, SCANNER, n=90, step=97 * TIME_PER_MS, frames=40):
        lit = [i for i, p in enumerate(wire) if p != (0, 0, 0)]
        assert lit, "the eye left the strip"
        assert lit == list(range(lit[0], lit[-1] + 1)), "more than one lit run"


def test_more_intensity_lights_more_sparkles(harness):
    def lit(intensity):
        return sum(p != (0, 0, 0)
                   for wire, _ in render(harness, SPARKLE, n=300, intensity=intensity,
                                         step=300 * TIME_PER_MS, frames=8)
                   for p in wire)
    assert lit(30) < lit(128) < lit(250)


def test_scale_sets_the_rainbow_wavelength(harness):
    """Scale 32 repeats every 64 pixels, as the scale control's description says."""
    wire, _ = render(harness, RAINBOW, n=200, scale=32)[0]
    assert wire[:64] == wire[64:128]
    assert wire[:32] != wire[32:64]


def test_long_strip_and_large_matrix_render(harness):
    """Sizes beyond any frame buffer the small boards could hold."""
    for effect in ALL_EFFECTS:
        render(harness, effect, n=4000)
        render(harness, effect, n=4000, width=50, layout=SERPENTINE | ORIGIN_BOTTOM)
