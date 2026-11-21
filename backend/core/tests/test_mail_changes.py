"""A folder's UIDs and flags against the test Dovecot, which offers CONDSTORE: HIGHESTMODSEQ from
EXAMINE and STATUS, the flags changed since a mod-sequence, and UIDs listed range by range."""

from typing import TYPE_CHECKING

import pytest

from mailroom_core.mail import MailConnections, changes
from mailroom_core.testing import (
    ImapTestServer,
    connections_for,
    mime,
    raw_client,
    seed,
    test_account,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration


@pytest.fixture
def user(imap_server: ImapTestServer) -> str:
    return imap_server.unique_user("changes")


@pytest.fixture
def mail(imap_server: ImapTestServer, user: str) -> Iterator[MailConnections]:
    connections = connections_for(imap_server, [test_account(1, user, imap_server)])
    yield connections
    connections.close_all()


def test_detects_condstore(mail: MailConnections) -> None:
    assert mail.capabilities(1).condstore


def test_highest_modseq_from_examine_and_status(
    imap_server: ImapTestServer, user: str, mail: MailConnections
) -> None:
    seed(imap_server, user, "INBOX", mime("one"))
    examined = mail.with_mailbox(1, "INBOX", True, lambda context: context.highest_modseq)
    inbox = next(f for f in mail.folders(1, with_status=True) if f.path == "INBOX")
    assert examined is not None
    assert inbox.highest_modseq == examined


def test_lists_flags_and_only_the_changes_since_a_modseq(
    imap_server: ImapTestServer, user: str, mail: MailConnections
) -> None:
    read = seed(imap_server, user, "INBOX", mime("read"), flags=["\\Seen"])
    unread = seed(imap_server, user, "INBOX", mime("unread"))

    everything = mail.folder_flags(1, "INBOX")
    assert everything.flags == {read: frozenset({"\\Seen"}), unread: frozenset()}
    assert everything.highest_modseq is not None
    assert mail.changed_flags(1, "INBOX", everything.highest_modseq).flags == {}

    with raw_client(imap_server, user) as client:
        client.select_folder("INBOX")
        client.add_flags([unread], ["\\Flagged"])
    added = seed(imap_server, user, "INBOX", mime("new"))

    changed = mail.changed_flags(1, "INBOX", everything.highest_modseq)
    assert changed.flags == {unread: frozenset({"\\Flagged"}), added: frozenset()}
    assert changed.uid_validity == everything.uid_validity
    assert changed.highest_modseq is not None
    assert changed.highest_modseq > everything.highest_modseq


def test_lists_uids_range_by_range_after_an_expunge(
    imap_server: ImapTestServer, user: str, mail: MailConnections, monkeypatch
) -> None:
    uids = [seed(imap_server, user, "INBOX", mime(f"m{n}")) for n in range(5)]
    with raw_client(imap_server, user) as client:
        client.select_folder("INBOX")
        client.delete_messages([uids[1]])
        client.uid_expunge([uids[1]])
    monkeypatch.setattr(changes, "UID_CHUNK", 2)

    listed = mail.folder_uids(1, "INBOX")
    assert listed.uids == (uids[0], *uids[2:])
    assert mail.folder_uids(1, "Trash").uids == ()
