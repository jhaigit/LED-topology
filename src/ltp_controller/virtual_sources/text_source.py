"""Text virtual source for LED matrices.

Displays text with various formatting and scrolling options.
"""

from typing import Any

import numpy as np

from libltp import (
    NumberControl,
    BooleanControl,
    EnumControl,
    ColorControl,
    StringControl,
)

from ltp_controller.virtual_sources.base import VirtualSource, VirtualSourceConfig
from ltp_controller.virtual_sources.text_renderer import (
    TextRenderer,
    TextAlign,
    VerticalAlign,
    ScrollDirection,
    measure_text,
    wrap_text,
    hex_to_rgb,
)
from ltp_controller.virtual_sources.fonts import (
    list_fonts,
    list_all_fonts,
    DEFAULT_FONT,
    HAS_PIL,
    is_ttf_font,
    parse_ttf_font_spec,
)


class TextSource(VirtualSource):
    """Virtual source that displays text on LED matrices.

    Features:
    - Static or scrolling text
    - Multiple fonts (5x7, 4x6, 3x5)
    - Horizontal and vertical scrolling
    - Text alignment (left, center, right)
    - Configurable colors
    - Word wrapping for multiline
    - Dynamic text updates via set_data()

    Example:
        source = TextSource()
        source.set_control("text", "Hello World!")
        source.set_control("scroll_mode", "left")  # Enable scrolling
    """

    source_type = "text"

    def _setup_controls(self) -> None:
        """Set up text-specific controls."""
        # Text content
        self._controls.register(
            StringControl(
                id="text",
                name="Text",
                description="Text to display",
                value="Hello",
                group="text",
            )
        )

        # Font selection - include both bitmap and TTF fonts
        font_options = []
        for font_info in list_all_fonts():
            font_options.append({
                "value": font_info["name"],
                "label": font_info["label"],
            })
        self._controls.register(
            EnumControl(
                id="font",
                name="Font",
                description="Font to use (TTF fonts require PIL)",
                value=DEFAULT_FONT,
                options=font_options,
                group="text",
            )
        )

        # TTF font size (only applies to TrueType fonts)
        self._controls.register(
            NumberControl(
                id="ttf_size",
                name="TTF Size",
                description="Font size for TrueType fonts (pixels)",
                value=16.0,
                min=6.0,
                max=64.0,
                step=1.0,
                group="text",
            )
        )

        # Anti-aliasing (TTF only)
        self._controls.register(
            BooleanControl(
                id="antialias",
                name="Anti-alias",
                description="Smooth edges for TTF fonts",
                value=True,
                group="text",
            )
        )

        # Colors
        self._controls.register(
            ColorControl(
                id="text_color",
                name="Text Color",
                description="Foreground text color",
                value="#FFFFFF",
                group="text",
            )
        )
        self._controls.register(
            ColorControl(
                id="background_color",
                name="Background",
                description="Background color",
                value="#000000",
                group="text",
            )
        )

        # Alignment
        self._controls.register(
            EnumControl(
                id="align",
                name="Horizontal Align",
                description="Horizontal text alignment",
                value="center",
                options=[
                    {"value": "left", "label": "Left"},
                    {"value": "center", "label": "Center"},
                    {"value": "right", "label": "Right"},
                ],
                group="text",
            )
        )
        self._controls.register(
            EnumControl(
                id="vertical_align",
                name="Vertical Align",
                description="Vertical text alignment",
                value="middle",
                options=[
                    {"value": "top", "label": "Top"},
                    {"value": "middle", "label": "Middle"},
                    {"value": "bottom", "label": "Bottom"},
                ],
                group="text",
            )
        )

        # Scrolling
        self._controls.register(
            EnumControl(
                id="scroll_mode",
                name="Scroll Mode",
                description="Scrolling direction",
                value="none",
                options=[
                    {"value": "none", "label": "None"},
                    {"value": "left", "label": "Left"},
                    {"value": "right", "label": "Right"},
                    {"value": "up", "label": "Up"},
                    {"value": "down", "label": "Down"},
                ],
                group="animation",
            )
        )
        self._controls.register(
            NumberControl(
                id="scroll_speed",
                name="Scroll Speed",
                description="Pixels per second",
                value=30.0,
                min=1.0,
                max=200.0,
                step=1.0,
                group="animation",
            )
        )
        self._controls.register(
            BooleanControl(
                id="scroll_gap",
                name="Scroll Gap",
                description="Add gap between text repeats",
                value=True,
                group="animation",
            )
        )

        # Multiline options
        self._controls.register(
            BooleanControl(
                id="word_wrap",
                name="Word Wrap",
                description="Wrap text to multiple lines",
                value=False,
                group="text",
            )
        )
        self._controls.register(
            NumberControl(
                id="line_spacing",
                name="Line Spacing",
                description="Pixels between lines",
                value=1.0,
                min=0.0,
                max=8.0,
                step=1.0,
                group="text",
            )
        )

        # Effects
        self._controls.register(
            BooleanControl(
                id="rainbow_mode",
                name="Rainbow Mode",
                description="Cycle through rainbow colors",
                value=False,
                group="effects",
            )
        )
        self._controls.register(
            NumberControl(
                id="rainbow_speed",
                name="Rainbow Speed",
                description="Color cycle speed",
                value=1.0,
                min=0.1,
                max=10.0,
                step=0.1,
                group="effects",
            )
        )
        self._controls.register(
            BooleanControl(
                id="blink",
                name="Blink",
                description="Blink text on/off",
                value=False,
                group="effects",
            )
        )
        self._controls.register(
            NumberControl(
                id="blink_rate",
                name="Blink Rate",
                description="Blinks per second",
                value=2.0,
                min=0.5,
                max=10.0,
                step=0.5,
                group="effects",
            )
        )

        # Internal state
        self._renderer: TextRenderer | None = None
        self._cached_text = ""
        self._cached_font = ""
        self._scroll_offset = 0.0

    def set_data(self, data: Any) -> None:
        """Set text dynamically.

        Args:
            data: Can be a string or dict with 'text' key
        """
        if isinstance(data, str):
            self._controls.set_value("text", data)
        elif isinstance(data, dict) and "text" in data:
            self._controls.set_value("text", data["text"])
            # Optionally update other settings
            if "color" in data:
                self._controls.set_value("text_color", data["color"])
            if "scroll_mode" in data:
                self._controls.set_value("scroll_mode", data["scroll_mode"])

    def _get_renderer(self, width: int, height: int) -> TextRenderer:
        """Get or create the text renderer."""
        font = self.get_control("font")

        # For TTF fonts, append size to create font spec like "DejaVuSans:16"
        if is_ttf_font(font) and ":" not in font:
            ttf_size = int(self.get_control("ttf_size"))
            font = f"{font}:{ttf_size}"

        text_color = hex_to_rgb(self.get_control("text_color"))
        bg_color = hex_to_rgb(self.get_control("background_color"))

        align_str = self.get_control("align")
        valign_str = self.get_control("vertical_align")

        align = TextAlign(align_str)
        valign = VerticalAlign(valign_str)
        antialias = self.get_control("antialias")

        if self._renderer is None or self._renderer.width != width or self._renderer.height != height:
            self._renderer = TextRenderer(
                width=width,
                height=height,
                font_name=font,
                text_color=text_color,
                background_color=bg_color,
                align=align,
                vertical_align=valign,
                antialias=antialias,
            )
        else:
            # Update settings
            self._renderer.set_font(font)
            self._renderer.text_color = text_color
            self._renderer.background_color = bg_color
            self._renderer.align = align
            self._renderer.vertical_align = valign
            self._renderer.antialias = antialias

        return self._renderer

    def _get_rainbow_color(self, time_elapsed: float) -> tuple[int, int, int]:
        """Generate rainbow color based on time."""
        speed = self.get_control("rainbow_speed")
        hue = (time_elapsed * speed) % 1.0

        # HSV to RGB conversion (S=1, V=1)
        h = hue * 6
        i = int(h)
        f = h - i
        q = 1 - f

        if i == 0:
            r, g, b = 1, f, 0
        elif i == 1:
            r, g, b = q, 1, 0
        elif i == 2:
            r, g, b = 0, 1, f
        elif i == 3:
            r, g, b = 0, q, 1
        elif i == 4:
            r, g, b = f, 0, 1
        else:
            r, g, b = 1, 0, q

        return (int(r * 255), int(g * 255), int(b * 255))

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        """Render text to pixel array.

        Args:
            num_pixels: Total number of pixels
            time_elapsed: Time since source started

        Returns:
            numpy array of shape (num_pixels, 3) with RGB values
        """
        # Get dimensions
        dims = self.config.output_dimensions
        if len(dims) >= 2:
            width, height = dims[0], dims[1]
        else:
            width = dims[0]
            height = 1

        # Ensure dimensions match pixel count
        if width * height != num_pixels:
            # Fallback to linear interpretation
            width = num_pixels
            height = 1

        # Get text content
        text = self.get_control("text")

        # Check blink mode
        if self.get_control("blink"):
            blink_rate = self.get_control("blink_rate")
            if int(time_elapsed * blink_rate * 2) % 2 == 0:
                # Return background only during "off" phase
                renderer = self._get_renderer(width, height)
                output = np.zeros((height, width, 3), dtype=np.uint8)
                output[:] = renderer.background_color
                return output.reshape(-1, 3)

        # Get renderer
        renderer = self._get_renderer(width, height)

        # Apply rainbow mode
        if self.get_control("rainbow_mode"):
            renderer.text_color = self._get_rainbow_color(time_elapsed)

        # Get scroll settings
        scroll_mode_str = self.get_control("scroll_mode")
        scroll_mode = ScrollDirection(scroll_mode_str)
        scroll_speed = self.get_control("scroll_speed")
        speed_multiplier = self.get_control("speed")

        # Word wrap handling
        word_wrap = self.get_control("word_wrap")
        line_spacing = int(self.get_control("line_spacing"))

        if word_wrap and scroll_mode == ScrollDirection.NONE:
            # Render wrapped multiline text
            lines = wrap_text(text, width, renderer.font_name)
            frame = renderer.render_multiline(lines, line_spacing)
        elif scroll_mode == ScrollDirection.NONE:
            # Static single line
            frame = renderer.render_static(text)
        else:
            # Scrolling text
            metrics = measure_text(text, renderer.font_name)

            # Calculate scroll position
            if scroll_mode in (ScrollDirection.LEFT, ScrollDirection.RIGHT):
                total_distance = metrics.width + (width if self.get_control("scroll_gap") else 0)
            else:
                # Use metrics.height which works for both bitmap and TTF fonts
                total_distance = metrics.height + (height if self.get_control("scroll_gap") else 0)

            if total_distance > 0:
                # Pixels scrolled = time * speed * multiplier
                pixels_scrolled = time_elapsed * scroll_speed * speed_multiplier
                scroll_position = (pixels_scrolled % total_distance) / total_distance
            else:
                scroll_position = 0.0

            # Calculate padding
            padding = width if self.get_control("scroll_gap") else 0
            if scroll_mode in (ScrollDirection.UP, ScrollDirection.DOWN):
                padding = height if self.get_control("scroll_gap") else 0

            frame = renderer.render_scrolling(text, scroll_position, scroll_mode, padding)

        # Apply brightness
        brightness = self.get_control("brightness")
        if brightness < 1.0:
            frame = (frame * brightness).astype(np.uint8)

        # Convert to linear array
        return renderer.to_linear(frame)


