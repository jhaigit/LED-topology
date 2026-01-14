"""Pattern generator virtual sources."""

import math
import random
from typing import Any

import numpy as np

from libltp import NumberControl, BooleanControl, EnumControl, ColorControl

from ltp_controller.palettes import palette_registry, Palette
from ltp_controller.virtual_sources.base import VirtualSource, VirtualSourceConfig


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    """Convert RGB tuple to hex color string."""
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


class RainbowPattern(VirtualSource):
    """Rainbow color cycling pattern."""

    source_type = "rainbow"

    def _setup_controls(self) -> None:
        self._controls.register(
            NumberControl(
                id="wavelength",
                name="Wavelength",
                description="Number of full spectrum cycles across the strip",
                value=1.0,
                min=0.1,
                max=10.0,
                step=0.1,
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="saturation",
                name="Saturation",
                description="Color saturation",
                value=1.0,
                min=0.0,
                max=1.0,
                step=0.05,
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        wavelength = self.get_control("wavelength")
        saturation = self.get_control("saturation")

        palette = palette_registry.get("rainbow")

        for i in range(num_pixels):
            # Position in pattern plus time-based offset
            t = (i / num_pixels * wavelength + time_elapsed * 0.5) % 1.0
            color = palette.get_color(t)

            # Apply saturation (blend towards white)
            if saturation < 1.0:
                gray = sum(color) // 3
                color = tuple(
                    int(c * saturation + gray * (1 - saturation)) for c in color
                )

            pixels[i] = color

        return pixels


class ChasePattern(VirtualSource):
    """Moving chase segments pattern."""

    source_type = "chase"

    def _setup_controls(self) -> None:
        self._controls.register(
            ColorControl(
                id="color",
                name="Color",
                description="Chase segment color",
                value="#FFFFFF",
                group="pattern",
            )
        )
        self._controls.register(
            ColorControl(
                id="background",
                name="Background",
                description="Background color",
                value="#000000",
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="length",
                name="Length",
                description="Length of chase segment in pixels",
                value=5,
                min=1,
                max=50,
                step=1,
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="spacing",
                name="Spacing",
                description="Spacing between segments (0 = single segment)",
                value=10,
                min=0,
                max=100,
                step=1,
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="fade",
                name="Fade",
                description="Tail fade amount",
                value=0.5,
                min=0.0,
                max=1.0,
                step=0.1,
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        color = hex_to_rgb(self.get_control("color"))
        background = hex_to_rgb(self.get_control("background"))
        length = int(self.get_control("length"))
        spacing = int(self.get_control("spacing"))
        fade = self.get_control("fade")

        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        pixels[:] = background

        # Calculate chase position
        period = length + spacing if spacing > 0 else num_pixels + length
        position = (time_elapsed * 20) % period

        for i in range(num_pixels):
            # Distance from chase head
            if spacing > 0:
                # Multiple segments
                dist = (i - position) % period
            else:
                # Single segment
                dist = (i - position) % (num_pixels + length)

            if dist < 0:
                dist += period

            if dist < length:
                # In the chase segment
                if fade > 0 and dist > 0:
                    # Apply fade to tail
                    fade_amount = 1.0 - (dist / length) * fade
                    pixels[i] = [
                        int(background[j] + (color[j] - background[j]) * fade_amount)
                        for j in range(3)
                    ]
                else:
                    pixels[i] = color

        return pixels


class CylonPattern(VirtualSource):
    """Back-and-forth scanning (Larson scanner) pattern."""

    source_type = "cylon"

    def _setup_controls(self) -> None:
        self._controls.register(
            ColorControl(
                id="color",
                name="Color",
                description="Scanner color",
                value="#FF0000",
                group="pattern",
            )
        )
        self._controls.register(
            ColorControl(
                id="background",
                name="Background",
                description="Background color",
                value="#000000",
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="width",
                name="Width",
                description="Width of the scanner",
                value=5,
                min=1,
                max=30,
                step=1,
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="fade",
                name="Fade",
                description="Tail fade intensity",
                value=0.8,
                min=0.0,
                max=1.0,
                step=0.05,
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        color = hex_to_rgb(self.get_control("color"))
        background = hex_to_rgb(self.get_control("background"))
        width = int(self.get_control("width"))
        fade = self.get_control("fade")

        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        pixels[:] = background

        # Bounce back and forth
        cycle_time = num_pixels / 15  # Time to traverse
        t = (time_elapsed % (cycle_time * 2)) / cycle_time
        if t > 1.0:
            t = 2.0 - t  # Reverse direction

        center = t * (num_pixels - 1)

        for i in range(num_pixels):
            dist = abs(i - center)
            if dist < width:
                # Apply fade based on distance from center
                intensity = 1.0 - (dist / width) * fade
                intensity = max(0, intensity)
                pixels[i] = [
                    int(background[j] + (color[j] - background[j]) * intensity)
                    for j in range(3)
                ]

        return pixels


class FlamePattern(VirtualSource):
    """Simulated fire effect."""

    source_type = "flame"

    def __init__(self, config: VirtualSourceConfig | None = None):
        super().__init__(config)
        self._heat: np.ndarray | None = None

    def _setup_controls(self) -> None:
        self._controls.register(
            NumberControl(
                id="cooling",
                name="Cooling",
                description="How quickly flames cool",
                value=55,
                min=20,
                max=100,
                step=5,
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="sparking",
                name="Sparking",
                description="Chance of new sparks",
                value=120,
                min=50,
                max=200,
                step=10,
                group="pattern",
            )
        )
        self._controls.register(
            EnumControl(
                id="palette",
                name="Palette",
                description="Color palette",
                value="fire",
                options=[
                    {"value": "fire", "label": "Fire"},
                    {"value": "ice", "label": "Ice"},
                    {"value": "forest", "label": "Forest"},
                    {"value": "lava", "label": "Lava"},
                ],
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        # Initialize heat array if needed
        if self._heat is None or len(self._heat) != num_pixels:
            self._heat = np.zeros(num_pixels, dtype=np.float32)

        cooling = self.get_control("cooling")
        sparking = int(self.get_control("sparking"))
        palette_name = self.get_control("palette")
        palette = palette_registry.get(palette_name) or palette_registry.get("fire")

        # Cool down every cell
        for i in range(num_pixels):
            cool_amount = random.random() * cooling / 255.0
            self._heat[i] = max(0, self._heat[i] - cool_amount)

        # Heat diffuses upward
        for i in range(num_pixels - 1, 1, -1):
            self._heat[i] = (
                self._heat[i - 1] + self._heat[i - 2] + self._heat[i - 2]
            ) / 3

        # Randomly ignite sparks at bottom
        if random.randint(0, 255) < sparking:
            spark_pos = random.randint(0, min(7, num_pixels - 1))
            self._heat[spark_pos] = min(1.0, self._heat[spark_pos] + random.uniform(0.6, 1.0))

        # Convert heat to colors
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        for i in range(num_pixels):
            pixels[i] = palette.get_color(min(1.0, self._heat[i]))

        return pixels


class SparklePattern(VirtualSource):
    """Random twinkling points pattern."""

    source_type = "sparkle"

    def __init__(self, config: VirtualSourceConfig | None = None):
        super().__init__(config)
        self._sparkle_values: np.ndarray | None = None

    def _setup_controls(self) -> None:
        self._controls.register(
            ColorControl(
                id="color",
                name="Color",
                description="Sparkle color",
                value="#FFFFFF",
                group="pattern",
            )
        )
        self._controls.register(
            ColorControl(
                id="background",
                name="Background",
                description="Background color",
                value="#000000",
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="density",
                name="Density",
                description="Probability of sparkle per pixel per frame",
                value=0.05,
                min=0.01,
                max=0.5,
                step=0.01,
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="fade_speed",
                name="Fade Speed",
                description="How quickly sparkles fade",
                value=0.9,
                min=0.5,
                max=0.99,
                step=0.01,
                group="pattern",
            )
        )
        self._controls.register(
            BooleanControl(
                id="random_color",
                name="Random Colors",
                description="Use random colors instead of fixed",
                value=False,
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        # Initialize sparkle values if needed
        if self._sparkle_values is None or len(self._sparkle_values) != num_pixels:
            self._sparkle_values = np.zeros((num_pixels, 4), dtype=np.float32)  # R, G, B, brightness

        color = hex_to_rgb(self.get_control("color"))
        background = hex_to_rgb(self.get_control("background"))
        density = self.get_control("density")
        fade_speed = self.get_control("fade_speed")
        random_color = self.get_control("random_color")

        # Fade existing sparkles
        self._sparkle_values[:, 3] *= fade_speed

        # Add new sparkles
        for i in range(num_pixels):
            if random.random() < density:
                if random_color:
                    self._sparkle_values[i, 0] = random.randint(0, 255)
                    self._sparkle_values[i, 1] = random.randint(0, 255)
                    self._sparkle_values[i, 2] = random.randint(0, 255)
                else:
                    self._sparkle_values[i, 0:3] = color
                self._sparkle_values[i, 3] = 1.0

        # Generate output
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        for i in range(num_pixels):
            brightness = self._sparkle_values[i, 3]
            if brightness > 0.01:
                sparkle_color = self._sparkle_values[i, 0:3]
                pixels[i] = [
                    int(background[j] + (sparkle_color[j] - background[j]) * brightness)
                    for j in range(3)
                ]
            else:
                pixels[i] = background

        return pixels


class SolidPattern(VirtualSource):
    """Static solid color fill."""

    source_type = "solid"

    def _setup_controls(self) -> None:
        self._controls.register(
            ColorControl(
                id="color",
                name="Color",
                description="Fill color",
                value="#FFFFFF",
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        color = hex_to_rgb(self.get_control("color"))
        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        pixels[:] = color
        return pixels


class GradientPattern(VirtualSource):
    """Gradient between colors."""

    source_type = "gradient"

    def _setup_controls(self) -> None:
        self._controls.register(
            ColorControl(
                id="color1",
                name="Color 1",
                description="Start color",
                value="#FF0000",
                group="pattern",
            )
        )
        self._controls.register(
            ColorControl(
                id="color2",
                name="Color 2",
                description="End color",
                value="#0000FF",
                group="pattern",
            )
        )
        self._controls.register(
            BooleanControl(
                id="animate",
                name="Animate",
                description="Animate the gradient",
                value=False,
                group="pattern",
            )
        )
        self._controls.register(
            EnumControl(
                id="mode",
                name="Mode",
                description="Gradient mode",
                value="linear",
                options=[
                    {"value": "linear", "label": "Linear"},
                    {"value": "reflected", "label": "Reflected"},
                ],
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        color1 = hex_to_rgb(self.get_control("color1"))
        color2 = hex_to_rgb(self.get_control("color2"))
        animate = self.get_control("animate")
        mode = self.get_control("mode")

        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)

        offset = (time_elapsed * 0.2) % 1.0 if animate else 0.0

        for i in range(num_pixels):
            t = i / max(1, num_pixels - 1)

            if mode == "reflected":
                t = 1.0 - abs(2 * t - 1.0)

            if animate:
                t = (t + offset) % 1.0

            pixels[i] = [
                int(color1[j] + (color2[j] - color1[j]) * t) for j in range(3)
            ]

        return pixels


class BreathePattern(VirtualSource):
    """Pulsing brightness effect."""

    source_type = "breathe"

    def _setup_controls(self) -> None:
        self._controls.register(
            ColorControl(
                id="color",
                name="Color",
                description="Pulse color",
                value="#FFFFFF",
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="min_brightness",
                name="Min Brightness",
                description="Minimum brightness",
                value=0.0,
                min=0.0,
                max=1.0,
                step=0.05,
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="max_brightness",
                name="Max Brightness",
                description="Maximum brightness",
                value=1.0,
                min=0.0,
                max=1.0,
                step=0.05,
                group="pattern",
            )
        )
        self._controls.register(
            EnumControl(
                id="waveform",
                name="Waveform",
                description="Pulse waveform",
                value="sine",
                options=[
                    {"value": "sine", "label": "Sine"},
                    {"value": "triangle", "label": "Triangle"},
                    {"value": "square", "label": "Square"},
                    {"value": "sawtooth", "label": "Sawtooth"},
                ],
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        color = hex_to_rgb(self.get_control("color"))
        min_b = self.get_control("min_brightness")
        max_b = self.get_control("max_brightness")
        waveform = self.get_control("waveform")

        # Calculate waveform value (0 to 1)
        t = time_elapsed % 1.0  # 1 second period

        if waveform == "sine":
            wave = (math.sin(t * 2 * math.pi) + 1) / 2
        elif waveform == "triangle":
            wave = 1 - abs(2 * t - 1)
        elif waveform == "square":
            wave = 1.0 if t < 0.5 else 0.0
        elif waveform == "sawtooth":
            wave = t
        else:
            wave = 0.5

        # Map to brightness range
        brightness = min_b + (max_b - min_b) * wave

        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)
        pixels[:] = [int(c * brightness) for c in color]

        return pixels


class StrobePattern(VirtualSource):
    """Flashing strobe effect."""

    source_type = "strobe"

    def _setup_controls(self) -> None:
        self._controls.register(
            ColorControl(
                id="color",
                name="Color",
                description="Strobe color",
                value="#FFFFFF",
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="on_time",
                name="On Time",
                description="On duration in milliseconds",
                value=50,
                min=10,
                max=500,
                step=10,
                unit="ms",
                group="pattern",
            )
        )
        self._controls.register(
            NumberControl(
                id="off_time",
                name="Off Time",
                description="Off duration in milliseconds",
                value=50,
                min=10,
                max=1000,
                step=10,
                unit="ms",
                group="pattern",
            )
        )

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        color = hex_to_rgb(self.get_control("color"))
        on_time = self.get_control("on_time") / 1000.0
        off_time = self.get_control("off_time") / 1000.0

        period = on_time + off_time
        t = time_elapsed % period

        pixels = np.zeros((num_pixels, 3), dtype=np.uint8)

        if t < on_time:
            pixels[:] = color

        return pixels
