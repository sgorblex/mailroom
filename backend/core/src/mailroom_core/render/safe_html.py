"""A message's HTML made safe to show (the Mailroom app's message view; no TypeScript original).

lxml normalises the markup first: it parses in linear time and bounds the nesting depth, which
html5ever (inside nh3) would take quadratic time over. nh3 (ammonia) then keeps only allowlisted
tags, attributes and CSS properties. Nothing in the result can run code, submit a form, frame
another page, or load anything except:

- the message's own raster images (`cid:` parts), inlined as `data:` URIs up to a byte budget;
- with `remote`, `http(s)` images, which load from their servers (and tell them the mail was
  opened). Without it they are removed. `remote_images` counts every remote image the markup
  asks for, `srcset` candidates, `background` attributes and CSS `url()`s included (those are
  removed either way), so the reader sees how many there were; `loadable_remote_images` counts
  the `<img>` sources alone, the ones `remote` shows.

Styles keep layout and colour only: no `url()`, `expression()` or escapes in any declaration.
Links open a new top-level browsing context, without opener or referrer. Each link's `title` is
the host it goes to (IDNA form), never the sender's, and a link whose text names another host
(`render.hosts`) is followed by a marker naming the real one. The host still serves the result
under a restrictive CSP in a sandboxed frame: this is one layer, not the only one.
"""

