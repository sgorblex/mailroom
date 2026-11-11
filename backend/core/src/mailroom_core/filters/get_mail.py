"""IMAP fetch helpers (search + fetch + parse)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from email.header import decode_header, make_header
from typing import TYPE_CHECKING, Any

from imapclient import IMAPClient, exceptions
from imapclient.imapclient import _is8bit, _quoted, format_criteria_date
from imapclient.response_parser import parse_message_list
from imapclient.util import to_bytes
from mailparser import parse_from_bytes

from imap_thingy.core import IMAPQuery, Message

if TYPE_CHECKING:
    from imap_thingy.report import MessageSummary

log = logging.getLogger(__name__)

type SearchCriteria = (
    str | bytes | int | date | datetime | list["SearchCriteria"] | tuple["SearchCriteria", ...]
)


def _is_simple_search_pair(item: SearchCriteria) -> bool:
    """Return whether *item* is a 2-element (search-key, value) pair, not an OR/NOT compound."""
    if not isinstance(item, tuple) or len(item) != 2:
        return False
    key = item[0]
    return isinstance(key, str) and key.upper() not in ("OR", "NOT")


def _normalise_search_criteria_utf8(criteria: SearchCriteria, charset: str) -> list[bytes]:
    """Like imapclient's ``_normalise_search_criteria`` but charset-safe for nested criteria.

    Upstream omits *charset* on recursive calls (defaulting to ASCII), which breaks
    non-ASCII string values. Simple (key, value) pairs in AND lists are emitted as flat
    atoms (no parentheses); compound OR/NOT groups are parenthesized. For compound groups,
    when the last inner token is 8-bit, ``)`` is appended as its own atom instead of
    merged into the quoted literal.
    """
    if not criteria:
        raise exceptions.InvalidCriteriaError("no criteria specified")

    if isinstance(criteria, (str, bytes)):
        return [to_bytes(criteria, charset)]

    if not isinstance(criteria, (list, tuple)):
        raise TypeError(
            f"search criteria must be str, bytes, list, or tuple, got {type(criteria).__name__}"
        )

    out: list[bytes] = []
    for item in criteria:
        if isinstance(item, int):
            out.append(str(item).encode("ascii"))
        elif isinstance(item, (datetime, date)):
            out.append(format_criteria_date(item))
        elif isinstance(item, (list, tuple)):
            if _is_simple_search_pair(item):
                out.extend(_normalise_search_criteria_utf8(item, charset))
                continue
            inner = _normalise_search_criteria_utf8(item, charset)
            inner[0] = b"(" + inner[0]
            last = inner[-1]
            if _is8bit(last):
                inner.append(b")")
            else:
                inner[-1] = last + b")"
            out.extend(inner)
        else:
            out.append(_quoted.maybe(to_bytes(item, charset)))
    return out


def search_mail(client: IMAPClient, imap_query: IMAPQuery) -> list[int]:
    """Run the IMAP query and return matching message IDs."""
    criteria = imap_query.build()
    charset = "UTF-8"
    args: list[bytes] = [b"CHARSET", to_bytes(charset)]
    args.extend(_normalise_search_criteria_utf8(criteria, charset))
    log.debug("IMAP query: %s", criteria)
    data = client._raw_command_untagged(b"SEARCH", args)  # noqa: SLF001 -- search() lacks charset-safe nesting
    ids: list[int] = parse_message_list(data)
    return ids


def fetch_mail(client: IMAPClient, msg_ids: list[int]) -> dict[int, Message]:
    """Fetch bodies and flags, parse; return msgid -> Message (parsed + flags)."""
    if not msg_ids:
        return {}
    if log.isEnabledFor(logging.DEBUG):
        sample_size = 10
        sample = msg_ids[:sample_size]
        truncated = f" (first {len(sample)} shown)" if len(msg_ids) > sample_size else ""
        log.debug(
            "IMAP FETCH %s msg_id(s)%s (BODY.PEEK[] FLAGS): %s", len(msg_ids), truncated, sample
        )
    fetched = client.fetch(msg_ids, ["BODY.PEEK[]", "FLAGS"])
    messages: dict[int, Message] = {}
    for msgid, data in fetched.items():
        body_data = data.get(b"BODY.PEEK[]") or data.get(b"BODY[]")
        if not body_data:
            log.debug("msgid %s: no body in server response", msgid)
            continue
        try:
            parsed = parse_from_bytes(body_data)
            flags = list(data.get(b"FLAGS", []))
            messages[msgid] = Message(id=msgid, parsed=parsed, flags=flags)
        except Exception as e:
            log.debug("msgid %s: parse error: %s", msgid, e)
            continue
    log.debug("fetch: requested %s, parsed %s", len(msg_ids), len(messages))
    return messages


def _decode_header(value: bytes | str | None) -> str | None:
    """Decode an RFC 2047 header value from an ENVELOPE; undecodable bytes are replaced."""
    if value is None:
        return None
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    try:
        return str(make_header(decode_header(text)))
    except Exception:
        return text


def _format_addresses(addresses: tuple[Any, ...] | None) -> str | None:
    if not addresses:
        return None
    out: list[str] = []
    for a in addresses:
        mailbox = _decode_header(a.mailbox)
        host = _decode_header(a.host)
        address = f"{mailbox}@{host}" if mailbox and host else (mailbox or host or "")
        name = _decode_header(a.name)
        out.append(f"{name} <{address}>" if name else address)
    return ", ".join(out)


def summarize_mail(client: IMAPClient, msg_ids: list[int]) -> list[MessageSummary]:
    """Fetch ENVELOPEs and return each message's UID, From, Subject and Date, in msg_ids order.

    Messages the server returns no envelope for keep their UID with the other fields None, as do
    all of them when the server rejects the FETCH: the report must not block the action.
    """
    if not msg_ids:
        return []
    try:
        fetched = client.fetch(msg_ids, ["ENVELOPE"])
    except exceptions.IMAPClientError as exc:
        log.warning("summarize: ENVELOPE fetch failed: %s", exc)
        fetched = {}
    summaries: list[MessageSummary] = []
    for msgid in msg_ids:
        envelope = fetched.get(msgid, {}).get(b"ENVELOPE")
        if envelope is None:
            summaries.append({"uid": msgid, "from": None, "subject": None, "date": None})
            continue
        summaries.append(
            {
                "uid": msgid,
                "from": _format_addresses(envelope.from_),
                "subject": _decode_header(envelope.subject),
                "date": envelope.date.astimezone(UTC).isoformat() if envelope.date else None,
            }
        )
    return summaries