class CounterSource(VirtualSource):
    """Virtual source that displays a counting number.

    Useful for testing, timing displays, or showing numeric data.
    """

    source_type = "counter"

    def _setup_controls(self) -> None:
        """Set up counter-specific controls."""
        self._controls.register(
            NumberControl(
                id="start_value",
                name="Start Value",
                description="Starting count value",
                value=0.0,
                min=-9999999.0,
                max=9999999.0,
                step=1.0,
                group="counter",
            )
        )
        self._controls.register(
            NumberControl(
                id="increment",
                name="Increment",
                description="Count increment per second",
                value=1.0,
                min=-1000.0,
                max=1000.0,
                step=0.1,
                group="counter",
            )
        )
        self._controls.register(
            NumberControl(
                id="decimals",
                name="Decimal Places",
                description="Number of decimal places",
                value=0.0,
                min=0.0,
                max=4.0,
                step=1.0,
                group="counter",
            )
        )
        self._controls.register(
            StringControl(
                id="prefix",
                name="Prefix",
                description="Text before number",
                value="",
                group="counter",
            )
        )
        self._controls.register(
            StringControl(
                id="suffix",
                name="Suffix",
                description="Text after number",
                value="",
                group="counter",
            )
        )

        # Inherit text display controls - include TTF fonts
        font_options = []
        for font_info in list_all_fonts():
            font_options.append({
                "value": font_info["name"],
                "label": font_info["label"],
            })
        self._controls.register(
            EnumControl(
                id="font",
                name="Font",
                description="Font to use",
                value=DEFAULT_FONT,
                options=font_options,
                group="text",
            )
        )
        self._controls.register(
            NumberControl(
                id="ttf_size",
                name="TTF Size",
                description="Font size for TrueType fonts (pixels)",
                value=16.0,
                min=6.0,
                max=64.0,
                step=1.0,
                group="text",
            )
        )
        self._controls.register(
            ColorControl(
                id="text_color",
                name="Text Color",
                description="Text color",
                value="#00FF00",
                group="text",
            )
        )
        self._controls.register(
            ColorControl(
                id="background_color",
                name="Background",
                description="Background color",
                value="#000000",
                group="text",
            )
        )
        self._controls.register(
            EnumControl(
                id="align",
                name="Align",
                description="Text alignment",
                value="center",
                options=[
                    {"value": "left", "label": "Left"},
                    {"value": "center", "label": "Center"},
                    {"value": "right", "label": "Right"},
                ],
                group="text",
            )
        )

        self._renderer: TextRenderer | None = None

    def _get_font_spec(self) -> str:
        """Get font specification, adding size for TTF fonts."""
        font = self.get_control("font")
        if is_ttf_font(font) and ":" not in font:
            ttf_size = int(self.get_control("ttf_size"))
            font = f"{font}:{ttf_size}"
        return font

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        """Render counter display."""
        dims = self.config.output_dimensions
        if len(dims) >= 2:
            width, height = dims[0], dims[1]
        else:
            width = dims[0]
            height = 1

        if width * height != num_pixels:
            width = num_pixels
            height = 1

        # Calculate current count
        start = self.get_control("start_value")
        increment = self.get_control("increment")
        speed = self.get_control("speed")
        decimals = int(self.get_control("decimals"))

        current_value = start + (time_elapsed * increment * speed)

        # Format number
        if decimals == 0:
            number_str = str(int(current_value))
        else:
            number_str = f"{current_value:.{decimals}f}"

        # Build display text
        prefix = self.get_control("prefix")
        suffix = self.get_control("suffix")
        text = f"{prefix}{number_str}{suffix}"

        # Get/create renderer
        if self._renderer is None or self._renderer.width != width or self._renderer.height != height:
            self._renderer = TextRenderer(width, height)

        self._renderer.set_font(self._get_font_spec())
        self._renderer.text_color = hex_to_rgb(self.get_control("text_color"))
        self._renderer.background_color = hex_to_rgb(self.get_control("background_color"))
        self._renderer.align = TextAlign(self.get_control("align"))
        self._renderer.vertical_align = VerticalAlign.MIDDLE

        frame = self._renderer.render_static(text)

        brightness = self.get_control("brightness")
        if brightness < 1.0:
            frame = (frame * brightness).astype(np.uint8)

        return self._renderer.to_linear(frame)


