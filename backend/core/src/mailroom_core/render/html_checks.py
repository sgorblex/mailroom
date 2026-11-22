"""What the checks of designed mail (`mail.designed`) read in a message's HTML: its images and
their `cid:` references, and what `outgoing_html` would remove from it, by kind. The markup is
read as lxml reads it for both sanitisers, the whole document, head included."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import unquote

import lxml.etree
import lxml.html

from mailroom_core.render._html_policy import UNSAFE_CSS, content_id, parser

if TYPE_CHECKING:
    from collections.abc import Mapping

_SCRIPT_URL: Final = re.compile(r"[\x00-\x20]*(?:java|vb)script:", re.IGNORECASE)
_URL_ATTRIBUTES: Final = frozenset({"href", "src", "action", "formaction", "xlink:href"})
_FORM_TAGS: Final = frozenset({"form", "input", "button", "select", "textarea"})
_REMOTE_TAGS: Final = frozenset(
    {"link", "iframe", "frame", "object", "embed", "applet", "audio", "video", "source", "track"}
)
_KEPT_IMAGE: Final = re.compile(r"\s*(?:cid|data):", re.IGNORECASE)
_DATA_IMAGE: Final = re.compile(r"\s*data:", re.IGNORECASE)
_REMOTE_IMAGE: Final = re.compile(r"\s*(?:https?:)?//", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class HtmlImages:
    """The `<img>` elements of a message's HTML."""

    without_alt: int
    """Those with no `alt` attribute; an empty one marks an image as decoration, which is fine."""
    content_ids: frozenset[str]
    """The Content-IDs their `cid:` sources name, without angle brackets."""
    remote: int
    """Those that load from a server: an `http(s)` or protocol-relative source."""


def _document(html: str) -> lxml.html.HtmlElement | None:
    if not html.strip():
        return None
    try:
        return lxml.html.document_fromstring(html.encode("utf-8", "replace"), parser=parser())
    except lxml.etree.ParserError:
        return None


def _elements(html: str) -> list[lxml.html.HtmlElement]:
    document = _document(html)
    if document is None:
        return []
    return [element for element in document.iter() if isinstance(element.tag, str)]


def html_images(html: str) -> HtmlImages:
    """The images of `html`, as a check before sending counts them."""
    images = [element for element in _elements(html) if element.tag == "img"]
    sources = [image.get("src", "").strip() for image in images]
    return HtmlImages(
        without_alt=sum(image.get("alt") is None for image in images),
        content_ids=frozenset(
            content_id(unquote(src[4:])) for src in sources if src[:4].lower() == "cid:"
        ),
        remote=sum(_REMOTE_IMAGE.match(src) is not None for src in sources),
    )


def image_sources(html: str) -> tuple[str, ...]:
    """Every `<img>` source in `html`, as written."""
    return tuple(element.get("src", "") for element in _elements(html) if element.tag == "img")


def with_image_sources(html: str, sources: Mapping[str, str]) -> str:
    """`html` with each `<img>` source that is a key of `sources` replaced by its value: the whole
    document as lxml serialises it."""
    document = _document(html)
    if document is None:
        return html
    for element in document.iter("img"):
        replaced = sources.get(element.get("src", ""))
        if replaced is not None:
            element.set("src", replaced)
    return lxml.html.tostring(document, encoding="unicode")


def unsafe_parts(html: str) -> dict[str, int]:
    """What sanitising `html` to send removes that the sender may miss, by kind: `scripts`
    (elements and `javascript:` URLs), `forms` (their controls too), `handlers` (`on*`
    attributes), `images` that load from elsewhere (sources but `cid:`, `background`
    attributes), `data` images, other `remote` resources (linked style sheets, frames, objects,
    media, CSS `url()`), and `styles`, the style sheets. Kinds with none are left out."""
    found = dict.fromkeys(("scripts", "forms", "handlers", "images", "data", "remote", "styles"), 0)
    for element in _elements(html):
        tag = element.tag
        if tag == "script":
            found["scripts"] += 1
        elif tag == "style":
            found["styles"] += 1
        elif tag in _FORM_TAGS:
            found["forms"] += 1
        elif tag in _REMOTE_TAGS:
            found["remote"] += 1
        elif tag == "img":
            src = element.get("src", "")
            if _DATA_IMAGE.match(src):
                found["data"] += 1
            elif src.strip() and not _KEPT_IMAGE.match(src):
                found["images"] += 1
        for name, value in element.attrib.items():
            attribute = name.lower()
            if attribute.startswith("on"):
                found["handlers"] += 1
            elif attribute in _URL_ATTRIBUTES and _SCRIPT_URL.match(value):
                found["scripts"] += 1
            elif attribute == "background" and value.strip():
                found["images"] += 1
            elif attribute == "style" and UNSAFE_CSS.search(value):
                found["remote"] += 1
    return {kind: count for kind, count in found.items() if count}
