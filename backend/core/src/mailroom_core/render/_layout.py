"""How wide a message's HTML is laid out: the widest fixed pixel width it declares, for a host
that fits a newsletter built for a desktop column to a phone's screen (`SafeHtml.layout_width`).

Read on lxml's tree before nh3 cleans, over what nh3 keeps alone: an allowed tag's `width`
attribute and the declarations `style_declarations` keeps. A `style` width replaces the
attribute, as in a browser, so a responsive table (`width="600" style="width:100%"`) is fluid,
not 600 pixels wide. A `max-width` in pixels limits it, and any other (`100%`) makes it fluid:
such a box shrinks with the screen already. So does a table's cell or column, whose width is
only a preference within its table's. A `min-width` counts whatever the rest. Images are left
out: the host shrinks them to their column (`max-width:100%`), whatever width they ask for.
Nothing in a subtree nh3 drops with its content, or one hidden with `display:none`, counts."""

import re
from typing import TYPE_CHECKING, Final

from mailroom_core.render._html_policy import DROPPED_WITH_CONTENT, TAGS, style_declarations

if TYPE_CHECKING:
    import lxml.html

MAX_LAYOUT_WIDTH: Final = 1600
"""Wider than this is no column a reader could read zoomed out to a phone (a spacer, a mistake):
such a width is not a layout, and is left to scroll."""
_ATTRIBUTE_PIXELS: Final = re.compile(r"\s*(\d{1,6}(?:\.\d+)?)\s*(?:px)?\s*", re.IGNORECASE)
_STYLE_PIXELS: Final = re.compile(r"(\d{1,6}(?:\.\d+)?)px\s*(?:!\s*important)?", re.IGNORECASE)
_UNLIMITED: Final = re.compile(r"(?:none|initial|unset|revert)\s*(?:!\s*important)?", re.IGNORECASE)
_CELLS: Final = frozenset({"col", "colgroup", "td", "th"})


def _pixels(pattern: re.Pattern[str], value: str | None) -> int:
    """`value` as whole pixels when `pattern` reads it as a pixel length, else 0."""
    found = pattern.fullmatch(value.strip()) if value is not None else None
    return int(float(found.group(1))) if found else 0


def _styles(element: lxml.html.HtmlElement) -> dict[str, str]:
    """The kept declarations of `element`'s style, by property; only those of a style that might
    set a width or `display`: most set neither, and parsing them all would slow the pass."""
    style = element.get("style")
    if style is None:
        return {}
    lowered = style.lower()
    if "width" not in lowered and "display" not in lowered:
        return {}
    pairs = (declaration.partition(":") for declaration in style_declarations(style))
    return {name: value for name, _, value in pairs}


def _fixed_width(element: lxml.html.HtmlElement) -> int | None:
    """The fixed pixel width `element` declares, 0 for none; None when it is hidden, and its
    subtree with it."""
    styles = _styles(element)
    if styles.get("display", "").lower().startswith("none"):
        return None
    # A cell the sender displays as something else (`display:block`) is laid out as that.
    if element.tag == "img" or (element.tag in _CELLS and "display" not in styles):
        width = 0
    elif "width" in styles:
        width = _pixels(_STYLE_PIXELS, styles["width"])
    else:
        width = _pixels(_ATTRIBUTE_PIXELS, element.get("width"))
    limit = styles.get("max-width")
    if limit is not None and not _UNLIMITED.fullmatch(limit):
        width = min(width, _pixels(_STYLE_PIXELS, limit))
    return max(width, _pixels(_STYLE_PIXELS, styles.get("min-width")))


def layout_width(body: lxml.html.HtmlElement) -> int | None:
    """The widest fixed pixel width `body`'s kept elements declare (module docstring), at most
    `MAX_LAYOUT_WIDTH` (a wider one does not count); None when there is none."""
    widest = 0
    stack = list(body)
    while stack:
        element = stack.pop()
        tag = element.tag
        if not isinstance(tag, str) or tag in DROPPED_WITH_CONTENT:
            continue
        # A tag nh3 strips keeps its content, but not its style: only a kept one hides it.
        if tag in TAGS:
            width = _fixed_width(element)
            if width is None:
                continue
            if width <= MAX_LAYOUT_WIDTH:
                widest = max(widest, width)
        stack.extend(element)
    return widest or None
