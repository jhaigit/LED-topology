"""Simulation effects: flakey power, failing bulb, lightning."""

import math
import random

import numpy as np

from libltp import NumberControl, ColorControl, EnumControl

from ltp_controller.virtual_sources.base import VirtualSource, VirtualSourceConfig


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


class FlakeyPower(VirtualSource):
    """Simulates an unreliable power connection.

    All pixels share the same supply, so the whole strip dims, flickers,
    and occasionally drops out together.  Between faults the strip shows
    the configured base color at full brightness.
    """

    source_type = "flakey_power"

    def __init__(self, config: VirtualSourceConfig | None = None):
        super().__init__(config)
        self._fault_until = 0.0
        self._fault_kind = "none"
        self._flicker_phase = 0.0

    def _setup_controls(self) -> None:
        self._controls.register(
            ColorControl(
                id="color",
                name="Color",
                description="Base color when power is good",
                value="#FFB840",
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="fault_rate",
                name="Fault Rate",
                description="Average faults per second",
                value=1.5,
                min=0.1,
                max=10.0,
                step=0.1,
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="severity",
                name="Severity",
                description="How bad faults get (0=mild flicker, 100=full dropout)",
                value=60.0,
                min=0.0,
                max=100.0,
                step=5.0,
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        color = hex_to_rgb(self.get_control("color"))
        fault_rate = self.get_control("fault_rate")
        severity = self.get_control("severity") / 100.0

        dt = 1.0 / 30.0  # approximate frame time

        if time_elapsed >= self._fault_until:
            # Between faults: maybe start a new one
            if random.random() < fault_rate * dt:
                roll = random.random()
                if roll < 0.4:
                    self._fault_kind = "flicker"
                    self._fault_until = time_elapsed + random.uniform(0.05, 0.3)
                elif roll < 0.7:
                    self._fault_kind = "brownout"
                    self._fault_until = time_elapsed + random.uniform(0.1, 0.6)
                elif roll < 0.9 and severity > 0.3:
                    self._fault_kind = "dropout"
                    self._fault_until = time_elapsed + random.uniform(0.05, 0.2 * severity)
                else:
                    self._fault_kind = "surge"
                    self._fault_until = time_elapsed + random.uniform(0.02, 0.08)

        # Compute brightness multiplier
        if time_elapsed < self._fault_until:
            if self._fault_kind == "flicker":
                self._flicker_phase += random.uniform(5, 20) * dt
                mult = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(self._flicker_phase * 30))
                mult = max(1.0 - severity, mult)
            elif self._fault_kind == "brownout":
                mult = 0.15 + (1.0 - severity) * 0.5
                mult += random.uniform(-0.05, 0.05)
            elif self._fault_kind == "dropout":
                mult = 0.0
            elif self._fault_kind == "surge":
                mult = min(1.5, 1.0 + severity * 0.5)
            else:
                mult = 1.0
        else:
            self._fault_kind = "none"
            mult = 1.0

        mult = max(0.0, min(1.5, mult))

        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        pixels[:] = [min(255, int(c * mult)) for c in color]
        return pixels


class FailingBulb(VirtualSource):
    """Simulates an incandescent bulb near end of life.

    Individual pixels degrade independently — some dim, some develop a
    flicker, some die completely.  The effect builds over time; resetting
    speed rewinds the degradation.
    """

    source_type = "failing_bulb"

    def __init__(self, config: VirtualSourceConfig | None = None):
        super().__init__(config)
        self._pixel_health: np.ndarray | None = None
        self._pixel_flicker_rate: np.ndarray | None = None
        self._pixel_dead: np.ndarray | None = None
        self._last_time = 0.0

    def _setup_controls(self) -> None:
        self._controls.register(
            ColorControl(
                id="color",
                name="Color",
                description="Healthy bulb color",
                value="#FFB040",
                group="pattern",
            )
        )
        self._controls.register(
            ColorControl(
                id="dim_color",
                name="Dim Color",
                description="Color when bulb is struggling",
                value="#802000",
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="decay_rate",
                name="Decay Rate",
                description="How fast bulbs degrade (health lost per second)",
                value=0.05,
                min=0.005,
                max=0.5,
                step=0.005,
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="failure_rate",
                name="Failure Rate",
                description="Chance per second a low-health bulb dies",
                value=0.03,
                min=0.0,
                max=0.2,
                step=0.005,
                group="pattern",
            )
        )

    def _init_pixels(self, n: int) -> None:
        self._pixel_health = np.ones(n, dtype=np.float32)
        self._pixel_flicker_rate = np.random.uniform(0.0, 0.3, n).astype(np.float32)
        self._pixel_dead = np.zeros(n, dtype=bool)

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        if self._pixel_health is None or len(self._pixel_health) != num_pixels:
            self._init_pixels(num_pixels)
            self._last_time = time_elapsed

        color = hex_to_rgb(self.get_control("color"))
        dim_color = hex_to_rgb(self.get_control("dim_color"))
        decay_rate = self.get_control("decay_rate")
        failure_rate = self.get_control("failure_rate")

        dt = time_elapsed - self._last_time
        self._last_time = time_elapsed
        dt = max(0.0, min(dt, 0.2))

        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)

        n = num_pixels
        living = ~self._pixel_dead

        # Degrade health of living bulbs with per-pixel variation. (Vectorized
        # with numpy's RNG — visually equivalent to the original per-pixel draws
        # rather than bit-identical, since draw order/source differs.)
        decay = np.where(living, decay_rate * dt * np.random.uniform(0.5, 1.5, n), 0.0)
        self._pixel_health = np.maximum(0.0, self._pixel_health - decay).astype(np.float32)
        h = self._pixel_health

        # Low-health bulbs may die this frame
        die = living & (h < 0.3) & (np.random.random(n) < failure_rate * dt)
        self._pixel_dead |= die
        living = ~self._pixel_dead

        # Low-health bulbs flicker
        low = living & (h < 0.5)
        rate = self._pixel_flicker_rate + (0.5 - h) * 2.0
        flicker = np.where(low, 0.4 + 0.6 * (0.5 + 0.5 * np.sin(time_elapsed * rate * 40)), 1.0)
        dip = low & (h < 0.2) & (np.random.random(n) < 0.1)
        flicker = np.where(dip, flicker * np.random.uniform(0.0, 0.5, n), flicker)

        brightness = (h * flicker)[:, None]
        dim = np.array(dim_color, dtype=np.float64)
        col = np.array(color, dtype=np.float64)
        vals = (dim + (col - dim) * brightness).astype(np.uint8)
        # Dead pixels (including those that just died) stay black.
        pixels[living] = vals[living]

        return pixels


