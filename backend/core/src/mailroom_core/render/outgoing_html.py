"""HTML the owner writes, made safe to send (the web composer's rich text, a quoted original).

lxml normalises the markup as for `safe_html`, then nh3 keeps the same tags, attributes and CSS
properties, minus anything that reaches out: links only `http(s)` and `mailto`, images only `cid:`
parts the message carries. A raster `data:` image becomes such a part, under a Content-ID made up
here; any other image is left out, element and all. No script, event handler, form, frame, object
or style sheet survives; styles lose `url()`, `expression()` and escapes. A `div` keeps
`SIGNATURE_MARK`, which marks the owner's signature.
"""

import base64
import binascii
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import unquote

import lxml.etree
import lxml.html
import nh3

from mailroom_core.render._html_policy import (
    ATTRIBUTES,
    CSS_PROPERTIES,
    DROPPED_WITH_CONTENT,
    LINK,
    RASTER_TYPES,
    TAGS,
    UNSAFE_CSS,
    content_id,
    parsed_body,
    parser,
)
from mailroom_core.render.hosts import browser_url, web_url

if TYPE_CHECKING:
    from collections.abc import Iterable

SIGNATURE_MARK: Final = "data-mailroom-signature"
"""The attribute of the `div` that holds the signature in a message Mailroom writes, so a client
can tell it from the rest of the body."""
_DATA_IMAGE: Final = re.compile(r"data:(image/[a-z]+);base64,(.*)", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True, slots=True)
class InlineImage:
    """A `data:` image taken out of the markup, to go as a part of its own."""

    content_id: str
    content_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class OutgoingHtml:
    """Sanitised HTML to send, and what its `cid:` images refer to."""

    html: str
    content_ids: frozenset[str]
    """The Content-IDs its images refer to, those of `images` included: parts it does not refer
    to have nothing to show."""
    images: tuple[InlineImage, ...]
    """The `data:` images it held, in order, each now referred to by `cid:`."""


class _Filter:
    """nh3's `attribute_filter`: rewrites or drops one attribute value."""

    def __init__(self, content_ids: Iterable[str], data_images: bool) -> None:
        self.known = frozenset(content_id(cid) for cid in content_ids)
        self.data_images = data_images
        self.images: dict[tuple[str, bytes], InlineImage] = {}

    def __call__(self, tag: str, attribute: str, value: str) -> str | None:
        if attribute == SIGNATURE_MARK:
            return ""
        if attribute == "style":
            kept = [decl for decl in value.split(";") if not UNSAFE_CSS.search(decl)]
            return ";".join(kept) or None
        if tag == "img" and attribute == "src":
            return self._image(browser_url(value))
        if attribute == "href":
            url = web_url(browser_url(value))
            return url if LINK.match(url) else None
        return value

    def _image(self, src: str) -> str | None:
        if src[:4].lower() == "cid:":
            cid = content_id(unquote(src[4:]))
            return f"cid:{cid}" if cid in self.known else None
        match = _DATA_IMAGE.fullmatch(src) if self.data_images else None
        if match is None or match.group(1).lower() not in RASTER_TYPES:
            return None
        try:
            data = base64.b64decode(match.group(2))
        except binascii.Error, ValueError:
            return None
        if not data:
            return None
        key = (match.group(1).lower(), data)
        image = self.images.get(key)
        if image is None:
            image = InlineImage(f"{uuid.uuid4().hex}@mailroom.invalid", key[0], data)
            self.images[key] = image
        return f"cid:{image.content_id}"


def _normalised(html: str, attribute_filter: _Filter) -> str:
    """The body as lxml serialises it, without `<script>` or `<style>`, whose raw content nh3
    might read as markup lxml never saw (`safe_html`'s reason too), and without the images whose
    source would not stay: a mail client shows an `<img>` with none as a broken image."""
    body = parsed_body(html)
    if body is None:
        return ""
    for element in list(body.iter("script", "style")):
        element.drop_tree()
    for image in list(body.iter("img")):
        if attribute_filter("img", "src", image.get("src", "")) is None:
            image.drop_tree()
    return lxml.html.tostring(body, encoding="unicode")


def _referred(cleaned: str) -> frozenset[str]:
    """The Content-IDs the cleaned markup's images refer to, read as a reader's parser would."""
    try:
        fragment = lxml.html.fragment_fromstring(
            cleaned.encode("utf-8"), create_parent="div", parser=parser()
        )
    except lxml.etree.ParserError:
        return frozenset()
    sources = (image.get("src", "") for image in fragment.iter("img"))
    return frozenset(src[4:] for src in sources if src.startswith("cid:"))


def outgoing_html(
    html: str,
    *,
    content_ids: Iterable[str] = (),
    data_images: bool = True,
    signature_mark: bool = True,
) -> OutgoingHtml:
    """`html` sanitised to send. An image stays only as `cid:` one of `content_ids` (angle
    brackets optional), or, with `data_images`, as a raster `data:` image, which becomes one of
    `images`; any other is removed. Without `signature_mark` a signature's mark goes too, as
    from a message quoted or forwarded, whose signature is not the owner's. Called from a worker
    thread, as `safe_html` is."""
    attribute_filter = _Filter(content_ids, data_images)
    attributes = {tag: set(names) for tag, names in ATTRIBUTES.items()}
    if signature_mark:
        attributes["div"] = {*attributes.get("div", ()), SIGNATURE_MARK}
    cleaned = nh3.clean(
        _normalised(html, attribute_filter),
        tags=set(TAGS),
        clean_content_tags=set(DROPPED_WITH_CONTENT),
        attributes=attributes,
        attribute_filter=attribute_filter,
        strip_comments=True,
        link_rel=None,
        # cid: and data: pass the scheme check so the filter can keep or convert them.
        url_schemes={"cid", "data", "http", "https", "mailto"},
        filter_style_properties=set(CSS_PROPERTIES),
    )
    referred = _referred(cleaned)
    images = tuple(i for i in attribute_filter.images.values() if i.content_id in referred)
    return OutgoingHtml(cleaned, referred, images)
