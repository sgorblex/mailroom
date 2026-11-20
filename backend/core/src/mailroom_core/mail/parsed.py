"""A whole message parsed from its raw source: the addresses, subject, date and text the filter
engine matches on. Headers go through the same decoding as the message view (`_mime`), and the
first of each field counts, as in the view's ENVELOPE; the body is every inline text part outside
attached messages, the HTML ones read as their visible text when there is no plain one. Only the
first `MAX_HEADER_BYTES` of the header block are read. Never raises: what cannot be read is left
empty."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes
from email.policy import compat32
from email.utils import getaddresses, parsedate_to_datetime
from typing import TYPE_CHECKING, Final

from mailroom_core.mail._mime import decode_bytes, header_fields, header_text, unflow
from mailroom_core.mail.message_headers import complete_fields, list_id
from mailroom_core.render import Address, html_to_text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from email.message import Message

_HEADER_END: Final = re.compile(rb"\r?\n\r?\n")
MAX_HEADER_BYTES: Final = 256 * 1024
"""Of the header block: a longer one is cut at the last whole field before it."""
MAX_REFERENCES: Final = 100
"""Message-IDs kept from References: the first (the thread's root), then the most recent."""
MAX_IN_REPLY_TO: Final = 16
MAX_MESSAGE_ID: Final = 900
"""Of a Message-ID, brackets included: a longer one is left out."""
_ANGLED: Final = re.compile(r"<([^<>]*)>")
_ID_CHARS: Final = re.compile(r"[!-;=?-~]+")
"""What a Message-ID holds between its brackets: printable ASCII but for `<` and `>`."""


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
    message_id: str | None = None
    """The first `Message-ID:` field's id, angle brackets included."""
    references: tuple[str, ...] = ()
    """The first `References:` field's ids, at most `MAX_REFERENCES`."""
    in_reply_to: tuple[str, ...] = ()
    """The first `In-Reply-To:` field's ids, at most `MAX_IN_REPLY_TO`."""
    list_id: str | None = None
    """The first `List-Id:` field's identifier, without its angle brackets (RFC 2919)."""


def parsed_message(source: bytes) -> ParsedMessage:
    """`source`, a message as `BODY[]` returns it, parsed."""
    fields = header_fields(_header_block(source))

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
        message_id=next(iter(_message_ids(first("message-id"))), None),
        references=_references(_message_ids(first("references"))),
        in_reply_to=_message_ids(first("in-reply-to"))[:MAX_IN_REPLY_TO],
        list_id=_list_id(first("list-id")),
    )


def _header_block(source: bytes) -> bytes:
    head, *rest = _HEADER_END.split(source[:MAX_HEADER_BYTES], maxsplit=1)
    return complete_fields(head) if not rest and len(source) > MAX_HEADER_BYTES else head


def _message_ids(value: str | None) -> tuple[str, ...]:
    """The well-formed `<local@domain>` ids of a field, in order. Matched bracket by bracket,
    then checked, so a field of the sender's is read in one pass whatever it holds."""
    if not value:
        return ()
    return tuple(
        f"<{inner}>"
        for inner in _ANGLED.findall(value)
        if len(inner) + 2 <= MAX_MESSAGE_ID and _ID_CHARS.fullmatch(inner) and "@" in inner[1:-1]
    )


def _references(ids: tuple[str, ...]) -> tuple[str, ...]:
    if len(ids) <= MAX_REFERENCES:
        return ids
    return (ids[0], *ids[len(ids) - MAX_REFERENCES + 1 :])


def _list_id(value: str | None) -> str | None:
    found = list_id(value) if value else None
    return found[0] if found is not None else None


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
