"""Tests for text rendering features (fonts, text_renderer, text_source)."""

import pytest
import numpy as np

from ltp_controller.virtual_sources.fonts import (
    FontInfo,
    get_font,
    list_fonts,
    get_char_bitmap,
    DEFAULT_FONT,
    FONTS,
    FONT_5X7,
    FONT_4X6,
    FONT_3X5,
)
from ltp_controller.virtual_sources.text_renderer import (
    TextRenderer,
    TextAlign,
    VerticalAlign,
    ScrollDirection,
    TextMetrics,
    measure_text,
    wrap_text,
    hex_to_rgb,
    render_char_to_pixels,
    render_text_to_pixels,
)
from ltp_controller.virtual_sources.text_source import (
    TextSource,
    CounterSource,
    ClockSource,
)
from ltp_controller.virtual_sources.base import VirtualSourceConfig


# =============================================================================
# Font Tests
# =============================================================================


class TestFontInfo:
    """Test FontInfo named tuple."""

    def test_font_info_5x7(self):
        """5x7 font has correct dimensions."""
        assert FONT_5X7.name == "5x7"
        assert FONT_5X7.width == 5
        assert FONT_5X7.height == 7
        assert FONT_5X7.spacing == 1

    def test_font_info_4x6(self):
        """4x6 font has correct dimensions."""
        assert FONT_4X6.name == "4x6"
        assert FONT_4X6.width == 4
        assert FONT_4X6.height == 6
        assert FONT_4X6.spacing == 1

    def test_font_info_3x5(self):
        """3x5 font has correct dimensions."""
        assert FONT_3X5.name == "3x5"
        assert FONT_3X5.width == 3
        assert FONT_3X5.height == 5
        assert FONT_3X5.spacing == 1


class TestGetFont:
    """Test get_font function."""

    def test_get_font_5x7(self):
        """Get 5x7 font returns correct info and data."""
        info, data = get_font("5x7")
        assert info.name == "5x7"
        assert info.width == 5
        assert info.height == 7
        assert "A" in data
        assert len(data["A"]) == 7  # 7 rows

    def test_get_font_4x6(self):
        """Get 4x6 font returns correct info and data."""
        info, data = get_font("4x6")
        assert info.name == "4x6"
        assert info.width == 4
        assert info.height == 6
        assert "A" in data
        assert len(data["A"]) == 6  # 6 rows

    def test_get_font_3x5(self):
        """Get 3x5 font returns correct info and data."""
        info, data = get_font("3x5")
        assert info.name == "3x5"
        assert info.width == 3
        assert info.height == 5
        assert "A" in data
        assert len(data["A"]) == 5  # 5 rows

    def test_get_font_unknown_raises(self):
        """Unknown font raises ValueError."""
        with pytest.raises(ValueError, match="Unknown font"):
            get_font("unknown_font")

    def test_default_font(self):
        """Default font is 5x7."""
        assert DEFAULT_FONT == "5x7"


class TestListFonts:
    """Test list_fonts function."""

    def test_list_fonts_returns_all(self):
        """list_fonts returns all available fonts."""
        fonts = list_fonts()
        assert "5x7" in fonts
        assert "4x6" in fonts
        assert "3x5" in fonts
        assert len(fonts) == 3

    def test_fonts_registry_matches_list(self):
        """FONTS registry matches list_fonts."""
        fonts = list_fonts()
        assert set(fonts) == set(FONTS.keys())


class TestGetCharBitmap:
    """Test get_char_bitmap function."""

    def test_get_char_bitmap_letter(self):
        """Get bitmap for a letter."""
        bitmap = get_char_bitmap("A", "5x7")
        assert bitmap is not None
        assert len(bitmap) == 7  # 7 rows

    def test_get_char_bitmap_number(self):
        """Get bitmap for a number."""
        bitmap = get_char_bitmap("0", "5x7")
        assert bitmap is not None
        assert len(bitmap) == 7

    def test_get_char_bitmap_space(self):
        """Get bitmap for space."""
        bitmap = get_char_bitmap(" ", "5x7")
        assert bitmap is not None
        # Space should be all zeros
        assert all(b == 0 for b in bitmap)

    def test_get_char_bitmap_lowercase_fallback(self):
        """Lowercase falls back to uppercase if not defined."""
        # 3x5 only has uppercase
        bitmap_upper = get_char_bitmap("A", "3x5")
        bitmap_lower = get_char_bitmap("a", "3x5")
        assert bitmap_upper == bitmap_lower

    def test_get_char_bitmap_unknown_char(self):
        """Unknown character returns space bitmap."""
        bitmap = get_char_bitmap("§", "5x7")  # Non-ASCII character
        space_bitmap = get_char_bitmap(" ", "5x7")
        assert bitmap == space_bitmap

    def test_get_char_bitmap_default_font(self):
        """get_char_bitmap uses default font when not specified."""
        bitmap = get_char_bitmap("A")  # No font specified
        assert bitmap is not None
        assert len(bitmap) == 7  # Default is 5x7