class ClockSource(VirtualSource):
    """Virtual source that displays the current time.

    Shows digital clock with configurable format.
    """

    source_type = "clock"

    def _setup_controls(self) -> None:
        """Set up clock-specific controls."""
        self._controls.register(
            EnumControl(
                id="format",
                name="Time Format",
                description="Clock display format",
                value="24h",
                options=[
                    {"value": "24h", "label": "24 Hour"},
                    {"value": "12h", "label": "12 Hour"},
                    {"value": "24h_seconds", "label": "24 Hour + Seconds"},
                    {"value": "12h_seconds", "label": "12 Hour + Seconds"},
                ],
                group="clock",
            )
        )
        self._controls.register(
            BooleanControl(
                id="blink_colon",
                name="Blink Colon",
                description="Blink the colon separator",
                value=True,
                group="clock",
            )
        )
        self._controls.register(
            BooleanControl(
                id="show_date",
                name="Show Date",
                description="Show date on second line",
                value=False,
                group="clock",
            )
        )

        # Text display controls - include TTF fonts
        font_options = []
        for font_info in list_all_fonts():
            font_options.append({
                "value": font_info["name"],
                "label": font_info["label"],
            })
        self._controls.register(
            EnumControl(
                id="font",
                name="Font",
                description="Font to use",
                value=DEFAULT_FONT,
                options=font_options,
                group="text",
            )
        )
        self._controls.register(
            NumberControl(
                id="ttf_size",
                name="TTF Size",
                description="Font size for TrueType fonts (pixels)",
                value=16.0,
                min=6.0,
                max=64.0,
                step=1.0,
                group="text",
            )
        )
        self._controls.register(
            ColorControl(
                id="text_color",
                name="Text Color",
                description="Text color",
                value="#00FFFF",
                group="text",
            )
        )
        self._controls.register(
            ColorControl(
                id="background_color",
                name="Background",
                description="Background color",
                value="#000000",
                group="text",
            )
        )

        self._renderer: TextRenderer | None = None

    def _get_font_spec(self) -> str:
        """Get font specification, adding size for TTF fonts."""
        font = self.get_control("font")
        if is_ttf_font(font) and ":" not in font:
            ttf_size = int(self.get_control("ttf_size"))
            font = f"{font}:{ttf_size}"
        return font

    def render(self, num_pixels: int, time_elapsed: float) -> np.ndarray:
        """Render clock display."""
        import time as time_module

        dims = self.config.output_dimensions
        if len(dims) >= 2:
            width, height = dims[0], dims[1]
        else:
            width = dims[0]
            height = 1

        if width * height != num_pixels:
            width = num_pixels
            height = 1

        # Get current time
        now = time_module.localtime()
        format_type = self.get_control("format")
        blink_colon = self.get_control("blink_colon")

        # Determine colon character
        colon = ":" if not blink_colon or int(time_elapsed * 2) % 2 == 0 else " "

        # Format time string
        if format_type == "24h":
            time_str = f"{now.tm_hour:02d}{colon}{now.tm_min:02d}"
        elif format_type == "12h":
            hour = now.tm_hour % 12
            if hour == 0:
                hour = 12
            am_pm = "A" if now.tm_hour < 12 else "P"
            time_str = f"{hour:2d}{colon}{now.tm_min:02d}{am_pm}"
        elif format_type == "24h_seconds":
            time_str = f"{now.tm_hour:02d}{colon}{now.tm_min:02d}{colon}{now.tm_sec:02d}"
        else:  # 12h_seconds
            hour = now.tm_hour % 12
            if hour == 0:
                hour = 12
            am_pm = "A" if now.tm_hour < 12 else "P"
            time_str = f"{hour:2d}{colon}{now.tm_min:02d}{colon}{now.tm_sec:02d}{am_pm}"

        # Get/create renderer
        if self._renderer is None or self._renderer.width != width or self._renderer.height != height:
            self._renderer = TextRenderer(width, height)

        self._renderer.set_font(self._get_font_spec())
        self._renderer.text_color = hex_to_rgb(self.get_control("text_color"))
        self._renderer.background_color = hex_to_rgb(self.get_control("background_color"))
        self._renderer.align = TextAlign.CENTER
        self._renderer.vertical_align = VerticalAlign.MIDDLE

        # Check if showing date
        if self.get_control("show_date") and height >= 12:
            date_str = f"{now.tm_mon:02d}/{now.tm_mday:02d}"
            frame = self._renderer.render_multiline([time_str, date_str], line_spacing=1)
        else:
            frame = self._renderer.render_static(time_str)

        brightness = self.get_control("brightness")
        if brightness < 1.0:
            frame = (frame * brightness).astype(np.uint8)

        return self._renderer.to_linear(frame)
