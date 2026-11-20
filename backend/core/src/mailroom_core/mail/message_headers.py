"""The header fields a read shows beside the envelope, parsed from one capped fetch: mailing-list
links (RFC 2369, 2919, 8058), Authentication-Results (an RFC 8601 subset), DKIM-Signature tags
(RFC 6376), the From field count and the delivery addresses. Pure: no IMAP. Every value is the
sender's, or a relay's the sender may have forged: nothing here is trusted until a host compares
an `AuthResults.authserv_id` with the account's own (`trusted_results`)."""

import re
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import unquote, urlsplit

from mailroom_core.mail._mime import header_fields, header_text
from mailroom_core.render.hosts import ascii_host

FIELDS: Final = (
    "FROM",
    "LIST-ID",
    "LIST-UNSUBSCRIBE",
    "LIST-UNSUBSCRIBE-POST",
    "LIST-ARCHIVE",
    "LIST-POST",
    "LIST-HELP",
    "AUTHENTICATION-RESULTS",
    "DKIM-SIGNATURE",
    "DELIVERED-TO",
    "X-ORIGINAL-TO",
)
MAX_BYTES: Final = 64 * 1024
"""Of the fields above, together: a block this long counts as cut (`truncated`)."""
FETCH_ITEM: Final = f"BODY.PEEK[HEADER.FIELDS ({' '.join(FIELDS)})]<0.{MAX_BYTES}>"
"""The FETCH item that reads them, at most `MAX_BYTES`."""

MAX_URL: Final = 2048
MAX_LIST_ID: Final = 255
MAX_LIST_NAME: Final = 200
MAX_DOMAIN: Final = 253
MAX_SIGNATURE_PREFIX: Final = 128
"""Of a DKIM `b=` and an Authentication-Results `header.b`: enough to match one with the other."""
MAX_AUTH_FIELDS: Final = 32
"""Authentication-Results fields read, topmost first; the rest are left out."""
MAX_AUTH_FIELD_BYTES: Final = 8 * 1024
MAX_RESULTS: Final = 32
"""Results read from one Authentication-Results field."""
MAX_PROPERTIES: Final = 16
"""Properties read from one result: a result with more is malformed."""
MAX_SIGNATURES: Final = 16
MAX_SIGNATURE_BYTES: Final = 16 * 1024
MAX_SIGNED_FIELDS: Final = 64
MAX_DELIVERED: Final = 16
_MAX_URLS: Final = 8
"""`<…>` entries read from one list field."""
_MAX_COMMENT_DEPTH: Final = 8

Method = Literal["spf", "dkim", "dmarc"]
_METHODS: Final = frozenset({"spf", "dkim", "dmarc"})
ONE_CLICK: Final = "List-Unsubscribe=One-Click"


@dataclass(frozen=True, slots=True)
class ListInfo:
    """A mailing list's own fields, the topmost of each: links only `https:` or `mailto:`."""

    id: str | None = None
    """List-Id's identifier, without its angle brackets."""
    name: str | None = None
    """List-Id's phrase, decoded."""
    archive: str | None = None
    post: str | None = None
    help: str | None = None
    unsubscribe_https: str | None = None
    unsubscribe_mailto: str | None = None
    one_click: bool = False
    """The topmost List-Unsubscribe-Post is exactly `List-Unsubscribe=One-Click` (RFC 8058)."""
    unsubscribe_fields: int = 0
    """How many List-Unsubscribe fields the message has: one-click needs exactly one."""
    unsubscribe_post_fields: int = 0


@dataclass(frozen=True, slots=True)
class AuthResult:
    """One `spf`, `dkim` or `dmarc` result of an Authentication-Results field."""

    method: Method
    result: str
    """Lowercase, as written (`pass`, `fail`, `none`…)."""
    header_d: str | None = None
    """Lowercase."""
    header_b: str | None = None
    """A prefix of the signature it checked, case kept."""
    header_from: str | None = None
    """Lowercase."""
    smtp_mailfrom: str | None = None
    """The domain of `smtp.mailfrom` (what follows its last `@`, if any), lowercase."""