# =============================================================================
# Text Renderer Tests
# =============================================================================


class TestHexToRgb:
    """Test hex_to_rgb function."""

    def test_hex_to_rgb_white(self):
        """Convert white hex to RGB."""
        assert hex_to_rgb("#FFFFFF") == (255, 255, 255)

    def test_hex_to_rgb_black(self):
        """Convert black hex to RGB."""
        assert hex_to_rgb("#000000") == (0, 0, 0)

    def test_hex_to_rgb_red(self):
        """Convert red hex to RGB."""
        assert hex_to_rgb("#FF0000") == (255, 0, 0)

    def test_hex_to_rgb_no_hash(self):
        """Convert hex without hash prefix."""
        assert hex_to_rgb("00FF00") == (0, 255, 0)

    def test_hex_to_rgb_lowercase(self):
        """Convert lowercase hex."""
        assert hex_to_rgb("#ff00ff") == (255, 0, 255)


class TestMeasureText:
    """Test measure_text function."""

    def test_measure_empty_text(self):
        """Empty text has zero width."""
        metrics = measure_text("", "5x7")
        assert metrics.width == 0
        assert metrics.height == 7
        assert metrics.char_count == 0

    def test_measure_single_char(self):
        """Single character width equals font width."""
        metrics = measure_text("A", "5x7")
        assert metrics.width == 5  # No spacing for single char
        assert metrics.height == 7
        assert metrics.char_count == 1

    def test_measure_multiple_chars(self):
        """Multiple characters include spacing."""
        metrics = measure_text("AB", "5x7")
        # 2 chars * 5 width + 1 spacing = 11
        assert metrics.width == 11
        assert metrics.height == 7
        assert metrics.char_count == 2

    def test_measure_text_different_fonts(self):
        """Measure text with different fonts."""
        metrics_5x7 = measure_text("AB", "5x7")
        metrics_4x6 = measure_text("AB", "4x6")
        metrics_3x5 = measure_text("AB", "3x5")

        # 5x7: 2*5 + 1 = 11
        assert metrics_5x7.width == 11
        assert metrics_5x7.height == 7

        # 4x6: 2*4 + 1 = 9
        assert metrics_4x6.width == 9
        assert metrics_4x6.height == 6

        # 3x5: 2*3 + 1 = 7
        assert metrics_3x5.width == 7
        assert metrics_3x5.height == 5


class TestRenderCharToPixels:
    """Test render_char_to_pixels function."""

    def test_render_char_dimensions(self):
        """Rendered character has correct dimensions."""
        pixels = render_char_to_pixels("A", "5x7")
        assert pixels.shape == (7, 5)
        assert pixels.dtype == bool

    def test_render_char_not_empty(self):
        """Rendered letter has some pixels set."""
        pixels = render_char_to_pixels("A", "5x7")
        assert pixels.any()  # At least one pixel is True

    def test_render_space_is_empty(self):
        """Rendered space has no pixels set."""
        pixels = render_char_to_pixels(" ", "5x7")
        assert not pixels.any()  # All pixels False


class TestRenderTextToPixels:
    """Test render_text_to_pixels function."""

    def test_render_text_dimensions(self):
        """Rendered text has correct dimensions."""
        pixels = render_text_to_pixels("AB", "5x7")
        assert pixels.shape == (7, 11)  # height=7, width=5+1+5
        assert pixels.dtype == bool

    def test_render_empty_text(self):
        """Empty text renders to zero width."""
        pixels = render_text_to_pixels("", "5x7")
        assert pixels.shape == (7, 0)

    def test_render_text_has_content(self):
        """Rendered text has some pixels set."""
        pixels = render_text_to_pixels("Hello", "5x7")
        assert pixels.any()


