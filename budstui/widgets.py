"""Width-aware panel widgets.

Each panel is driven by a *builder* -- a callable taking the width the widget
has to fill and returning the lines to draw. Textual passes the real width in
during layout, so the content resizes with the terminal for free.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.text import Text
from textual.geometry import Size
from textual.widget import Widget

Builder = Callable[[int], list[Text]]

FALLBACK_WIDTH = 40


class Panel(Widget):
    """A borderless block of generated lines."""

    DEFAULT_CSS = """
    Panel {
        width: 100%;
        height: auto;
    }
    """

    def __init__(self, builder: Builder, **kwargs) -> None:
        super().__init__(**kwargs)
        self.builder = builder

    def _lines(self, width: int) -> list[Text]:
        return self.builder(max(width, 1)) or [Text()]

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return len(self._lines(width))

    def render(self) -> Text:
        width = self.content_size.width or FALLBACK_WIDTH
        return Text("\n").join(self._lines(width))


class GroupBox(Panel):
    """A titled, bordered group. Gains an accent border while it holds the cursor."""

    DEFAULT_CSS = """
    GroupBox {
        border: round #444444;
        border-title-color: #6e6e6e;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
    }
    GroupBox.-active {
        border: round #dddddd;
        border-title-color: #dddddd;
        border-title-style: bold;
    }
    """

    def __init__(self, title: str, builder: Builder, **kwargs) -> None:
        super().__init__(builder, **kwargs)
        self.border_title = title

    def set_active(self, active: bool) -> None:
        self.set_class(active, "-active")