@dataclass(frozen=True, slots=True)
class AuthResults:
    """One Authentication-Results field. A field whose results cannot all be read has none, and
    one whose authserv-id cannot be read has `authserv_id` None: either still stands in its
    place, so a field below it is never taken for it."""

    authserv_id: str | None
    """Lowercase."""
    results: tuple[AuthResult, ...] = ()


@dataclass(frozen=True, slots=True)
class DkimSignature:
    """One well-formed DKIM-Signature's tags; a malformed one is left out."""

    d: str
    """Lowercase."""
    b: str
    """The signature's start, at most `MAX_SIGNATURE_PREFIX` characters, whitespace removed."""
    h: tuple[str, ...]
    """The signed fields, lowercase, in order."""


@dataclass(frozen=True, slots=True)
class MessageHeaders:
    """What `parse_headers` reads from the `FIELDS` of a message."""

    mailing_list: ListInfo | None = None
    """None when the message has no List-* field."""
    authentication: tuple[AuthResults, ...] = ()
    """Topmost first, at most `MAX_AUTH_FIELDS`."""
    dkim_signatures: tuple[DkimSignature, ...] = ()
    from_fields: int = 0
    delivered_to: tuple[str, ...] = ()
    """X-Original-To's addresses, then Delivered-To's, each topmost first."""
    truncated: bool = False
    """The block reached `MAX_BYTES`: its last field was left out, and counts may be short."""


_FIELD_START: Final = re.compile(rb"\n(?=[^ \t])")
_ADDRESS: Final = re.compile(r"[^\s<>,;:\"()]+@[^\s<>,;:\"()]+")


def _complete(block: bytes) -> bytes:
    """A cut block without its last, possibly partial, field."""
    starts = [match.end() for match in _FIELD_START.finditer(block)]
    return block[: starts[-1]] if starts else b""


def parse_headers(block: bytes, *, cap: int = MAX_BYTES) -> MessageHeaders:
    """The `FIELDS` of a header block (`FETCH_ITEM`'s answer), a block of `cap` bytes or more
    being cut. Never raises: what cannot be read is left out."""
    truncated = len(block) >= cap
    fields = header_fields(_complete(block[:cap]) if truncated else block)
    authentication = fields.get("authentication-results", [])[:MAX_AUTH_FIELDS]
    return MessageHeaders(
        mailing_list=_list_info(fields),
        authentication=tuple(_auth_results(value) for value in authentication),
        dkim_signatures=tuple(
            signature
            for value in fields.get("dkim-signature", ())[:MAX_SIGNATURES]
            if (signature := _dkim_signature(value)) is not None
        ),
        from_fields=len(fields.get("from", ())),
        delivered_to=delivery_addresses(fields),
        truncated=truncated,
    )


def delivery_addresses(fields: dict[str, list[str]]) -> tuple[str, ...]:
    """The addresses of X-Original-To, then Delivered-To (`header_fields`), at most
    `MAX_DELIVERED`: where an alias actually delivered the message."""
    found = (
        address
        for name in ("x-original-to", "delivered-to")
        for value in fields.get(name, ())
        for address in _ADDRESS.findall(value)
        if len(address) <= 254
    )
    return tuple(address for _, address in zip(range(MAX_DELIVERED), found, strict=False))


def trusted_results(headers: MessageHeaders, authserv_id: str | None) -> AuthResults | None:
    """The topmost Authentication-Results field of `authserv_id` (RFC 8601, 5): the only one a
    host may believe. None when the id is unset, no field has it, or a field above it has no
    readable authserv-id (it may be that very field, so none below it is taken)."""
    if not authserv_id:
        return None
    wanted = authserv_id.strip().lower()
    for results in headers.authentication:
        if results.authserv_id is None:
            return None
        if results.authserv_id == wanted:
            return results
    return None


