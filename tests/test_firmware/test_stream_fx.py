"""Tests for the frame-buffer-free pattern engine (arduino/libraries/LtpStreamFx).

The engine is plain C++ with no Arduino dependency, so the same source the
firmware compiles is built here for the host and driven through
fx_harness.cpp. What is checked is what the firmware relies on:

- pixels sent in wiring order and pixels read back by logical index are the
  same frame, for every matrix wiring layout;
- a frame is a function of its inputs only, so nothing needs remembering;
- brightness, palette and geometry settings do what the controls promise.

The engine is built twice: as the small boards get it, and with the extended
patterns of the Teensy 4.0 (FX_EXTENDED, ltp_fx_extended.h).

The cycle-accurate behaviour on the ATmega328P is covered by the simulator
kit in arduino/ltp_stream_serial/sim (make sim-test), not here; the engine as
the Teensy compiler builds it is run in test_stream_fx_arm.py.
"""

import shutil

import pytest

from .fx_host import build_harness, run_job

# Effect numbers, as in ltp_fx.h.
OFF, SCANNER, RAINBOW, FIRE, SPARKLE, CHASE, SOLID = 0, 1, 2, 3, 4, 5, 6
COMET, WAVES, TWINKLE, NOISE, PLASMA, RIPPLE = 7, 8, 9, 10, 11, 12
ALL_EFFECTS = list(range(1, 13))
STATIC_EFFECTS = {SOLID}

# The extended effects, built only with FX_EXTENDED.
FIREWORKS, BOUNCE, METEORS, LAVA_LAMP, AURORA, RAINDROPS = range(13, 19)
EXTENDED_EFFECTS = list(range(13, 19))

# Palette numbers, as in ltp_fx.h.
PAL_DEFAULT, PAL_COLOR, PAL_RAINBOW = 0, 1, 2

# Matrix layout bits, as in ltp_protocol.h.
SERPENTINE, VERTICAL_FIRST, ORIGIN_BOTTOM = 1, 2, 4

# fxTime units per millisecond at the nominal speed setting.
TIME_PER_MS = 256

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    """The engine as the small boards build it."""
    return build_harness(tmp_path_factory.mktemp("fx"), extended=False)


@pytest.fixture(scope="module")
def extended(tmp_path_factory):
    """The engine with the extended patterns, as the Teensy 4.0 builds it."""
    return build_harness(tmp_path_factory.mktemp("fx_extended"), extended=True)


def render(harness, effect, *, n=60, width=1, layout=0, brightness=255, scale=32,
           palette=PAL_DEFAULT, hue=0, saturation=255, intensity=128, count=2,
           reverse=0, time=5000 * TIME_PER_MS, step=0, frames=1):
    """Frames from the engine: a list of (wire_order, logical_order) pixel lists."""
    args = [n, width, layout, effect, brightness, scale, palette, hue, saturation,
            intensity, count, reverse, time, step, frames]
    raw = run_job(harness, args)
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


# ============================================================================
# Extended patterns (FX_EXTENDED)
# ============================================================================


def lit_count(wire):
    return sum(p != (0, 0, 0) for p in wire)


def lit_runs(wire):
    """Number of separate stretches of lit pixels."""
    lit = [p != (0, 0, 0) for p in wire]
    return sum(1 for i, on in enumerate(lit) if on and (i == 0 or not lit[i - 1]))


def test_extended_build_leaves_the_base_patterns_alone(harness, extended):
    for effect in ALL_EFFECTS:
        for kw in ({"n": 90}, {"n": 96, "width": 12, "layout": SERPENTINE | ORIGIN_BOTTOM}):
            assert render(extended, effect, **kw) == render(harness, effect, **kw), effect


def test_small_boards_show_nothing_for_an_extended_number(harness):
    for effect in EXTENDED_EFFECTS:
        wire, _ = render(harness, effect)[0]
        assert set(wire) == {(0, 0, 0)}


@pytest.mark.parametrize("layout", range(8))
@pytest.mark.parametrize("n,width", [(256, 16), (96, 12), (60, 1), (35, 7)])
def test_extended_wire_order_matches_logical_order(extended, layout, n, width):
    for effect in EXTENDED_EFFECTS:
        for wire, logical in render(extended, effect, n=n, width=width, layout=layout, count=4,
                                    step=450 * TIME_PER_MS, frames=3):
            for idx in range(n):
                assert wire[map_pixel(idx, n, width, layout)] == logical[idx], (effect, idx)


@pytest.mark.parametrize("effect", EXTENDED_EFFECTS)
def test_extended_zero_brightness_is_black(extended, effect):
    for wire, _ in render(extended, effect, brightness=0, step=300 * TIME_PER_MS, frames=6):
        assert set(wire) == {(0, 0, 0)}