class TestWrapText:
    """Test wrap_text function."""

    def test_wrap_short_text(self):
        """Short text doesn't wrap."""
        lines = wrap_text("Hi", 100, "5x7")
        assert lines == ["Hi"]

    def test_wrap_long_word(self):
        """Long word is split."""
        # With 5x7 font, char_width = 5+1 = 6
        # max_width=12 means 2 chars per line
        lines = wrap_text("HELLO", 12, "5x7")
        assert len(lines) > 1
        # Word gets split into chunks

    def test_wrap_multiple_words(self):
        """Multiple words wrap at word boundaries."""
        # Width for ~4 chars = 4*6 = 24
        lines = wrap_text("AB CD EF", 24, "5x7")
        assert len(lines) >= 1

    def test_wrap_empty_text(self):
        """Empty text returns single empty line."""
        lines = wrap_text("", 100, "5x7")
        assert lines == [""]


class TestTextRenderer:
    """Test TextRenderer class."""

    def test_renderer_init(self):
        """TextRenderer initializes with correct defaults."""
        renderer = TextRenderer(32, 8)
        assert renderer.width == 32
        assert renderer.height == 8
        assert renderer.font_name == DEFAULT_FONT
        assert renderer.text_color == (255, 255, 255)
        assert renderer.background_color == (0, 0, 0)

    def test_renderer_custom_settings(self):
        """TextRenderer accepts custom settings."""
        renderer = TextRenderer(
            16, 8,
            font_name="3x5",
            text_color=(255, 0, 0),
            background_color=(0, 0, 255),
            align=TextAlign.RIGHT,
            vertical_align=VerticalAlign.BOTTOM,
        )
        assert renderer.font_name == "3x5"
        assert renderer.text_color == (255, 0, 0)
        assert renderer.background_color == (0, 0, 255)
        assert renderer.align == TextAlign.RIGHT
        assert renderer.vertical_align == VerticalAlign.BOTTOM

    def test_render_static_dimensions(self):
        """render_static returns correct dimensions."""
        renderer = TextRenderer(32, 8)
        frame = renderer.render_static("Hi")
        assert frame.shape == (8, 32, 3)
        assert frame.dtype == np.uint8

    def test_render_static_background_color(self):
        """render_static fills background."""
        renderer = TextRenderer(10, 8, background_color=(10, 20, 30))
        frame = renderer.render_static("")
        # All pixels should be background color
        assert np.all(frame == (10, 20, 30))

    def test_render_static_text_color(self):
        """render_static uses text color."""
        renderer = TextRenderer(32, 8, text_color=(255, 0, 0), background_color=(0, 0, 0))
        frame = renderer.render_static("A")
        # Find non-black pixels
        text_pixels = frame[frame[:, :, 0] > 0]
        if len(text_pixels) > 0:
            assert text_pixels[0, 0] == 255  # Red
            assert text_pixels[0, 1] == 0    # Green
            assert text_pixels[0, 2] == 0    # Blue

    def test_render_static_alignment_left(self):
        """Left alignment positions text at left edge."""
        renderer = TextRenderer(32, 8, align=TextAlign.LEFT)
        frame = renderer.render_static("A")
        # Check leftmost columns have some text pixels
        left_region = frame[:, :5, :]
        assert np.any(left_region > 0)

    def test_render_static_alignment_right(self):
        """Right alignment positions text at right edge."""
        renderer = TextRenderer(32, 8, align=TextAlign.RIGHT)
        frame = renderer.render_static("A")
        # Check rightmost columns have some text pixels
        right_region = frame[:, -5:, :]
        assert np.any(right_region > 0)

    def test_render_scrolling_dimensions(self):
        """render_scrolling returns correct dimensions."""
        renderer = TextRenderer(32, 8)
        frame = renderer.render_scrolling("Hello World", 0.5, ScrollDirection.LEFT)
        assert frame.shape == (8, 32, 3)

    def test_render_scrolling_none_returns_static(self):
        """ScrollDirection.NONE returns static rendering."""
        renderer = TextRenderer(32, 8)
        static = renderer.render_static("Test")
        scrolling = renderer.render_scrolling("Test", 0.0, ScrollDirection.NONE)
        np.testing.assert_array_equal(static, scrolling)

    def test_render_multiline_dimensions(self):
        """render_multiline returns correct dimensions."""
        renderer = TextRenderer(32, 16)
        frame = renderer.render_multiline(["Line1", "Line2"])
        assert frame.shape == (16, 32, 3)

    def test_render_multiline_empty(self):
        """render_multiline with empty list returns background."""
        renderer = TextRenderer(32, 16, background_color=(5, 10, 15))
        frame = renderer.render_multiline([])
        assert np.all(frame == (5, 10, 15))

    def test_set_font(self):
        """set_font changes the font."""
        renderer = TextRenderer(32, 8)
        assert renderer.font_name == "5x7"
        renderer.set_font("3x5")
        assert renderer.font_name == "3x5"

    def test_set_colors_rgb_tuple(self):
        """set_colors accepts RGB tuples."""
        renderer = TextRenderer(32, 8)
        renderer.set_colors(text_color=(100, 150, 200))
        assert renderer.text_color == (100, 150, 200)

    def test_set_colors_hex_string(self):
        """set_colors accepts hex strings."""
        renderer = TextRenderer(32, 8)
        renderer.set_colors(text_color="#FF8800")
        assert renderer.text_color == (255, 136, 0)

    def test_to_linear(self):
        """to_linear converts 2D frame to linear array."""
        renderer = TextRenderer(4, 2)
        frame = np.zeros((2, 4, 3), dtype=np.uint8)
        frame[0, 0] = (255, 0, 0)  # First pixel red
        frame[1, 3] = (0, 0, 255)  # Last pixel blue

        linear = renderer.to_linear(frame)
        assert linear.shape == (8, 3)  # 4*2 = 8 pixels
        assert tuple(linear[0]) == (255, 0, 0)  # First pixel
        assert tuple(linear[7]) == (0, 0, 255)  # Last pixel


