"""IMAP fetch helpers (search + fetch + parse)."""

import logging

from imapclient import IMAPClient
from mailparser import parse_from_bytes

from imap_thingy.core import IMAPQuery, Message

_log = logging.getLogger(__name__)


def search_mail(client: IMAPClient, imap_query: IMAPQuery) -> list[int]:
    """Run the IMAP query and return matching message IDs."""
    return client.search(imap_query.build())


def fetch_mail(client: IMAPClient, msg_ids: list[int]) -> dict[int, Message]:
    """Fetch bodies and flags, parse; return msgid -> Message (parsed + flags)."""
    if not msg_ids:
        return {}
    fetched = client.fetch(msg_ids, ["BODY.PEEK[]", "FLAGS"])
    messages: dict[int, Message] = {}
    for msgid, data in fetched.items():
        body_data = data.get(b"BODY.PEEK[]") or data.get(b"BODY[]")
        if not body_data:
            _log.debug("msgid %s: no body", msgid)
            continue
        try:
            parsed = parse_from_bytes(body_data)
            flags = list(data.get(b"FLAGS", []))
            messages[msgid] = Message(id=msgid, parsed=parsed, flags=flags)
        except Exception as e:
            _log.debug("msgid %s: parse failed: %s", msgid, e)
            continue
    return messages
