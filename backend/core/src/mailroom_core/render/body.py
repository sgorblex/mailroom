"""The body part as the text the model reads (`src/render/body.ts`)."""

import re
from dataclasses import dataclass
from typing import Final, Literal

from mailroom_core.render._js import Utf16Index, trim, trim_end
from mailroom_core.render.html import html_to_text
from mailroom_core.render.quotes import CUT_MARK, Collapsed, Marker, collapse_quotes, default_marker
from mailroom_core.render.sanitize import clean_text

_BLANK_RUNS: Final = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class RenderedBody:
    """A window of the rendered body text, with what was removed or collapsed."""

    text: str
    removed_chars: int
    """Invisible characters removed from the whole body."""
    total_chars: int
    """Length of the whole rendered text in UTF-16 units, so the model knows how much there is."""
    next_offset: int | None
    """Set when more follows: pass it as `offset` to continue."""
    incomplete: bool
    """Part of the HTML was too large or too deeply nested to render, and is missing."""
    quoted_lines: int = 0
    """Lines of quoted text collapsed in the whole body (not only this window); 0 with
    `include_quoted`."""
    signature_lines: int = 0
    """Lines of a trailing signature collapsed, when nothing else was."""


def render_body(
    content: str,
    format: Literal["text", "html"],  # noqa: A002 -- the TypeScript's name, as in the tools
    *,
    include_quoted: bool,
    offset: int,
    max_chars: int,
    marker: Marker | None = default_marker,
) -> RenderedBody:
    """The decoded body part to the text the model reads: HTML converted, quotes collapsed unless
    asked for, invisible characters removed, blank runs squeezed, then one window of `max_chars`
    cut on a line boundary. Offsets and lengths are UTF-16 units of this rendered text, as the
    TypeScript's are, so they hold across calls with the same `include_quoted` and `marker`.

    `marker` writes the lines that stand for collapsed text (`collapse_quotes`): the default
    names the TypeScript tool's parameter, a host passes its own wording, or None for no line
    and only the counts in the result.

    The HTML conversion costs about a second per megabyte: call it from a worker thread."""
    html = html_to_text(content, not include_quoted) if format == "html" else None
    as_text = content if html is None else html.text.replace(CUT_MARK, "…")
    collapsed = (
        Collapsed(as_text, 0, 0) if include_quoted else collapse_quotes(as_text, marker=marker)
    )
    cleaned = clean_text(collapsed.text)
    lines = "\n".join(trim_end(line) for line in cleaned.text.split("\n"))
    full = trim(_BLANK_RUNS.sub("\n\n", lines))

    index = Utf16Index(full)
    start = index.safe_cut(min(max(0, offset), index.length))
    end = index.safe_cut(min(start + max_chars, index.length))
    if end < index.length:
        # Prefer ending on a line break, unless that would give back less than half a window.
        line_end = index.last_index_of("\n", end)
        if line_end > start + max_chars / 2:
            end = line_end + 1
    return RenderedBody(
        text=trim_end(index.slice(start, end)),
        removed_chars=cleaned.removed,
        total_chars=index.length,
        next_offset=end if end < index.length else None,
        incomplete=html is not None and html.cut,
        quoted_lines=collapsed.quoted_lines,
        signature_lines=collapsed.signature_lines,
    )
