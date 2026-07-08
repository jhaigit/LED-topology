"""Guards for the vectorized render paths.

The per-pixel render loops were rewritten as numpy array operations. Most were
verified bit-identical against the original scalar code with an out-of-tree
golden harness; these tests lock in the two properties that can be checked
self-contained, without an external golden file:

* ``Palette.get_color_array`` must stay bit-identical to per-scalar
  ``Palette.get_color`` (rainbow and flame depend on it).
* ``FailingBulb`` is now driven by numpy's RNG (not bit-identical to the old
  ``random`` draws), so its correctness is pinned by invariants instead.
"""

import numpy as np
import pytest

from ltp_controller.palettes import BUILTIN_PALETTES
from ltp_controller.virtual_sources.effects import FailingBulb
from ltp_controller.virtual_sources.base import VirtualSourceConfig


@pytest.mark.parametrize("name", sorted(BUILTIN_PALETTES))
def test_get_color_array_matches_scalar(name):
    """Vectorized palette lookup is byte-for-byte identical to the scalar path."""
    pal = BUILTIN_PALETTES[name]
    # Cover the full range, out-of-range clamping, and every stop boundary.
    ts = np.concatenate([
        np.linspace(-0.1, 1.1, 500),
        np.array([s.position for s in pal.stops]),
    ])
    scalar = np.array([pal.get_color(float(t)) for t in ts], dtype=np.uint8)
    vec = pal.get_color_array(ts)
    assert np.array_equal(scalar, vec)


def test_failing_bulb_invariants():
    """Vectorized FailingBulb degrades physically: health >= 0, dead pixels
    stay dead and render black, failures only accumulate."""
    np.random.seed(7)
    n = 200
    src = FailingBulb(VirtualSourceConfig(name="fb", output_dimensions=[n]))
    src.start()
    src.set_control("decay_rate", 0.5)
    src.set_control("failure_rate", 0.2)

    prev_dead = np.zeros(n, dtype=bool)
    dead_counts = []
    t = 0.0
    for _ in range(400):
        t += 1 / 30
        out = src.render(n, t)
        assert out.shape == (n, 3) and out.dtype == np.uint8
        assert out.min() >= 0 and out.max() <= 255
        assert (src._pixel_health >= 0).all()
        dead = src._pixel_dead
        # dead is monotonic: nothing comes back to life
        assert np.array_equal(dead | prev_dead, dead)
        # dead bulbs are black
        assert np.all(out[dead] == 0)
        prev_dead = dead.copy()
        dead_counts.append(int(dead.sum()))

    assert dead_counts == sorted(dead_counts)
    assert dead_counts[-1] > dead_counts[0]