@pytest.mark.parametrize("effect", EXTENDED_EFFECTS)
@pytest.mark.parametrize("n,width", [(120, 1), (192, 16)])
def test_extended_effect_lights_and_moves(extended, effect, n, width):
    # Fireworks and aurora have dark moments, so not every frame need be lit.
    frames = [wire for wire, _ in render(extended, effect, n=n, width=width, count=4,
                                         step=137 * TIME_PER_MS, frames=30)]
    assert sum(any(p != (0, 0, 0) for p in f) for f in frames) >= 10, "mostly dark"
    assert len({tuple(f) for f in frames}) >= 10, "hardly moves"


@pytest.mark.parametrize("effect", EXTENDED_EFFECTS)
@pytest.mark.parametrize("n,width", [(100, 1), (160, 16)])
def test_extended_frame_depends_only_on_its_inputs(extended, effect, n, width):
    t = 123456789
    alone = render(extended, effect, n=n, width=width, count=5, time=t)[0]
    after_others = render(extended, effect, n=n, width=width, count=5,
                          time=t - 3 * 99999, step=99999, frames=4)[3]
    assert alone == after_others


@pytest.mark.parametrize("effect", EXTENDED_EFFECTS)
def test_extended_brightness_only_dims(extended, effect):
    kw = dict(n=150, count=6, step=200 * TIME_PER_MS, frames=8)
    full = [wire for wire, _ in render(extended, effect, brightness=255, **kw)]
    half = [wire for wire, _ in render(extended, effect, brightness=128, **kw)]
    assert all(h <= f for hw, fw in zip(half, full) for hp, fp in zip(hw, fw) for h, f in zip(hp, fp))
    assert sum(sum(map(sum, w)) for w in half) < sum(sum(map(sum, w)) for w in full)


@pytest.mark.parametrize("effect", EXTENDED_EFFECTS)
@pytest.mark.parametrize("n,width", [(120, 1), (192, 16)])
def test_extended_one_colour_palette_keeps_that_colour(extended, effect, n, width):
    """Even the white flash of a firework gives way to the one colour."""
    frames = [wire for wire, _ in render(extended, effect, n=n, width=width, palette=PAL_COLOR,
                                         hue=0, saturation=255, count=4,
                                         step=97 * TIME_PER_MS, frames=40)]
    assert all(g == 0 and b == 0 for f in frames for _, g, b in f)
    assert any(r > 0 for f in frames for r, _, _ in f)


@pytest.mark.parametrize("effect", EXTENDED_EFFECTS)
def test_extended_settings_at_their_limits(extended, effect):
    """Every corner of the controls, on awkward sizes; the build traps overflow."""
    for n, width in [(1, 1), (2, 1), (7, 1), (4000, 1), (4000, 50), (255, 255), (510, 255), (64, 2)]:
        for scale in (1, 32, 255):
            for intensity, count in ((0, 0), (255, 255), (128, 8)):
                render(extended, effect, n=n, width=width, scale=scale, intensity=intensity,
                       count=count, layout=SERPENTINE)
                render(extended, effect, n=n, width=width, scale=scale, intensity=intensity,
                       count=count, reverse=1, time=2 ** 32 - 5000)


def test_bounce_shows_as_many_balls_as_count(extended):
    for count in (1, 3, 8):
        most = 0
        for wire, _ in render(extended, BOUNCE, n=400, count=count, scale=64,
                              step=83 * TIME_PER_MS, frames=40):
            runs = lit_runs(wire)
            assert 1 <= runs <= count
            most = max(most, runs)
        assert most == count, "the balls never came apart"


def test_bounce_ball_size_follows_scale(extended):
    def widest(scale):
        return max(lit_count(wire) for wire, _ in render(extended, BOUNCE, n=300, count=1, scale=scale,
                                                         step=83 * TIME_PER_MS, frames=20))
    assert widest(128) < widest(32) < widest(8)


def test_bounce_goes_higher_with_intensity(extended):
    def highest(intensity):
        return max(i for wire, _ in render(extended, BOUNCE, n=300, count=3, intensity=intensity,
                                           step=61 * TIME_PER_MS, frames=60)
                   for i, p in enumerate(wire) if p != (0, 0, 0))
    assert highest(0) < highest(128) < highest(255)
    assert highest(255) > 250


def test_fireworks_rise_burst_and_go_dark(extended):
    """One shell: a single dot on its way up, then many sparks, then nothing."""
    counts = [lit_count(wire) for wire, _ in render(extended, FIREWORKS, n=300, count=1, intensity=255,
                                                    step=40 * TIME_PER_MS, frames=140)]
    assert 0 in counts, "never dark between shells"
    assert any(1 <= c <= 2 for c in counts), "no rising shell"
    assert max(counts) >= 20, "no burst"


