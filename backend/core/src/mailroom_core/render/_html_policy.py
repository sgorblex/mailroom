"""The allowlists and the lxml pass both HTML sanitisers share: `safe_html` (to show) and
`outgoing_html` (to send). A change here changes what both let through."""

import re
from typing import TYPE_CHECKING, Final

import lxml.etree
import lxml.html

if TYPE_CHECKING:
    from collections.abc import Iterator

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
_STRUCTURE: Final = re.compile(r"[\"'()\[\]{}]|/\*")
"""What can hide a semicolon or leave a value malformed: quotes, brackets, comments."""
_FUNCTION_NAME: Final = re.compile(r"-*[a-z_][\w-]*", re.IGNORECASE)


def _declarations(style: str) -> Iterator[str]:
    """`style` split at the semicolons outside quotes and parentheses, each comment a space."""
    if not _STRUCTURE.search(style):  # most styles: the same answer, without the slow walk
        yield from style.split(";")
        return
    start = depth = 0
    quote = ""
    parts: list[str] = []
    index = 0
    while index < len(style):
        char = style[index]
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif style.startswith("/*", index):
            parts.append(f"{style[start:index]} ")
            end = style.find("*/", index + 2)
            index = len(style) if end < 0 else end + 2
            start = index
            continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        elif char == ";" and depth == 0:
            parts.append(style[start:index])
            yield "".join(parts)
            parts = []
            start = index + 1
        index += 1
    parts.append(style[start:])
    yield "".join(parts)


def _well_formed(value: str) -> bool:
    """A declaration's value is one nh3 writes out again as it reads it: its strings closed on
    their line, its parentheses balanced and each one a function's (`rgb(`, `calc(`, a name
    starting the value or after a space, comma or slash), not inside another; no other block
    (`[`, `{`). nh3 mangles a bare block or a nested function, and drops a broken string,
    differently on a second pass."""
    if not _STRUCTURE.search(value):
        return True
    depth = 0
    quote = ""
    word = 0  # where the run of name characters up to this one starts
    for index, char in enumerate(value):
        if quote:
            if char in "\n\r\f":
                return False
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "(":
            if depth > 0 or not (
                _FUNCTION_NAME.fullmatch(value, word, index)
                and (word == 0 or value[word - 1].isspace() or value[word - 1] in ",/")
            ):
                return False
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
        elif char in "[]{}":
            return False
        if not (char.isalnum() or char in "-_"):
            word = index + 1
    return depth == 0 and not quote


def style_declarations(style: str) -> list[str]:
    """The declarations of a `style` attribute that both sanitisers keep, each `name:value`:
    an allowed property (`CSS_PROPERTIES`, in any case) with a well-formed value, and nothing
    `UNSAFE_CSS` matches. Keeping only those, rather than leaving nh3 to drop the rest, means a
    style with none left goes as a whole, not as an empty `style=""` that a second clean would
    drop: cleaning twice gives what cleaning once did."""
    kept = []
    for declaration in _declarations(style):
        name, colon, value = declaration.partition(":")
        name, value = name.strip().lower(), value.strip()
        if (
            colon
            and value
            and name in CSS_PROPERTIES
            and not UNSAFE_CSS.search(value)
            and _well_formed(value)
        ):
            kept.append(f"{name}:{value}")
    return kept


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