@dataclass(frozen=True, slots=True)
class Authentication:
    """The verdict of the account's own authentication server on a message (`authentication`).
    Not `verified`, every result is None: nothing the message says about itself is believed."""

    verified: bool
    """The account names its authserv-id and the message has a field of it."""
    spf: str | None = None
    spf_domain: str | None = None
    """The domain SPF checked (`smtp.mailfrom`'s), as an ASCII host name; None when it is none."""
    dkim: str | None = None
    """`pass` when any signature passed, else the first result."""
    dkim_domain: str | None = None
    """The signing domain (`header.d`) of the result `dkim` is, likewise."""
    dmarc: str | None = None
    """As written: a `pass` counts only as `dmarc_pass` says."""
    dmarc_pass: bool = False
    """DMARC passed for this message's one From: `header.from` is its domain."""
    from_domain: str | None = None
    """That From's domain, when `dmarc_pass`."""


NOT_VERIFIED: Final = Authentication(verified=False)


def authentication(
    headers: MessageHeaders, senders: tuple[str, ...], authserv_id: str | None
) -> Authentication:
    """The `trusted_results` of `authserv_id`, read for `senders`, the From addresses. DMARC
    passes only for exactly one From (one field, one address, and a block not cut short) whose
    domain is the result's `header.from`: a second From is what a reader may be shown instead."""
    trusted = trusted_results(headers, authserv_id)
    if trusted is None:
        return NOT_VERIFIED

    def first(method: Method) -> AuthResult | None:
        return next((r for r in trusted.results if r.method == method), None)

    spf, dmarc = first("spf"), first("dmarc")
    dkim = next(
        (r for r in trusted.results if r.method == "dkim" and r.result == "pass"), first("dkim")
    )
    domain = (
        senders[0].rpartition("@")[2].strip().lower()
        if len(senders) == 1 and headers.from_fields == 1 and not headers.truncated
        else None
    )
    dmarc_pass = bool(
        dmarc is not None
        and dmarc.result == "pass"
        and dmarc.header_from is not None
        and (from_host := ascii_host(domain or "")) is not None
        and ascii_host(dmarc.header_from) == from_host
    )
    return Authentication(
        verified=True,
        spf=None if spf is None else spf.result,
        spf_domain=None if spf is None else ascii_host(spf.smtp_mailfrom or ""),
        dkim=None if dkim is None else dkim.result,
        dkim_domain=None if dkim is None else ascii_host(dkim.header_d or ""),
        dmarc=None if dmarc is None else dmarc.result,
        dmarc_pass=dmarc_pass,
        from_domain=domain if dmarc_pass else None,
    )


# Unsubscribing (RFC 8058, RFC 6068)

OneClickRefusal = Literal["unavailable", "repeated", "not_https", "unverified", "not_signed"]
"""Why a message may not be unsubscribed from in one click: no List-Unsubscribe with a
`List-Unsubscribe=One-Click` List-Unsubscribe-Post; more than one of either field (or a block cut
short, where a second may hide); no `https:` link in it; no authserv-id for the account; no
signature the account's server passed that signs both fields."""
MIN_SIGNATURE_PREFIX: Final = 8
"""The shortest `header.b` that names a signature (RFC 6008, 2)."""
_UNSUBSCRIBE_SIGNED: Final = frozenset({"list-unsubscribe", "list-unsubscribe-post"})


@dataclass(frozen=True, slots=True)
class OneClick:
    """`one_click_unsubscribe`'s answer: the URL to POST to, or why there is none."""

    url: str | None
    refused: OneClickRefusal | None = None