import base64
import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import unquote

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
)
from mailroom_core.render.hosts import (
    browser_url,
    element_text,
    link_host,
    link_mark,
    names_other_host,
    web_url,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_DATA_IMAGE: Final = re.compile(r"data:image/(?:gif|jpeg|png|webp)[;,]", re.IGNORECASE)
_REMOTE: Final = re.compile(r"https?://", re.IGNORECASE)
_REMOTE_CANDIDATE: Final = re.compile(r"(?:^|[\s,])https?://", re.IGNORECASE)
_REMOTE_CSS_URL: Final = re.compile(r"url\s*\(\s*['\"]?\s*https?:", re.IGNORECASE)
_MARK_STYLE: Final = (
    "display:inline;margin-left:4px;padding:0 4px;border:1px solid #b00020;"
    "background-color:#fff0f0;color:#b00020;font-size:12px;font-style:normal;"
    "font-weight:bold;text-decoration:none;letter-spacing:normal;word-spacing:normal;"
    "font-family:sans-serif;font-variant:normal;text-transform:none;line-height:normal;"
    "white-space:nowrap;vertical-align:baseline;direction:ltr"
)
"""The link marker's look, set in full so that a style the sender put around it cannot hide it."""


@dataclass(frozen=True, slots=True)
class SafeHtml:
    """A sanitised HTML fragment, and how many remote images the message asked for."""

    html: str
    """The sanitised fragment, for the body of a document the host builds."""
    remote_images: int
    """Remote images the markup asked for (module docstring): `<img>` sources are removed unless
    `remote`, loaded if it was set; the others are removed either way."""
    loadable_remote_images: int
    """Of those, the `<img>` sources: what `remote` would show."""


class _Filter:
    """nh3's `attribute_filter`: rewrites or drops one attribute value."""

    def __init__(
        self, inline_images: Mapping[str, tuple[str, bytes]], remote: bool, max_inline_bytes: int
    ) -> None:
        self.images = {content_id(cid): part for cid, part in inline_images.items()}
        self.remote = remote
        self.budget = max_inline_bytes
        self.inlined: dict[str, str] = {}
        self.title_prefix = f"\ue000{secrets.token_hex(8)}\ue000"
        """What `_prepare` puts before a link title it wrote: any other title on a link is the
        sender's, through a tree that nh3 reads differently from lxml, and is dropped."""

    def __call__(self, tag: str, attribute: str, value: str) -> str | None:
        if tag == "a" and attribute == "title":
            prefix = self.title_prefix
            return value.removeprefix(prefix) if value.startswith(prefix) else None
        if attribute == "style":
            kept = [decl for decl in value.split(";") if not UNSAFE_CSS.search(decl)]
            return ";".join(kept) or None
        if tag == "img" and attribute == "src":
            return self._image(browser_url(value))
        if attribute == "href":
            # Absolute web and mail links only: no cid:, data:, or a path on the host's origin.
            url = browser_url(value)
            return url if LINK.match(url) else None
        return value

    def _image(self, src: str) -> str | None:
        if src[:4].lower() == "cid:":
            return self._inline(content_id(unquote(src[4:])))
        if _DATA_IMAGE.match(src):
            return src
        if _REMOTE.match(src):
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


def _remote_source(element: lxml.html.HtmlElement) -> bool:
    """The element is an `<img>` whose source is remote: the one kind `remote` shows."""
    src = element.get("src")
    return element.tag == "img" and src is not None and _REMOTE.match(browser_url(src)) is not None


def _remote_images(element: lxml.html.HtmlElement) -> int:
    """The remote images one element asks for: its `<img>` source, `srcset` candidates,
    `background` and CSS `url()`s, a `<style>` element's included."""
    count = int(_remote_source(element))
    background = element.get("background")
    if background is not None and _REMOTE.match(browser_url(background)):
        count += 1
    count += len(_REMOTE_CANDIDATE.findall(element.get("srcset") or ""))
    count += len(_REMOTE_CSS_URL.findall(element.get("style") or ""))
    if element.tag == "style":
        count += len(_REMOTE_CSS_URL.findall(element.text or ""))
    return count


def _mark_link(link: lxml.html.HtmlElement, title_prefix: str) -> None:
    """The sender's `title` replaced by the host the link goes to, and the marker after a link
    whose text names another host."""
    link.attrib.pop("title", None)
    href = link.get("href")
    if href is None:
        return
    url = web_url(browser_url(href))
    if not LINK.match(url):
        return
    link.set("href", url)
    host = link_host(url)
    if host is not None:
        link.set("title", title_prefix + host)
    if names_other_host(element_text(link), host):
        mark = lxml.html.Element("span", style=_MARK_STYLE, dir="ltr")
        mark.text = link_mark(host)
        mark.tail, link.tail = link.tail, None
        link.addnext(mark)


def _prepare(body: lxml.html.HtmlElement, title_prefix: str) -> tuple[int, int]:
    """What nh3's attribute filter cannot do, since it sees one value at a time: links marked
    (`_mark_link`) and remote images counted. `<script>` and `<style>` go here, not in nh3: lxml
    writes their content out raw, which nh3 might read as markup lxml never saw. Returns the
    count of remote images, and of the loadable ones."""
    count = loadable = 0
    for element in list(body.iter()):
        if not isinstance(element.tag, str):
            continue
        count += _remote_images(element)
        loadable += _remote_source(element)
        if element.tag == "a":
            _mark_link(element, title_prefix)
        elif element.tag in ("script", "style"):
            element.drop_tree()
    return count, loadable


def _normalised(html: str, title_prefix: str) -> tuple[str, tuple[int, int]]:
    """The body as lxml serialises it once `_prepare`d: well-formed, at most libxml2's depth
    (2048 with `huge_tree`), no comments or processing instructions; and its remote images, all
    and loadable."""
    body = parsed_body(html)
    if body is None:
        return "", (0, 0)
    counts = _prepare(body, title_prefix)
    return lxml.html.tostring(body, encoding="unicode"), counts


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
    normalised, (remote_images, loadable) = _normalised(html, attribute_filter.title_prefix)
    cleaned = nh3.clean(
        normalised,
        tags=set(TAGS),
        clean_content_tags=set(DROPPED_WITH_CONTENT),
        attributes={tag: set(names) for tag, names in ATTRIBUTES.items()},
        attribute_filter=attribute_filter,
        strip_comments=True,
        link_rel="noopener noreferrer nofollow",
        # cid: and data: pass the scheme check so the filter can inline or drop them.
        url_schemes={"cid", "data", "http", "https", "mailto"},
        set_tag_attribute_values={"a": {"target": "_blank"}},
        filter_style_properties=set(CSS_PROPERTIES),
    )
    return SafeHtml(cleaned, remote_images, loadable)
