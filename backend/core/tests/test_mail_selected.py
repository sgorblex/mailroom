"""The operations on a selected folder against the test Dovecot: searches with non-ASCII text in
groups, fetches that leave `\\Seen` alone, the flags they may set, and the one permanent delete
sparing whatever else is marked `\\Deleted`."""

from typing import TYPE_CHECKING

import pytest

from mailroom_core.mail import (
    MailboxContext,
    MailConnections,
    MailError,
    Moved,
    delete_uids,
    fetch_envelopes,
    fetch_sources,
    listed_folders,
    move_uids,
    search_uids,
    store_flags,
)
from mailroom_core.testing import (
    ImapTestServer,
    connections_for,
    create_folders,
    folder_contents,
    mime,
    seed,
    test_account,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from imapclient.response_types import Envelope

    from mailroom_core.mail import FetchedMessage

pytestmark = pytest.mark.integration


@pytest.fixture
def mail(imap_server: ImapTestServer) -> Iterator[Callable[[str], MailConnections]]:
    made: list[MailConnections] = []

    def make(user: str) -> MailConnections:
        connections = connections_for(imap_server, [test_account("acc", user, imap_server)])
        made.append(connections)
        return connections

    yield make
    for connections in made:
        connections.close_all()


def flags_of(server: ImapTestServer, user: str, folder: str = "INBOX") -> dict[str, set[str]]:
    return {m.subject: set(m.flags) for m in folder_contents(server, user, folder)}


def test_searches_non_ascii_text_inside_or_and_not_groups(imap_server, mail) -> None:
    user = imap_server.unique_user("search")
    perche = seed(imap_server, user, "INBOX", mime("=?utf-8?q?perch=C3=A9_no?="))
    citta = seed(imap_server, user, "INBOX", mime("=?utf-8?q?la_citt=C3=A0?="))
    plain = seed(imap_server, user, "INBOX", mime("plain"))
    connections = mail(user)

    def search(criteria: list[object]) -> list[int]:
        return connections.with_mailbox(
            "acc", "INBOX", True, lambda context: sorted(search_uids(context, criteria))
        )

    assert search(["OR", ["SUBJECT", "perché"], ["SUBJECT", "città"]]) == [perche, citta]
    assert search(["NOT", ["SUBJECT", "perché"]]) == [citta, plain]
    assert search(["NOT", ["OR", ["SUBJECT", "città"], ["SUBJECT", "perché"]]]) == [plain]
    assert search([]) == [perche, citta, plain]


def test_fetches_leave_seen_alone(imap_server, mail) -> None:
    user = imap_server.unique_user("fetch")
    raw = mime("read me", sender="Ana <ana@example.org>", text="hello")
    uid = seed(imap_server, user, "INBOX", raw, ["\\Flagged"])
    connections = mail(user)

    def run(
        context: MailboxContext,
    ) -> tuple[dict[int, FetchedMessage], dict[int, Envelope | None]]:
        return fetch_sources(context, [uid, uid, 999]), fetch_envelopes(context, [uid, 999])

    # Read-write, where a plain BODY[] would set \Seen.
    sources, envelopes = connections.with_mailbox("acc", "INBOX", False, run)
    assert list(sources) == [uid]
    assert sources[uid].source == raw.encode()
    assert sources[uid].flags - {"\\Recent"} == {"\\Flagged"}
    assert list(envelopes) == [uid]
    envelope = envelopes[uid]
    assert envelope is not None
    assert envelope.subject == b"read me"
    assert flags_of(imap_server, user) == {"read me": {"\\Flagged"}}


def test_sets_and_clears_answered_and_refuses_deleted(imap_server, mail) -> None:
    user = imap_server.unique_user("flags")
    uid = seed(imap_server, user, "INBOX", mime("answer me"))
    connections = mail(user)

    def store(**change: list[str]) -> None:
        connections.with_mailbox(
            "acc", "INBOX", False, lambda context: store_flags(context, [uid], **change)
        )

    store(add=["\\Answered", "\\Seen"])
    assert flags_of(imap_server, user) == {"answer me": {"\\Answered", "\\Seen"}}
    store(remove=["\\Answered"])
    assert flags_of(imap_server, user) == {"answer me": {"\\Seen"}}
    for change in ({"add": ["\\Seen", "\\Deleted"]}, {"remove": ["\\Deleted"]}):
        with pytest.raises(MailError) as refused:
            store(**change)
        assert refused.value.code == "invalid_argument"
    assert flags_of(imap_server, user) == {"answer me": {"\\Seen"}}


def test_delete_expunges_only_those_uids(imap_server, mail) -> None:
    user = imap_server.unique_user("delete")
    seed(imap_server, user, "INBOX", mime("keep me"), ["\\Deleted"])
    doomed = seed(imap_server, user, "INBOX", mime("delete me"))
    seed(imap_server, user, "INBOX", mime("and keep me"))
    connections = mail(user)

    connections.with_mailbox("acc", "INBOX", False, lambda context: delete_uids(context, [doomed]))
    assert flags_of(imap_server, user) == {"keep me": {"\\Deleted"}, "and keep me": set()}


def test_moves_and_lists_on_the_session_connection(imap_server, mail) -> None:
    """With `pool_size=1`, whatever a session needs comes from the connection it holds."""
    user = imap_server.unique_user("move")
    create_folders(imap_server, user, "Archive")
    uid = seed(imap_server, user, "INBOX", mime("file me"))
    connections = mail(user)
    delimiter = connections.delimiter("acc")
    assert delimiter == connections.delimiter("acc")

    def run(context: MailboxContext) -> Moved:
        folders = listed_folders(context)
        assert {f.path for f in folders} >= {"INBOX", "Archive", "Trash"}
        assert {f.delimiter for f in folders} == {delimiter}
        return move_uids(context, [uid], "Archive")

    moved = connections.with_mailbox("acc", "INBOX", False, run)
    archived = folder_contents(imap_server, user, "Archive")
    assert [m.subject for m in archived] == ["file me"]
    assert moved.destination == "Archive"
    assert moved.uid_validity is not None
    assert moved.new_uids == {uid: archived[0].uid}
    assert folder_contents(imap_server, user, "INBOX") == []
