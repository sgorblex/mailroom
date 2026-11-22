"""Mail rendered for a reader: bodies and headers as compact text for the model, the
untrusted-content envelope, sanitised HTML for the Mailroom app's message view, the owner's HTML
sanitised to send, and what designed mail's checks read in HTML."""

from mailroom_core.render.body import RenderedBody, render_body
from mailroom_core.render.envelope import untrusted
from mailroom_core.render.hosts import ascii_host
from mailroom_core.render.html import MAX_DEPTH, MAX_HTML_CHARS, HtmlText, html_to_text
from mailroom_core.render.html_checks import (
    HtmlImages,
    html_images,
    image_sources,
    unsafe_parts,
    with_image_sources,
)
from mailroom_core.render.message import (
    Address,
    DateValue,
    HeaderView,
    ListItem,
    PartInfo,
    format_addresses,
    format_date,
    format_size,
    header_block,
    list_line,
)
from mailroom_core.render.outgoing_html import (
    SIGNATURE_MARK,
    InlineImage,
    OutgoingHtml,
    outgoing_html,
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
from mailroom_core.render.sender import sender_mismatch

__all__ = [
    "CUT_MARK",
    "MARKER_LINE",
    "MAX_DEPTH",
    "MAX_HTML_CHARS",
    "QUOTE_END",
    "QUOTE_START",
    "SIGNATURE_MARK",
    "Address",
    "CleanText",
    "Collapsed",
    "DateValue",
    "HeaderView",
    "HtmlImages",
    "HtmlText",
    "InlineImage",
    "ListItem",
    "Marker",
    "Omitted",
    "OutgoingHtml",
    "PartInfo",
    "RenderedBody",
    "SafeHtml",
    "ascii_host",
    "clean_header",
    "clean_text",
    "collapse_quotes",
    "default_marker",
    "format_addresses",
    "format_date",
    "format_size",
    "header_block",
    "html_images",
    "html_to_text",
    "image_sources",
    "list_line",
    "outgoing_html",
    "render_body",
    "safe_cut",
    "safe_html",
    "sender_mismatch",
    "unsafe_parts",
    "untrusted",
    "with_image_sources",
]
