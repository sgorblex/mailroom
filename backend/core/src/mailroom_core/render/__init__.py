"""Mail rendered for a reader: bodies and headers as compact text for the model, the
untrusted-content envelope, and sanitised HTML for the Mailroom app's message view."""

from mailroom_core.render.body import RenderedBody, render_body
from mailroom_core.render.envelope import untrusted
from mailroom_core.render.html import MAX_DEPTH, MAX_HTML_CHARS, HtmlText, html_to_text
from mailroom_core.render.message import (
    Address,
    DateValue,
    HeaderView,
    ListItem,
    PartInfo,
    format_date,
    format_size,
    header_block,
    list_line,
)
from mailroom_core.render.quotes import (
    CUT_MARK,
    MARKER_LINE,
    QUOTE_END,
    QUOTE_START,
    Collapsed,
    Marker,
    Omitted,
    collapse_quotes,
    default_marker,
)
from mailroom_core.render.safe_html import SafeHtml, safe_html
from mailroom_core.render.sanitize import CleanText, clean_header, clean_text, safe_cut

__all__ = [
    "CUT_MARK",
    "MARKER_LINE",
    "MAX_DEPTH",
    "MAX_HTML_CHARS",
    "QUOTE_END",
    "QUOTE_START",
    "Address",
    "CleanText",
    "Collapsed",
    "DateValue",
    "HeaderView",
    "HtmlText",
    "ListItem",
    "Marker",
    "Omitted",
    "PartInfo",
    "RenderedBody",
    "SafeHtml",
    "clean_header",
    "clean_text",
    "collapse_quotes",
    "default_marker",
    "format_date",
    "format_size",
    "header_block",
    "html_to_text",
    "list_line",
    "render_body",
    "safe_cut",
    "safe_html",
    "untrusted",
]