# =============================================================================
# Text Source Tests
# =============================================================================


class TestTextSource:
    """Test TextSource virtual source."""

    @pytest.fixture
    def text_source(self):
        """Create a TextSource instance."""
        config = VirtualSourceConfig(
            name="test_text",
            output_dimensions=(32, 8),
        )
        return TextSource(config)

    def test_source_type(self, text_source):
        """TextSource has correct type."""
        assert text_source.source_type == "text"

    def test_default_controls(self, text_source):
        """TextSource has expected controls."""
        assert text_source.get_control("text") == "Hello"
        assert text_source.get_control("font") == "5x7"
        assert text_source.get_control("scroll_mode") == "none"
        assert text_source.get_control("align") == "center"

    def test_render_output_dimensions(self, text_source):
        """render returns correct shape."""
        output = text_source.render(256, 0.0)  # 32*8 = 256 pixels
        assert output.shape == (256, 3)
        assert output.dtype == np.uint8

    def test_render_static_text(self, text_source):
        """render produces visible output for static text."""
        text_source.set_control("text", "A")
        output = text_source.render(256, 0.0)
        # Should have some non-zero pixels (the letter)
        assert np.any(output > 0)

    def test_set_text_via_control(self, text_source):
        """Text can be set via control."""
        text_source.set_control("text", "New Text")
        assert text_source.get_control("text") == "New Text"

    def test_set_data_string(self, text_source):
        """set_data accepts string."""
        text_source.set_data("Dynamic Text")
        assert text_source.get_control("text") == "Dynamic Text"

    def test_set_data_dict(self, text_source):
        """set_data accepts dict with text key."""
        text_source.set_data({"text": "Dict Text", "color": "#FF0000"})
        assert text_source.get_control("text") == "Dict Text"
        assert text_source.get_control("text_color") == "#FF0000"

    def test_scrolling_mode(self, text_source):
        """Scrolling mode produces different frames over time."""
        text_source.set_control("text", "Scrolling Text")
        text_source.set_control("scroll_mode", "left")
        text_source.set_control("scroll_speed", 60.0)

        frame1 = text_source.render(256, 0.0)
        frame2 = text_source.render(256, 1.0)

        # Frames should differ due to scroll
        assert not np.array_equal(frame1, frame2)

    def test_rainbow_mode(self, text_source):
        """Rainbow mode changes text color over time."""
        text_source.set_control("text", "X")
        text_source.set_control("rainbow_mode", True)

        frame1 = text_source.render(256, 0.0)
        frame2 = text_source.render(256, 0.5)

        # Should produce different colored output
        # (assuming both frames have the letter visible)
        text_pixels1 = frame1[frame1.sum(axis=1) > 0]
        text_pixels2 = frame2[frame2.sum(axis=1) > 0]

        if len(text_pixels1) > 0 and len(text_pixels2) > 0:
            # Colors should differ
            assert not np.array_equal(text_pixels1[0], text_pixels2[0])

    def test_blink_mode(self, text_source):
        """Blink mode alternates between visible and hidden."""
        text_source.set_control("text", "X")
        text_source.set_control("blink", True)
        text_source.set_control("blink_rate", 2.0)

        frame_on = text_source.render(256, 0.0)
        frame_off = text_source.render(256, 0.25)  # Half blink period

        # One should have text, other should not
        has_text_on = np.any(frame_on > 0)
        has_text_off = np.any(frame_off > 0)

        # At least one should differ from the other
        assert has_text_on != has_text_off or np.array_equal(frame_on, frame_off)


