"""Tests for the Teensy 4.0 WS2812 driver (arduino/ltp_stream_serial/ws2812_teensy4.h).

The driver has not been run on a Teensy. Here its source is compiled for the
host against a model of the processor's UART (teensy4_stub/Arduino.h) and the
characters the model transmits are decoded as a WS2812 would see them. This
checks the driver's logic - encoding, ring, interrupt handler, what happens
when a pattern is too slow - against the UART as its author understands it
from the reference manual. It is not a test of the hardware.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKETCH = ROOT / "arduino" / "ltp_stream_serial"
HERE = Path(__file__).parent

BIT_NS = 250          # one UART bit at 4 Mbit/s
CHAR_NS = 10 * BIT_NS
PIXEL_NS = 12 * CHAR_NS
LATCH_NS = 300_000    # LED_LATCH_US in the harness

# A WS2812 reads a high pulse shorter than this as 0, longer as 1.
ONE_THRESHOLD_NS = 600
# Older WS2812 take about 6 us of low as the end of the frame.
OLD_WS2812_LATCH_NS = 6000

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    exe = tmp_path_factory.mktemp("ws_t4") / "ws2812_t4_harness"
    subprocess.run(
        ["g++", "-std=gnu++17", "-O1", "-Wall", "-Wextra", "-Werror",
         "-fsanitize=undefined", "-fno-sanitize-recover=undefined",
         f"-I{HERE / 'teensy4_stub'}", f"-I{SKETCH}",
         str(HERE / "ws2812_t4_harness.cpp"), "-o", str(exe)],
        check=True,
    )
    return exe


def run(harness, total, *, count=None, cost_ns=5000, stall_pixel=-1, stall_ns=0,
        irq_latency_ns=200, frames=1):
    """Run the driver; returns (setup dict, list of frame dicts)."""
    args = [total, total if count is None else count, cost_ns, stall_pixel, stall_ns,
            irq_latency_ns, frames]
    text = subprocess.run([str(harness), *map(str, args)], check=True, capture_output=True,
                          text=True, timeout=120).stdout
    setup, out = {}, []
    for line in text.splitlines():
        kind, *fields = line.split()
        if kind == "setup":
            setup = {k: int(v) for k, v in (f.split("=") for f in fields)}
        elif kind == "frame":
            out.append({k: int(v) for k, v in (f.split("=") for f in fields)})
            out[-1]["sent"] = []
        elif kind == "c":
            out[-1]["sent"].append((int(fields[0]), int(fields[1])))
    assert len(out) == frames
    return setup, out


def waveform(sent):
    """The data line as (level, start ns, duration ns) runs, from the characters sent.

    The line is inverted and idles low: the start bit is high, a data bit is
    high when it is 0, the stop bit is low. Bits go out least significant first.
    """
    runs = []

    def add(level, start, duration):
        if runs and runs[-1][0] == level and runs[-1][1] + runs[-1][2] == start:
            runs[-1][2] += duration
        else:
            if runs and runs[-1][1] + runs[-1][2] < start:  # idle gap between characters
                idle_from = runs[-1][1] + runs[-1][2]
                if runs[-1][0] == 0:
                    runs[-1][2] += start - idle_from
                else:
                    runs.append([0, idle_from, start - idle_from])
                if level == 0:
                    runs[-1][2] += duration
                    return
            runs.append([level, start, duration])

    for start, value in sent:
        add(1, start, BIT_NS)
        for bit in range(8):
            add(0 if (value >> bit) & 1 else 1, start + (1 + bit) * BIT_NS, BIT_NS)
        add(0, start + 9 * BIT_NS, BIT_NS)
    return [tuple(r) for r in runs]


def decode(sent):
    """What a WS2812 strip makes of the line: (bytes, set of high times, longest low inside)."""
    runs = waveform(sent)
    bits, highs, lows = [], set(), []
    for i, (level, _, duration) in enumerate(runs):
        if level == 1:
            highs.add(duration)
            bits.append(1 if duration > ONE_THRESHOLD_NS else 0)
        elif i < len(runs) - 1:
            lows.append(duration)
    data = bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits) - 7, 8))
    return data, highs, max(lows, default=0)


def expected(total, count=None):
    """The harness's pattern in GRB order, black beyond `count`."""
    count = total if count is None else count
    out = bytearray()
    for i in range(total):
        r, g, b = ((7 * i + 1) & 255, (13 * i + 5) & 255, (29 * i + 11) & 255) if i < count else (0, 0, 0)
        out += bytes((g, r, b))
    return bytes(out)


def test_uart_is_set_up_for_ws2812(harness):
    setup, _ = run(harness, 1)
    assert setup["baud"] == (5 << 24) | 1      # 24 MHz / (6 samples * 1) = 4 Mbit/s
    assert setup["ctrl"] & (1 << 19)           # transmitter on
    assert setup["ctrl"] & (1 << 28)           # inverted: the line idles low
    assert setup["fifo"] & (1 << 7)            # transmit FIFO on
    assert setup["water"] == 2
    assert setup["clock"] == 3 << 28           # LPUART2 clock gate, for pin 14
    assert setup["pinfn"] == 2
    assert setup["priority"] == 0 and setup["irq"] == 1


