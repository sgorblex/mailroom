"""IMAP fetch helpers (search + fetch + parse)."""

from __future__ import annotations

import logging
from datetime import date, datetime

from imapclient import IMAPClient, exceptions
from imapclient.imapclient import _is8bit, _quoted, format_criteria_date
from imapclient.response_parser import parse_message_list
from imapclient.util import to_bytes
from mailparser import parse_from_bytes

from imap_thingy.core import IMAPQuery, Message

log = logging.getLogger(__name__)

type SearchCriteria = str | bytes | int | date | datetime | list["SearchCriteria"] | tuple["SearchCriteria", ...]


def _normalise_search_criteria_utf8(criteria: SearchCriteria, charset: str) -> list[bytes]:
    """Like imapclient's ``_normalise_search_criteria`` but charset-safe for nested criteria.

    Upstream omits *charset* on recursive calls (defaulting to ASCII), which breaks
    non-ASCII string values. It also appends ``)`` to the last inner token; when that
    token is 8-bit (UTF-8 quoted text), the parenthesis is swallowed into the literal
    and servers reject the command (e.g. ``Missing ')'``). For an 8-bit last token we
    append ``)`` as its own atom instead.
    """
    if not criteria:
        raise exceptions.InvalidCriteriaError("no criteria specified")

    if isinstance(criteria, (str, bytes)):
        return [to_bytes(criteria, charset)]

    if not isinstance(criteria, (list, tuple)):
        raise TypeError(f"search criteria must be str, bytes, list, or tuple, got {type(criteria).__name__}")

    out: list[bytes] = []
    for item in criteria:
        if isinstance(item, int):
            out.append(str(item).encode("ascii"))
        elif isinstance(item, (datetime, date)):
            out.append(format_criteria_date(item))
        elif isinstance(item, (list, tuple)):
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
    data = client._raw_command_untagged(b"SEARCH", args)
    return parse_message_list(data)


def fetch_mail(client: IMAPClient, msg_ids: list[int]) -> dict[int, Message]:
    """Fetch bodies and flags, parse; return msgid -> Message (parsed + flags)."""
    if not msg_ids:
        return {}
    if log.isEnabledFor(logging.DEBUG):
        sample_size = 10
        sample = msg_ids[:sample_size]
        truncated = f" (first {len(sample)} shown)" if len(msg_ids) > sample_size else ""
        log.debug("IMAP FETCH %s msg_id(s)%s (BODY.PEEK[] FLAGS): %s", len(msg_ids), truncated, sample)
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