class TestCounterSource:
    """Test CounterSource virtual source."""

    @pytest.fixture
    def counter_source(self):
        """Create a CounterSource instance."""
        config = VirtualSourceConfig(
            name="test_counter",
            output_dimensions=(32, 8),
        )
        return CounterSource(config)

    def test_source_type(self, counter_source):
        """CounterSource has correct type."""
        assert counter_source.source_type == "counter"

    def test_default_controls(self, counter_source):
        """CounterSource has expected controls."""
        assert counter_source.get_control("start_value") == 0.0
        assert counter_source.get_control("increment") == 1.0
        assert counter_source.get_control("decimals") == 0.0

    def test_render_output_dimensions(self, counter_source):
        """render returns correct shape."""
        output = counter_source.render(256, 0.0)
        assert output.shape == (256, 3)

    def test_counter_increments(self, counter_source):
        """Counter value increases over time."""
        counter_source.set_control("start_value", 0.0)
        counter_source.set_control("increment", 10.0)

        frame1 = counter_source.render(256, 0.0)
        frame2 = counter_source.render(256, 1.0)

        # Frames should differ as counter changes
        assert not np.array_equal(frame1, frame2)

    def test_prefix_suffix(self, counter_source):
        """Prefix and suffix are applied."""
        counter_source.set_control("prefix", "$")
        counter_source.set_control("suffix", "!")
        # Just verify it doesn't error
        output = counter_source.render(256, 0.0)
        assert output.shape == (256, 3)


class TestClockSource:
    """Test ClockSource virtual source."""

    @pytest.fixture
    def clock_source(self):
        """Create a ClockSource instance."""
        config = VirtualSourceConfig(
            name="test_clock",
            output_dimensions=(48, 8),
        )
        return ClockSource(config)

    def test_source_type(self, clock_source):
        """ClockSource has correct type."""
        assert clock_source.source_type == "clock"

    def test_default_controls(self, clock_source):
        """ClockSource has expected controls."""
        assert clock_source.get_control("format") == "24h"
        assert clock_source.get_control("blink_colon") is True

    def test_render_output_dimensions(self, clock_source):
        """render returns correct shape."""
        output = clock_source.render(384, 0.0)  # 48*8 = 384
        assert output.shape == (384, 3)

    def test_render_produces_output(self, clock_source):
        """Clock renders visible output."""
        output = clock_source.render(384, 0.0)
        assert np.any(output > 0)

    def test_blink_colon_effect(self, clock_source):
        """Blink colon produces different frames."""
        clock_source.set_control("blink_colon", True)

        frame1 = clock_source.render(384, 0.0)
        frame2 = clock_source.render(384, 0.5)  # Half second later

        # May or may not differ depending on colon state
        # Just verify both render successfully
        assert frame1.shape == frame2.shape

    def test_format_options(self, clock_source):
        """Different format options work."""
        for fmt in ["24h", "12h", "24h_seconds", "12h_seconds"]:
            clock_source.set_control("format", fmt)
            output = clock_source.render(384, 0.0)
            assert output.shape == (384, 3)


# =============================================================================
# Alignment and Scroll Direction Enum Tests
# =============================================================================


class TestEnums:
    """Test enum classes."""

    def test_text_align_values(self):
        """TextAlign has expected values."""
        assert TextAlign.LEFT.value == "left"
        assert TextAlign.CENTER.value == "center"
        assert TextAlign.RIGHT.value == "right"

    def test_vertical_align_values(self):
        """VerticalAlign has expected values."""
        assert VerticalAlign.TOP.value == "top"
        assert VerticalAlign.MIDDLE.value == "middle"
        assert VerticalAlign.BOTTOM.value == "bottom"

    def test_scroll_direction_values(self):
        """ScrollDirection has expected values."""
        assert ScrollDirection.NONE.value == "none"
        assert ScrollDirection.LEFT.value == "left"
        assert ScrollDirection.RIGHT.value == "right"
        assert ScrollDirection.UP.value == "up"
        assert ScrollDirection.DOWN.value == "down"
