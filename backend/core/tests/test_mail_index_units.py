"""An index's Gmail rows and label lookups, and its plan's age limit, over a fake client."""

from datetime import date
from typing import Any, cast

import pytest

from mailroom_core.mail import MailboxContext, MailError, fetch_index, gmail_uid, plan_index


class _Client:
    """A Gmail server (or not) with one message per UID; records what it is asked."""

    def __init__(self, uids: list[int], *, gmail: bool = True) -> None:
        self.uids = uids
        self.gmail = gmail
        self.use_uid = True
        self.calls: list[tuple[object, ...]] = []

    def has_capability(self, name: str) -> bool:
        return self.gmail and name == "X-GM-EXT-1"

    def search(self, criteria: list[object]) -> list[int]:
        self.calls.append(("search", *criteria))
        return list(self.uids)

    def _raw_command_untagged(self, command: bytes, arguments: list[bytes]) -> list[bytes]:
        self.calls.append((command.decode(), *arguments))
        return [b" ".join(str(uid).encode() for uid in self.uids)]

    def fetch(self, uids: list[int], items: list[str]) -> dict[int, dict[bytes, object]]:
        self.calls.append(("fetch", tuple(uids), tuple(items)))
        if "X-GM-LABELS" not in items:
            return {}
        return {
            uid: {
                b"FLAGS": (b"\\Seen", b"\\Recent"),
                b"INTERNALDATE": None,
                b"X-GM-LABELS": (
                    b"\\Inbox",
                    b"Caf&AOk-",
                    b"&J2T+Dw- Family",
                    b"&2AA-",
                    b"&!!-",
                    b"&AAA-",
                ),
                b"X-GM-MSGID": 1_700_000_000_000_000_000 + uid,
                b"X-GM-THRID": 1_600_000_000_000_000_000,
            }
            for uid in uids
            if uid in self.uids
        }


def _context(client: _Client) -> MailboxContext:
    return MailboxContext(
        cast("Any", client), "[Gmail]/All Mail", True, 7, len(client.uids), None, 3
    )


def test_plans_with_the_age_limit_newest_first() -> None:
    client = _Client([3, 5, 9, 12, 20])
    plan = plan_index(_context(client), since=date(2026, 5, 1), size=2)
    assert plan.batches == ((20, 12), (9, 5), (3,))
    assert client.calls == [("search", "UID", "1:*", "SINCE", date(2026, 5, 1))]
    with pytest.raises(MailError) as caught:
        plan_index(_context(client), size=0)
    assert caught.value.code == "invalid_argument"


def test_a_gmail_row_carries_its_labels_and_ids() -> None:
    client = _Client([4, 6])
    rows = fetch_index(_context(client), [4, 6, 8], uid_validity=7)
    assert [row.uid for row in rows] == [6, 4]
    row = rows[0]
    # Decoded as LIST decodes a path: the emoji's variation selector stays, so it matches.
    assert row.gmail_labels == ("\\Inbox", "Café", "\u2764\ufe0f Family", "\ufffd", "&!!-")
    assert row.gmail_message_id == 1_700_000_000_000_000_006
    assert row.thread_key == "gmail:1600000000000000000"
    assert row.flags == {"\\Seen"}
    assert row.account == 3
    assert row.folder == "[Gmail]/All Mail"
    assert row.snippet is None


def test_finds_a_messages_uid_in_a_label_folder() -> None:
    client = _Client([41])
    assert gmail_uid(_context(client), 1_700_000_000_000_000_006) == 41
    assert client.calls == [("SEARCH", b"X-GM-MSGID", b"1700000000000000006")]
    assert gmail_uid(_context(_Client([])), 1) is None
    with pytest.raises(MailError) as caught:
        gmail_uid(_context(_Client([41], gmail=False)), 1)
    assert caught.value.code == "unsupported"
