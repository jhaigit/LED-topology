"""Renderers for displaying LED data."""

from ltp_sink.renderers.base import Renderer, RendererConfig
from ltp_sink.renderers.terminal import TerminalRenderer, TerminalConfig

__all__ = [
    "Renderer",
    "RendererConfig",
    "TerminalRenderer",
    "TerminalConfig",
]

# Lazy imports for optional dependencies
try:
    from ltp_sink.renderers.gui import GuiRenderer, GuiConfig

    __all__ += ["GuiRenderer", "GuiConfig"]
except ImportError:
    pass