class Lightning(VirtualSource):
    """Simulates lightning strikes.

    A dark sky background punctuated by bright multi-flash strikes
    that illuminate all or part of the strip.
    """

    source_type = "lightning"

    def __init__(self, config: VirtualSourceConfig | None = None):
        super().__init__(config)
        self._strike_flashes: list[dict] = []
        self._next_strike = 0.0

    def _setup_controls(self) -> None:
        self._controls.register(
            ColorControl(
                id="sky_color",
                name="Sky Color",
                description="Background sky color",
                value="#0A0A1A",
                group="pattern",
            )
        )
        self._controls.register(
            ColorControl(
                id="flash_color",
                name="Flash Color",
                description="Lightning bolt color",
                value="#EEEEFF",
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="strike_rate",
                name="Strike Rate",
                description="Average strikes per second",
                value=0.5,
                min=0.05,
                max=5.0,
                step=0.05,
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="intensity",
                name="Intensity",
                description="Brightness of flashes",
                value=1.0,
                min=0.2,
                max=1.0,
                step=0.05,
                group="pattern",
            )
        )
        self._controls.register(
            EnumControl(
                id="coverage",
                name="Coverage",
                description="How much of the strip each strike lights",
                value="full",
                options=[
                    {"value": "full", "label": "Full Strip"},
                    {"value": "partial", "label": "Partial"},
                    {"value": "random", "label": "Random"},
                ],
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        sky = hex_to_rgb(self.get_control("sky_color"))
        flash_color = hex_to_rgb(self.get_control("flash_color"))
        strike_rate = self.get_control("strike_rate")
        intensity = self.get_control("intensity")
        coverage = self.get_control("coverage")

        dt = 1.0 / 30.0

        # Maybe start a new strike (series of rapid flashes)
        if time_elapsed >= self._next_strike:
            if random.random() < strike_rate * dt or time_elapsed >= self._next_strike:
                num_flashes = random.choices([1, 2, 3, 4], weights=[20, 40, 30, 10])[0]
                t = time_elapsed
                for _ in range(num_flashes):
                    duration = random.uniform(0.02, 0.08)
                    flash_intensity = random.uniform(0.5, 1.0) * intensity

                    if coverage == "full":
                        start, end = 0, num_pixels
                    elif coverage == "partial":
                        span = random.uniform(0.3, 0.8)
                        start_frac = random.uniform(0, 1.0 - span)
                        start = int(start_frac * num_pixels)
                        end = int((start_frac + span) * num_pixels)
                    else:
                        span = random.uniform(0.1, 1.0)
                        start_frac = random.uniform(0, 1.0 - span)
                        start = int(start_frac * num_pixels)
                        end = int((start_frac + span) * num_pixels)

                    self._strike_flashes.append({
                        "start_time": t,
                        "duration": duration,
                        "intensity": flash_intensity,
                        "pixel_start": start,
                        "pixel_end": end,
                    })
                    t += duration + random.uniform(0.03, 0.12)

                self._next_strike = t + random.uniform(0.3, 3.0) / max(strike_rate, 0.1)

        # Build output
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        pixels[:] = sky

        # Apply active flashes
        active = []
        for flash in self._strike_flashes:
            elapsed = time_elapsed - flash["start_time"]
            if elapsed < 0:
                active.append(flash)
                continue
            if elapsed > flash["duration"]:
                continue
            active.append(flash)

            # Sharp attack, quick decay
            progress = elapsed / flash["duration"]
            if progress < 0.15:
                brightness = progress / 0.15
            else:
                brightness = 1.0 - ((progress - 0.15) / 0.85) ** 0.5
            brightness *= flash["intensity"]

            ps = flash["pixel_start"]
            pe = min(flash["pixel_end"], num_pixels)
            if ps < pe:
                # Constant color across the flash span; min(255,int(x)) and
                # int(min(255,x)) agree for these non-negative values.
                skyf = np.array(sky, dtype=np.float64)
                val = np.minimum(
                    255.0, skyf + (np.array(flash_color, dtype=np.float64) - skyf) * brightness
                ).astype(np.uint8)
                pixels[ps:pe] = val

        self._strike_flashes = active

        return pixels
