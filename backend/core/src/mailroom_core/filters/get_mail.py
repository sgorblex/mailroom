"""IMAP fetch helpers (search + fetch + parse)."""

import logging

from imapclient import IMAPClient
from mailparser import parse_from_bytes

from imap_thingy.core import IMAPQuery, Message

log = logging.getLogger(__name__)


def search_mail(client: IMAPClient, imap_query: IMAPQuery) -> list[int]:
    """Run the IMAP query and return matching message IDs."""
    criteria = imap_query.build()
    log.debug("IMAP query: %s", criteria)
    return client.search(criteria)


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