def one_click_unsubscribe(headers: MessageHeaders, authserv_id: str | None) -> OneClick:
    """The `https:` List-Unsubscribe URL of a message that may be unsubscribed from in one click
    (RFC 8058, 3): exactly one List-Unsubscribe and one List-Unsubscribe-Post, the latter
    `List-Unsubscribe=One-Click`, and a DKIM signature that signs both and that the topmost
    Authentication-Results field of `authserv_id` passed, named by its `header.d` and `header.b`
    (only one signature may answer to them)."""
    info = headers.mailing_list
    if info is None or not info.unsubscribe_fields or not info.one_click:
        return OneClick(None, "unavailable")
    if headers.truncated or info.unsubscribe_fields != 1 or info.unsubscribe_post_fields != 1:
        return OneClick(None, "repeated")
    if info.unsubscribe_https is None:
        return OneClick(None, "not_https")
    if not authserv_id:
        return OneClick(None, "unverified")
    trusted = trusted_results(headers, authserv_id)
    passed = () if trusted is None else trusted.results
    for result in passed:
        if result.method != "dkim" or result.result != "pass":
            continue
        d, b = result.header_d, result.header_b
        if d is None or b is None or len(b) < MIN_SIGNATURE_PREFIX:
            continue
        named = [s for s in headers.dkim_signatures if s.d == d and s.b.startswith(b)]
        if len(named) == 1 and set(named[0].h) >= _UNSUBSCRIBE_SIGNED:
            return OneClick(info.unsubscribe_https)
    return OneClick(None, "not_signed")


MAX_MAILTO_HFIELDS: Final = 16


@dataclass(frozen=True, slots=True)
class Mailto:
    """What a `mailto:` unsubscribe link asks to be sent: one address, a subject and a body
    (RFC 6068), percent-decoded; every other field (cc, bcc, more To) is dropped."""

    to: str
    subject: str = ""
    body: str = ""


def mailto_fields(url: str) -> Mailto | None:
    """`url`'s first address and its first `subject` and `body`; None when it is no
    `mailto:` link with an address."""
    if len(url) > MAX_URL or not _mailto(url):
        return None
    addresses, _, query = url[7:].partition("?")
    to = unquote(addresses.split(",", 1)[0], errors="replace").strip()
    if not _ADDRESS.fullmatch(to):
        return None
    fields: dict[str, str] = {}
    for pair in query.split("&")[:MAX_MAILTO_HFIELDS]:
        name, _, value = pair.partition("=")
        fields.setdefault(unquote(name).strip().lower(), unquote(value, errors="replace"))
    return Mailto(to=to, subject=fields.get("subject", ""), body=fields.get("body", ""))


# Mailing lists


def _printable(text: str) -> bool:
    return all("!" <= c <= "~" for c in text)