def test_fireworks_intensity_adds_sparks(extended):
    def most(intensity):
        return max(lit_count(wire) for wire, _ in render(extended, FIREWORKS, n=600, count=1, scale=8,
                                                         intensity=intensity,
                                                         step=40 * TIME_PER_MS, frames=140))
    assert most(0) < most(255)


def test_meteors_travel_and_reverse(extended):
    def head_positions(reverse):
        out = []
        for wire, _ in render(extended, METEORS, n=600, count=1, reverse=reverse,
                              time=3000 * TIME_PER_MS, step=20 * TIME_PER_MS, frames=12):
            lit = [i for i, p in enumerate(wire) if sum(p) > 300]
            if lit:
                out.append(max(lit) if not reverse else min(lit))
        return out
    forward = head_positions(0)
    backward = head_positions(1)
    assert len(forward) >= 6 and len(backward) >= 6
    rising = sum(b > a for a, b in zip(forward, forward[1:]))
    falling = sum(b < a for a, b in zip(backward, backward[1:]))
    assert rising >= len(forward) - 2, "meteors do not move away from LED 0"
    assert falling >= len(backward) - 2, "reversed meteors do not move towards LED 0"


def test_meteor_tails_grow_with_intensity(extended):
    def lit(intensity):
        return sum(lit_count(wire) for wire, _ in render(extended, METEORS, n=600, count=4,
                                                         intensity=intensity,
                                                         step=250 * TIME_PER_MS, frames=24))
    assert lit(10) < lit(128) < lit(255)


def test_meteors_on_a_matrix_keep_to_their_columns(extended):
    """Each streak runs down one column: lit pixels in a row are isolated."""
    for wire, logical in render(extended, METEORS, n=32 * 24, width=32, count=2,
                                step=150 * TIME_PER_MS, frames=10):
        columns = {i % 32 for i, p in enumerate(logical) if p != (0, 0, 0)}
        assert len(columns) <= 4  # two per count


def test_lava_lamp_blobs_grow_as_scale_shrinks(extended):
    def lit(scale):
        return sum(sum(p) > 60 for wire, _ in render(extended, LAVA_LAMP, n=400, count=2, scale=scale,
                                                     step=3000 * TIME_PER_MS, frames=10)
                   for p in wire)
    assert lit(128) < lit(32) < lit(8)


def test_lava_lamp_is_hottest_where_blobs_overlap(extended):
    """With the Heat palette red comes first, then green: green means overlap or a core."""
    hottest_one = max(p[1] for wire, _ in render(extended, LAVA_LAMP, n=4000, count=1, scale=64,
                                                 palette=3, step=7000 * TIME_PER_MS, frames=6)
                      for p in wire)
    hottest_many = max(p[1] for wire, _ in render(extended, LAVA_LAMP, n=40, count=8, scale=16,
                                                  palette=3, step=7000 * TIME_PER_MS, frames=6)
                       for p in wire)
    assert hottest_many > hottest_one


def test_aurora_intensity_lights_more_of_the_sky(extended):
    def lit(intensity):
        return sum(lit_count(wire) for wire, _ in render(extended, AURORA, n=300, intensity=intensity,
                                                         step=5000 * TIME_PER_MS, frames=20))
    assert lit(0) < lit(128) < lit(255)


def test_aurora_changes_slowly(extended):
    """No pixel jumps between frames 33 ms apart."""
    frames = [wire for wire, _ in render(extended, AURORA, n=200, step=33 * TIME_PER_MS, frames=90)]
    biggest = max(abs(a - b) for f0, f1 in zip(frames, frames[1:])
                  for p0, p1 in zip(f0, f1) for a, b in zip(p0, p1))
    assert biggest <= 40


def test_raindrops_ripple_the_water(extended):
    """Still water is one colour; drops make it uneven, more so with more of them."""
    def unevenness(count):
        return sum(len(set(wire)) for wire, _ in render(extended, RAINDROPS, n=300, count=count,
                                                        step=400 * TIME_PER_MS, frames=20))
    assert unevenness(1) > 20 * 2
    assert unevenness(8) > unevenness(1)


def test_raindrops_on_a_matrix_spread_both_ways(extended):
    """Rings, not stripes: the water differs from row to row and from column to column."""
    width = height = 33
    rippled = 0
    for _, logical in render(extended, RAINDROPS, n=width * height, width=width, count=2,
                             step=900 * TIME_PER_MS, frames=8):
        rows = {tuple(logical[r * width:(r + 1) * width]) for r in range(height)}
        columns = {tuple(logical[c::width]) for c in range(width)}
        if len(rows) > 3 and len(columns) > 3:
            rippled += 1
    assert rippled >= 6
