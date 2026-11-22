"""The allowlists and the lxml pass both HTML sanitisers share: `safe_html` (to show) and
`outgoing_html` (to send). A change here changes what both let through."""

import re
from typing import Final

import lxml.etree
import lxml.html

TAGS: Final = frozenset(
    {
        "a", "abbr", "address", "article", "aside", "b", "bdi", "bdo", "big", "blockquote",
        "br", "caption", "center", "cite", "code", "col", "colgroup", "dd", "del", "details",
        "dfn", "div", "dl", "dt", "em", "figcaption", "figure", "font", "footer", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "i", "img", "ins", "kbd", "li", "main", "mark",
        "nav", "ol", "p", "pre", "q", "s", "samp", "section", "small", "span", "strike",
        "strong", "sub", "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead",
        "time", "tr", "tt", "u", "ul", "var", "wbr",
    }
)  # fmt: skip

ATTRIBUTES: Final = {
    "*": {"align", "bgcolor", "border", "color", "dir", "height", "lang", "style", "title",
          "valign", "width"},
    "a": {"href"},
    "blockquote": {"type"},
    "col": {"span"},
    "colgroup": {"span"},
    "font": {"face", "size"},
    "img": {"alt", "src"},
    "ol": {"start", "type", "reversed"},
    "table": {"cellpadding", "cellspacing"},
    "td": {"colspan", "nowrap", "rowspan"},
    "th": {"colspan", "nowrap", "rowspan", "scope"},
}  # fmt: skip

DROPPED_WITH_CONTENT: Final = frozenset(
    {
        "form", "head", "iframe", "math", "noscript", "object", "script", "select", "style",
        "svg", "template", "textarea", "title",
    }
)  # fmt: skip

CSS_PROPERTIES: Final = frozenset(
    {
        "background-color", "border", "border-bottom", "border-collapse", "border-color",
        "border-left", "border-radius", "border-right", "border-spacing", "border-style",
        "border-top", "border-width", "color", "direction", "display", "font", "font-family",
        "font-size", "font-style", "font-variant", "font-weight", "height", "letter-spacing",
        "line-height", "list-style-type", "margin", "margin-bottom", "margin-left",
        "margin-right", "margin-top", "max-height", "max-width", "min-height", "min-width",
        "overflow-wrap", "padding", "padding-bottom", "padding-left", "padding-right",
        "padding-top", "table-layout", "text-align", "text-decoration", "text-indent",
        "text-transform", "vertical-align", "white-space", "width", "word-break", "word-spacing",
    }
)  # fmt: skip

RASTER_TYPES: Final = frozenset({"image/gif", "image/jpeg", "image/png", "image/webp"})
"""The image types shown in place. Not SVG, which is a document of its own."""

UNSAFE_CSS: Final = re.compile(r"url\s*\(|expression\s*\(|\\", re.IGNORECASE)
LINK: Final = re.compile(r"(?:https?://|mailto:)", re.IGNORECASE)


def content_id(value: str) -> str:
    """A Content-ID without its angle brackets."""
    return value.strip().removeprefix("<").removesuffix(">").strip()


def parser() -> lxml.html.HTMLParser:
    """lxml's parser for mail: linear time, at most libxml2's depth (2048 with `huge_tree`), no
    network, comments or processing instructions."""
    return lxml.html.HTMLParser(
        encoding="utf-8", huge_tree=True, no_network=True, remove_comments=True, remove_pis=True
    )


def parsed_body(html: str) -> lxml.html.HtmlElement | None:
    """The document's `<body>` as lxml reads it, None when there is none. Its `<script>` and
    `<style>` elements are for the caller to drop before serialising: lxml writes their content
    out raw, which nh3 might read as markup lxml never saw."""
    try:
        document = lxml.html.document_fromstring(html.encode("utf-8", "replace"), parser=parser())
    except lxml.etree.ParserError:
        return None
    return document.find("body")