@pytest.mark.parametrize("total", [1, 2, 15, 16, 17, 21, 22, 60, 300])
def test_frame_arrives_as_ws2812_bits(harness, total):
    _, (frame,) = run(harness, total)
    data, highs, longest_low = decode(frame["sent"])
    assert frame["ok"] == 1 and frame["overflows"] == 0
    assert data == expected(total)
    assert highs == {250, 1000}                # a 0 and a 1, nothing in between
    assert longest_low <= 1000                 # no gap anywhere in the frame


def test_pixels_beyond_the_pattern_are_black(harness):
    _, (frame,) = run(harness, 40, count=25)
    data, _, _ = decode(frame["sent"])
    assert data == expected(40, count=25)
    assert data[25 * 3:] == bytes(15 * 3)


def test_frame_takes_30_us_per_pixel_whatever_the_pattern_costs(harness):
    for cost_ns in (0, 5000, 20000, 28000):
        _, (frame,) = run(harness, 300, cost_ns=cost_ns)
        sent = frame["sent"]
        assert frame["ok"] == 1
        assert sent[-1][0] + CHAR_NS - sent[0][0] == 300 * PIXEL_NS


def test_load_reports_the_share_of_pixel_time_used(harness):
    for cost_ns, low, high in ((3000, 9, 13), (15000, 49, 53), (27000, 89, 93)):
        _, (frame,) = run(harness, 200, cost_ns=cost_ns)
        assert low <= frame["load"] <= high


def test_one_slow_pixel_is_absorbed_by_the_ring(harness):
    """400 us for one pixel - thirteen times its share - and the line never pauses."""
    _, (frame,) = run(harness, 200, cost_ns=5000, stall_pixel=120, stall_ns=400_000)
    data, highs, longest_low = decode(frame["sent"])
    assert frame["ok"] == 1
    assert data == expected(200)
    assert highs == {250, 1000} and longest_low <= 1000


def test_pattern_that_cannot_keep_up_cuts_the_frame(harness):
    """Slower than the wire: the frame ends early, cleanly, and is reported."""
    _, frames = run(harness, 300, cost_ns=45000, frames=3)
    for frame in frames:
        data, highs, longest_low = decode(frame["sent"])
        assert frame["ok"] == 0
        assert frame["load"] > 100
        assert 0 < len(data) < 300 * 3
        assert len(data) % 3 == 0, "the frame ended inside a pixel"
        assert data == expected(300)[:len(data)]   # what did go out is right
        assert highs == {250, 1000} and longest_low <= 1000
        assert frame["ended"] - frame["began"] < 20_000_000, "the driver hung"


def test_long_stall_cuts_the_frame_and_the_next_one_is_whole(harness):
    _, frames = run(harness, 100, cost_ns=5000, stall_pixel=50, stall_ns=3_000_000, frames=1)
    assert frames[0]["ok"] == 0
    _, frames = run(harness, 100, cost_ns=5000, frames=2)
    assert [f["ok"] for f in frames] == [1, 1]


def test_frames_are_separated_by_the_latch_time(harness):
    _, frames = run(harness, 50, frames=3)
    for before, after in zip(frames, frames[1:]):
        quiet = after["sent"][0][0] - (before["sent"][-1][0] + CHAR_NS)
        assert quiet >= LATCH_NS


def test_late_interrupts_are_tolerated_up_to_the_fifo_slack(harness):
    """The handler may arrive 7 us late without a pause on the line.

    It is asked for when two characters are left in the FIFO and a third has
    just started: 7.5 us of data still to go.
    """
    _, (frame,) = run(harness, 120, irq_latency_ns=7000)
    data, highs, longest_low = decode(frame["sent"])
    assert frame["ok"] == 1 and data == expected(120)
    assert highs == {250, 1000} and longest_low <= 1000


def test_very_late_interrupts_stretch_a_low_but_keep_the_data(harness):
    """Past the slack the line pauses, by the excess only: 9 us late is a 1.5 us pause."""
    _, (frame,) = run(harness, 120, irq_latency_ns=9000)
    data, highs, longest_low = decode(frame["sent"])
    assert data == expected(120)
    assert highs == {250, 1000}
    assert 1000 < longest_low <= 1000 + 1500 < OLD_WS2812_LATCH_NS


def test_clock_follows_real_time(harness):
    text = subprocess.run([str(harness), "300", "300", "5000", "-1", "0", "200", "4"],
                          check=True, capture_output=True, text=True).stdout
    fields = dict(f.split("=") for f in text.splitlines()[-1].split()[1:])
    assert int(fields["ms"]) == int(fields["now"]) // 1_000_000
