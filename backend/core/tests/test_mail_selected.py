"""The operations on a selected folder against the test Dovecot: searches with non-ASCII text in
groups, fetches that leave `\\Seen` alone, the flags they may set, and the one permanent delete
sparing whatever else is marked `\\Deleted`."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from mailroom_core.mail import (
    MailboxContext,
    MailConnections,
    MailError,
    Moved,
    append_raw,
    delete_uids,
    fetch_envelopes,
    fetch_source,
    fetch_sources,
    listed_folders,
    message_id_uids,
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

    from mailroom_core.mail import FetchedMessage, MessageSource

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


def test_appends_a_message_as_it_was_with_its_flags_and_arrival_date(imap_server, mail) -> None:
    user = imap_server.unique_user("append")
    raw = mime("moved here", text="hello").encode()
    arrived = datetime(2024, 3, 1, 9, 30, tzinfo=UTC)
    connections = mail(user)

    def run(context: MailboxContext) -> tuple[int | None, int | None]:
        return context.uid_next, append_raw(
            context, raw, ["\\Seen", "\\Flagged"], internal_date=arrived
        )

    before, uid = connections.with_mailbox("acc", "INBOX", False, run)
    assert uid is not None
    assert before is not None
    assert uid >= before
    stored = connections.with_mailbox(
        "acc", "INBOX", True, lambda context: fetch_source(context, uid, len(raw))
    )
    assert stored.source == raw
    assert stored.flags - {"\\Recent"} == {"\\Seen", "\\Flagged"}
    assert stored.internal_date is not None
    assert stored.internal_date.astimezone(UTC) == arrived


def test_finds_a_message_id_that_needs_quoting(imap_server, mail) -> None:
    user = imap_server.unique_user("message-id")
    odd = '<a(b)"c"{5}@example.org>'
    first = seed(imap_server, user, "INBOX", mime("one", message_id=odd))
    second = seed(imap_server, user, "INBOX", mime("two", message_id=odd))
    seed(imap_server, user, "INBOX", mime("other"))
    connections = mail(user)

    def find(since_uid: int) -> list[int]:
        return connections.with_mailbox(
            "acc",
            "INBOX",
            True,
            lambda context: sorted(message_id_uids(context, odd, since_uid=since_uid)),
        )

    assert find(0) == [first, second]
    assert find(second) == [second]


def test_fetches_a_whole_source_up_to_a_cap(imap_server, mail) -> None:
    user = imap_server.unique_user("source")
    raw = mime("capped", text="x" * 200)
    arrived = datetime(2023, 12, 24, 18, 0, tzinfo=UTC)
    uid = seed(imap_server, user, "INBOX", raw, ["\\Answered"], arrived)
    connections = mail(user)

    def fetch(cap: int) -> MessageSource:
        return connections.with_mailbox(
            "acc", "INBOX", False, lambda context: fetch_source(context, uid, cap)
        )

    size = len(raw.encode())
    whole = fetch(size)
    assert whole.source == raw.encode()
    assert whole.flags - {"\\Recent"} == {"\\Answered"}
    assert whole.internal_date is not None
    assert whole.internal_date.astimezone(UTC) == arrived
    with pytest.raises(MailError) as refused:
        fetch(size - 1)
    assert refused.value.code == "too_large"
    with pytest.raises(MailError) as gone:
        connections.with_mailbox(
            "acc", "INBOX", True, lambda context: fetch_source(context, 999, size)
        )
    assert gone.value.code == "not_found"
    assert flags_of(imap_server, user) == {"capped": {"\\Answered"}}


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
