"""A message's HTML made safe to show (the Mailroom app's message view; no TypeScript original).

lxml normalises the markup first: it parses in linear time and bounds the nesting depth, which
html5ever (inside nh3) would take quadratic time over. nh3 (ammonia) then keeps only allowlisted
tags, attributes and CSS properties. Nothing in the result can run code, submit a form, frame
another page, or load anything except:

- the message's own raster images (`cid:` parts), inlined as `data:` URIs up to a byte budget;
- with `remote`, `http(s)` images, which load from their servers (and tell them the mail was
  opened). Without it they are removed, and `has_remote` says whether there were any, so the
  reader can choose to show them.

Styles keep layout and colour only: no `url()`, `expression()` or escapes in any declaration.
Links open a new top-level browsing context, without opener or referrer. The host still serves
the result under a restrictive CSP in a sandboxed frame: this is one layer, not the only one.
"""

import base64
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import unquote

import lxml.etree
import lxml.html
import nh3

if TYPE_CHECKING:
    from collections.abc import Mapping

_TAGS: Final = frozenset(
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

_ATTRIBUTES: Final = {
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

_DROPPED_WITH_CONTENT: Final = frozenset(
    {
        "form", "head", "iframe", "math", "noscript", "object", "script", "select", "style",
        "svg", "template", "textarea", "title",
    }
)  # fmt: skip

_CSS_PROPERTIES: Final = frozenset(
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
"""The image types inlined as `data:`. Not SVG, which is a document of its own."""

_UNSAFE_CSS: Final = re.compile(r"url\s*\(|expression\s*\(|\\", re.IGNORECASE)
_DATA_IMAGE: Final = re.compile(r"data:image/(?:gif|jpeg|png|webp)[;,]", re.IGNORECASE)
_REMOTE: Final = re.compile(r"https?://", re.IGNORECASE)
_LINK: Final = re.compile(r"(?:https?://|mailto:)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SafeHtml:
    """A sanitised HTML fragment, and whether the message had remote images."""

    html: str
    """The sanitised fragment, for the body of a document the host builds."""
    has_remote: bool
    """The message had remote images: removed unless `remote`, loaded if it was set."""


def _content_id(value: str) -> str:
    return value.strip().removeprefix("<").removesuffix(">").strip()


class _Filter:
    """nh3's `attribute_filter`: rewrites or drops one attribute value."""

    def __init__(
        self, inline_images: Mapping[str, tuple[str, bytes]], remote: bool, max_inline_bytes: int
    ) -> None:
        self.images = {_content_id(cid): part for cid, part in inline_images.items()}
        self.remote = remote
        self.budget = max_inline_bytes
        self.inlined: dict[str, str] = {}
        self.has_remote = False

    def __call__(self, tag: str, attribute: str, value: str) -> str | None:
        if attribute == "style":
            kept = [decl for decl in value.split(";") if not _UNSAFE_CSS.search(decl)]
            return ";".join(kept) or None
        if tag == "img" and attribute == "src":
            return self._image(_url(value))
        if attribute == "href":
            # Absolute web and mail links only: no cid:, data:, or a path on the host's origin.
            url = _url(value)
            return url if _LINK.match(url) else None
        return value

    def _image(self, src: str) -> str | None:
        if src[:4].lower() == "cid:":
            return self._inline(_content_id(unquote(src[4:])))
        if _DATA_IMAGE.match(src):
            return src
        if _REMOTE.match(src):
            self.has_remote = True
            return src if self.remote else None
        return None

    def _inline(self, cid: str) -> str | None:
        """The part as a `data:` URI. Every reference is charged against the budget, the same
        part's too, since each one is a copy in the output: the output stays bounded."""
        part = self.images.get(cid)
        if part is None:
            return None
        content_type, data = part
        media = content_type.split(";")[0].strip().lower()
        if media not in RASTER_TYPES or len(data) > self.budget:
            return None
        self.budget -= len(data)
        uri = self.inlined.get(cid)
        if uri is None:
            uri = f"data:{media};base64,{base64.b64encode(data).decode('ascii')}"
            self.inlined[cid] = uri
        return uri


_C0_OR_SPACE: Final = "".join(map(chr, range(0x21)))
_TAB_OR_NEWLINE: Final = str.maketrans(dict.fromkeys("\t\n\r"))


def _url(value: str) -> str:
    """`value` as a browser's URL parser reads it: leading and trailing C0 controls and spaces
    stripped, tabs and newlines removed. Not Python's `strip()`, which also removes Unicode
    spaces such as U+00A0 that a browser keeps, so a URL would pass as absolute here and
    resolve as a relative one there."""
    return value.strip(_C0_OR_SPACE).translate(_TAB_OR_NEWLINE)


def _normalised(html: str) -> str:
    """The body as lxml serialises it: well-formed, at most libxml2's depth (2048 with
    `huge_tree`), no comments or processing instructions."""
    parser = lxml.html.HTMLParser(
        encoding="utf-8", huge_tree=True, no_network=True, remove_comments=True, remove_pis=True
    )
    try:
        document = lxml.html.document_fromstring(html.encode("utf-8", "replace"), parser=parser)
    except lxml.etree.ParserError:
        return ""
    body = document.find("body")
    return "" if body is None else lxml.html.tostring(body, encoding="unicode")


def safe_html(
    html: str,
    *,
    inline_images: Mapping[str, tuple[str, bytes]],
    remote: bool,
    max_inline_bytes: int,
) -> SafeHtml:
    """`html` sanitised for display. `inline_images` maps a Content-ID (angle brackets optional)
    to the part's content type and bytes; images past `max_inline_bytes` in total, or not raster,
    are dropped. With `remote`, `http(s)` images stay. The module docstring has the rules.

    There is no cap on the size of `html`: the caller bounds it, and calls this from a worker
    thread, since a large body takes a while to parse and clean."""
    attribute_filter = _Filter(inline_images, remote, max_inline_bytes)
    cleaned = nh3.clean(
        _normalised(html),
        tags=set(_TAGS),
        clean_content_tags=set(_DROPPED_WITH_CONTENT),
        attributes={tag: set(names) for tag, names in _ATTRIBUTES.items()},
        attribute_filter=attribute_filter,
        strip_comments=True,
        link_rel="noopener noreferrer nofollow",
        # cid: and data: pass the scheme check so the filter can inline or drop them.
        url_schemes={"cid", "data", "http", "https", "mailto"},
        set_tag_attribute_values={"a": {"target": "_blank"}},
        filter_style_properties=set(_CSS_PROPERTIES),
    )
    return SafeHtml(cleaned, attribute_filter.has_remote)
