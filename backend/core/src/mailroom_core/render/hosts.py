"""Host names in text a sender wrote, set against the host a link or an address really names.

Hosts are compared in the IDNA form a browser computes (UTS #46, non-transitional: sharp s, final
sigma and joiners are kept, not folded), after NFKC, and a host is related to another when it is the
same or a parent or child of it (`paypal.com`, `www.paypal.com`), with no public-suffix list:
`example.com` and `evil.example.com` are related. A dotted word reads as a host ("report.pdf"), a
false warning; text hidden by the sender's styles is read too, so visible text can be split by
hidden characters into something that names no host. The real host is shown in either case: these
checks only add a warning."""

import re
import unicodedata
from typing import TYPE_CHECKING, Final
from urllib.parse import unquote, urlsplit

import idna

from mailroom_core.render.sanitize import clean_header

if TYPE_CHECKING:
    from lxml.etree import _Element

MAX_TEXT: Final = 320
"""Of the text looked at, in UTF-16 units: a longer one is cut there first."""
MARK: Final = "⚠"
"""Before the real host, where a link's text names another one."""

_LABEL: Final = r"[^\W_](?:[\w-]{0,61}[^\W_])?"
_TOP: Final = r"(?:[^\W\d_]{2,63}|xn--[\w-]+)"
DOMAIN_LIKE: Final = re.compile(rf"(?<![\w.-])(?:{_LABEL}\.)+{_TOP}(?![\w-])")
_C0_OR_SPACE: Final = "".join(map(chr, range(0x21)))
_TAB_OR_NEWLINE: Final = str.maketrans(dict.fromkeys("\t\n\r"))
_WEB: Final = re.compile(r"https?:", re.IGNORECASE)
_MAILTO: Final = re.compile(r"mailto:", re.IGNORECASE)
_HOST: Final = re.compile(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*|[0-9a-f:.]*:[0-9a-f:.]*")
"""A host name in IDNA form (a browser takes `_`), or an IPv6 literal without its brackets."""


def ascii_host(host: str) -> str | None:
    """A host's IDNA form (`xn--…`) as a browser computes it, so that a name in Unicode matches
    a punycode one; None when it is no host a browser would visit (a space, a `>`, a joiner out
    of place)."""
    host = host.strip(".")
    if not host.isascii():
        try:
            host = idna.encode(host, uts46=True, transitional=False).decode("ascii")
        except idna.IDNAError, UnicodeError:
            return None
    host = host.lower()
    return host if _HOST.fullmatch(host) else None


def related(named: str, real: str) -> bool:
    """`named` is `real`, or a parent or child of it: `paypal.com` for `mail.paypal.com`."""
    return named == real or real.endswith(f".{named}") or named.endswith(f".{real}")


def browser_url(value: str) -> str:
    """`value` as a browser's URL parser reads it: leading and trailing C0 controls and spaces
    stripped, tabs and newlines removed. Not Python's `strip()`, which also removes Unicode
    spaces such as U+00A0 that a browser keeps, so a URL would pass as absolute here and
    resolve as a relative one there."""
    return value.strip(_C0_OR_SPACE).translate(_TAB_OR_NEWLINE)


def web_url(url: str) -> str:
    """An `http(s)` URL with its backslashes as slashes, which is how a browser reads them: in
    `https://evil.example\\@paypal.com` the host is `evil.example`, where Python's parser says
    `paypal.com`. Any other URL as it is."""
    return url.replace("\\", "/") if _WEB.match(url) else url


def link_host(url: str) -> str | None:
    """The host a link (`browser_url` and `web_url` applied) goes to, in IDNA form: an `http(s)`
    URL's host, a `mailto:` link's first address's domain; None when it has none."""
    if _MAILTO.match(url):
        address = unquote(url[7:].split("?", 1)[0]).split(",", 1)[0].strip()
        local, at, domain = address.rpartition("@")
        host = domain.strip(" >") if at and local else ""
    elif _WEB.match(url):
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            return None
    else:
        return None
    return ascii_host(unicodedata.normalize("NFKC", host))


def element_text(element: _Element) -> str:
    """An element's text, its descendants' included, gathered only until there is enough for
    `names_other_host`: a link around a whole body costs no more than a short one."""
    pieces: list[str] = []
    left = 4 * MAX_TEXT
    for piece in element.itertext():
        pieces.append(piece[:left])
        left -= len(piece)
        if left <= 0:
            break
    return "".join(pieces)


def names_other_host(text: str, host: str | None) -> bool:
    """Whether `text` names a host that is neither `host` nor a parent or child of it (any host,
    when `host` is None). `text` is cleaned of invisible characters and cut at `MAX_TEXT`."""
    normalised = unicodedata.normalize("NFKC", clean_header(text, MAX_TEXT))
    return any(
        host is None or (named := ascii_host(found)) is None or not related(named, host)
        for found in DOMAIN_LIKE.findall(normalised)
    )


def link_mark(host: str | None) -> str:
    """The warning beside a link whose text names another host: `MARK` and the real host."""
    return MARK if host is None else f"{MARK} {host}"
