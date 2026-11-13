"""A whole message parsed from its raw source: the addresses, subject, date and text the filter
engine matches on. Headers go through the same decoding as the message view (`_mime`), and the
first of each field counts, as in the view's ENVELOPE; the body is every inline text part outside
attached messages, the HTML ones read as their visible text when there is no plain one. Never
raises: what cannot be read is left empty."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes
from email.policy import compat32
from email.utils import getaddresses, parsedate_to_datetime
from typing import TYPE_CHECKING, Final

from mailroom_core.mail._mime import decode_bytes, header_fields, header_text, unflow
from mailroom_core.render import Address, html_to_text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from email.message import Message

_HEADER_END: Final = re.compile(rb"\r?\n\r?\n")


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    """What a message says about itself, from its raw source."""

    sender: tuple[Address, ...]
    """The first `From:` field's mailboxes."""
    to: tuple[Address, ...]
    cc: tuple[Address, ...]
    bcc: tuple[Address, ...]
    """The message's own `Bcc:` field, which most delivered mail no longer has."""
    subject: str
    """Decoded and flattened as the view shows it (whitespace collapsed, at most 998
    characters); empty when there is none."""
    date: datetime | None
    """The `Date:` field in UTC (one without a zone taken as UTC); None when absent or
    unreadable."""
    text: str
    """Every inline `text/plain` part (format=flowed joined), separated by line breaks;
    without any, every inline `text/html` part as its visible text. Line breaks are `\\n`."""


def parsed_message(source: bytes) -> ParsedMessage:
    """`source`, a message as `BODY[]` returns it, parsed."""
    fields = header_fields(_HEADER_END.split(source, maxsplit=1)[0])

    def first(name: str) -> str | None:
        values = fields.get(name)
        return values[0] if values else None

    subject = first("subject")
    return ParsedMessage(
        sender=_addresses(first("from")),
        to=_addresses(first("to")),
        cc=_addresses(first("cc")),
        bcc=_addresses(first("bcc")),
        subject=header_text(subject) if subject is not None else "",
        date=_date(first("date")),
        text=_text(source),
    )


def _addresses(value: str | None) -> tuple[Address, ...]:
    """The mailboxes of an address field, display names decoded; groups give their members. A
    field strict parsing gives up on (a trailing comma, a comma in a comment, `;` between
    addresses) is read leniently rather than dropped whole."""
    if not value:
        return ()
    try:
        pairs = getaddresses([value])
        if pairs == [("", "")]:
            pairs = getaddresses([value], strict=False)
    except Exception:
        return ()
    found = []
    for display, address in pairs:
        decoded = header_text(display)
        if address.strip() or decoded:
            found.append(Address(decoded or None, address.strip() or None))
    return tuple(found)


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        date = parsedate_to_datetime(value)
        return date.replace(tzinfo=UTC) if date.tzinfo is None else date.astimezone(UTC)
    except TypeError, ValueError, IndexError, OverflowError:
        return None


def _text(source: bytes) -> str:
    plain: list[str] = []
    html: list[str] = []
    try:
        for part in _inline_leaves(message_from_bytes(source, policy=compat32)):
            kind = part.get_content_type()
            if kind not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                continue
            text = decode_bytes(payload, part.get_content_charset()).replace("\r\n", "\n")
            if kind == "text/html":
                html.append(text)
            elif str(part.get_param("format", "")).lower() == "flowed":
                plain.append(unflow(text, str(part.get_param("delsp", "")).lower() == "yes"))
            else:
                plain.append(text)
    except Exception:
        return "\n".join(plain)
    joined = "\n".join(plain)
    if joined.strip() or not html:
        return joined
    try:
        return html_to_text("\n".join(html), mark_quotes=False).text
    except Exception:
        return ""


def _inline_leaves(part: Message) -> Iterator[Message]:
    """The message's own leaf parts, in order: not the parts of an attached message
    (`message/rfc822`), nor files sent along."""
    if part.get_content_maintype() == "message" or _attached(part):
        return
    if part.is_multipart():
        for child in part.get_payload():
            if not isinstance(child, str):
                yield from _inline_leaves(child)
    else:
        yield part


def _attached(part: Message) -> bool:
    """Whether the part is a file sent along rather than the message's own text."""
    try:
        return part.get_content_disposition() == "attachment" or part.get_filename() is not None
    except Exception:
        return True