def _https(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (
        parts.scheme.lower() == "https"
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
    )


def _mailto(url: str) -> bool:
    return url[:7].lower() == "mailto:" and "@" in url.split("?", 1)[0]


_ANGLED: Final = re.compile(r"<([^<>]*)>")


def _links(value: str) -> list[str]:
    """The `https:` and `mailto:` URLs of an RFC 2369 field, in order (whitespace inside the
    brackets dropped, as folding may put it there)."""
    out: list[str] = []
    for match in list(_ANGLED.finditer(value))[:_MAX_URLS]:
        url = re.sub(r"\s+", "", match.group(1))
        if len(url) <= MAX_URL and _printable(url) and (_https(url) or _mailto(url)):
            out.append(url)
    return out


def _first_link(fields: dict[str, list[str]], name: str) -> str | None:
    values = fields.get(name)
    return next(iter(_links(values[0])), None) if values else None


_LIST_ID: Final = re.compile(r"(.*?)<([^<>\s]+)>\s*", re.DOTALL)


def _list_info(fields: dict[str, list[str]]) -> ListInfo | None:
    if not any(name.startswith("list-") for name in fields):
        return None
    list_id = name = None
    if ids := fields.get("list-id"):
        match = _LIST_ID.fullmatch(ids[0])
        if match is not None and len(match.group(2)) <= MAX_LIST_ID and _printable(match.group(2)):
            list_id = match.group(2)
            name = header_text(match.group(1)).strip().strip('"').strip()[:MAX_LIST_NAME] or None
    unsubscribe = fields.get("list-unsubscribe", [])
    links = _links(unsubscribe[0]) if unsubscribe else []
    post = fields.get("list-unsubscribe-post", [])
    return ListInfo(
        id=list_id,
        name=name,
        archive=_first_link(fields, "list-archive"),
        post=_first_link(fields, "list-post"),
        help=_first_link(fields, "list-help"),
        unsubscribe_https=next((u for u in links if not _mailto(u)), None),
        unsubscribe_mailto=next((u for u in links if _mailto(u)), None),
        one_click=bool(post) and post[0].strip() == ONE_CLICK,
        unsubscribe_fields=len(unsubscribe),
        unsubscribe_post_fields=len(post),
    )


# Authentication-Results (RFC 8601, 2.2): authserv-id [version] (; resinfo)*, CFWS anywhere.


def _uncomment(text: str) -> str | None:
    """`text` with its comments (nested, RFC 5322) replaced by a space; None when a comment or
    quoted string is unbalanced or nested too deep."""
    out: list[str] = []
    depth = 0
    quoted = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and (depth or quoted):
            if not depth:
                out.append(text[index : index + 2])
            index += 2
            continue
        if quoted:
            out.append(char)
            quoted = char != '"'
        elif char == "(":
            depth += 1
            if depth > _MAX_COMMENT_DEPTH:
                return None
        elif char == ")":
            if not depth:
                return None
            depth -= 1
            if not depth:
                out.append(" ")
        elif not depth:
            out.append(char)
            quoted = char == '"'
        index += 1
    return None if depth or quoted else "".join(out)


def _split(text: str) -> list[str]:
    """`text` split at the semicolons outside quoted strings."""
    parts: list[str] = []
    start = 0
    quoted = False
    index = 0
    while index < len(text):
        char = text[index]
        if quoted and char == "\\":
            index += 2
            continue
        if char == '"':
            quoted = not quoted
        elif char == ";" and not quoted:
            parts.append(text[start:index])
            start = index + 1
        index += 1
    parts.append(text[start:])
    return parts


_QUOTED: Final = r'"(?:[^"\\]|\\.)*"'
_TOKEN: Final = r"[A-Za-z0-9!#$%&'*+.^_`{|}~-]+"
_AUTHSERV: Final = re.compile(rf"\s*({_TOKEN}|{_QUOTED})(?:\s+[0-9]+)?\s*")
_METHOD: Final = re.compile(r"\s*([A-Za-z0-9-]+)(?:\s*/\s*[0-9]+)?\s*=\s*([A-Za-z0-9-]+)")
_VALUE: Final = rf'({_QUOTED}|[^\s"]+)'
_REASON: Final = re.compile(rf"\s*reason\s*=\s*{_VALUE}", re.IGNORECASE)
_PROPERTY: Final = re.compile(rf"\s*([A-Za-z0-9-]+)\s*\.\s*([A-Za-z0-9_-]+)\s*=\s*{_VALUE}")
_BARE_PROPERTY: Final = re.compile(rf"\s*[A-Za-z0-9_-]+\s*=\s*{_VALUE}")
"""A `name=value` outside the ptype.property form that servers still write (`x-bits=2048`,
`action=none`): skipped."""


def _unquote(value: str) -> str:
    if value.startswith('"'):
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def _result(segment: str) -> AuthResult | Literal[False] | None:
    """One resinfo: its result, None for a method left out, False when malformed."""
    method = _METHOD.match(segment)
    if method is None:
        return False
    position = method.end()
    properties: dict[str, str] = {}
    if (reason := _REASON.match(segment, position)) is not None:
        position = reason.end()
    count = 0
    while segment[position:].strip():
        prop = _PROPERTY.match(segment, position)
        count += 1
        if count > MAX_PROPERTIES:
            return False
        if prop is None:
            bare = _BARE_PROPERTY.match(segment, position)
            if bare is None:
                return False
            position = bare.end()
            continue
        key = f"{prop.group(1).lower()}.{prop.group(2).lower()}"
        properties.setdefault(key, _unquote(prop.group(3)))
        position = prop.end()
    name = method.group(1).lower()
    if name not in _METHODS:
        return None

    def domain(value: str | None) -> str | None:
        if not value or len(value) > MAX_DOMAIN or not _printable(value):
            return None
        return value.lower()

    b = properties.get("header.b")
    return AuthResult(
        method=name,  # type: ignore[arg-type]
        result=method.group(2).lower(),
        header_d=domain(properties.get("header.d")),
        header_b=b[:MAX_SIGNATURE_PREFIX] if b and _printable(b) else None,
        header_from=domain(properties.get("header.from")),
        smtp_mailfrom=domain(properties.get("smtp.mailfrom", "").rpartition("@")[2]),
    )


def _authserv_id(head: str) -> str | None:
    match = _AUTHSERV.fullmatch(head)
    if match is None:
        return None
    authserv_id = _unquote(match.group(1)).lower()
    if not authserv_id or len(authserv_id) > MAX_DOMAIN or not _printable(authserv_id):
        return None
    return authserv_id


def _auth_results(value: str) -> AuthResults:
    text = _uncomment(value) if len(value) <= MAX_AUTH_FIELD_BYTES else None
    if text is None:
        # The receiving server's own field carries the sender's text (smtp.mailfrom, comments):
        # when that breaks the parse, the authserv-id before it still names the field.
        head, semicolon, _ = value[: MAX_DOMAIN * 2].partition(";")
        text = _uncomment(head) if semicolon else None
        return AuthResults(_authserv_id(text) if text is not None else None)
    head, *segments = _split(text)
    authserv_id = _authserv_id(head)
    if authserv_id is None:
        return AuthResults(None)
    if [s.strip().lower() for s in segments] == ["none"]:
        return AuthResults(authserv_id)
    results: list[AuthResult] = []
    for index, segment in enumerate(segments[:MAX_RESULTS]):
        if not segment.strip() and index == len(segments) - 1:
            continue
        result = _result(segment)
        if result is False:
            return AuthResults(authserv_id)
        if result is not None:
            results.append(result)
    return AuthResults(authserv_id, tuple(results))


# DKIM-Signature (RFC 6376, 3.2): tag=value pairs; a duplicate tag makes it invalid.

_TAG: Final = re.compile(r"\s*([A-Za-z][A-Za-z0-9_]*)\s*=(.*)", re.DOTALL)
_FIELD_NAME: Final = re.compile(r"[!-9;-~]+")
_BASE64: Final = re.compile(r"[A-Za-z0-9+/]*=*")


def _dkim_signature(value: str) -> DkimSignature | None:
    if len(value) > MAX_SIGNATURE_BYTES:
        return None
    tags: dict[str, str] = {}
    segments = value.split(";")
    for index, segment in enumerate(segments):
        if not segment.strip() and index == len(segments) - 1:
            continue
        match = _TAG.fullmatch(segment)
        if match is None or match.group(1) in tags:
            return None
        tags[match.group(1)] = match.group(2).strip()
    d, b, h = tags.get("d"), tags.get("b"), tags.get("h")
    if not d or b is None or h is None or len(d) > MAX_DOMAIN or not _printable(d):
        return None
    b = re.sub(r"\s+", "", b)
    names = [name.strip().lower() for name in h.split(":")]
    if (
        not b
        or not _BASE64.fullmatch(b)
        or len(names) > MAX_SIGNED_FIELDS
        or not all(_FIELD_NAME.fullmatch(name) for name in names)
    ):
        return None
    return DkimSignature(d=d.lower(), b=b[:MAX_SIGNATURE_PREFIX], h=tuple(names))
