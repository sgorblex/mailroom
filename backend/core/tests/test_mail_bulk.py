"""Counting and acting on every message a search matches, against the test Dovecot: the count
checked on each call, a batch at a time, newest first, and an audit item per message."""

import imaplib
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from imapclient import IMAPClient

from mailroom_core.mail import (
    ALL_FOLDERS,
    AuditItem,
    BulkFlag,
    BulkMove,
    BulkTrash,
    MailConnections,
    MailError,
    MatchCountError,
    SearchQuery,
    act_on_matching,
    count_matching,
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
    from collections.abc import Iterator

    from mailroom_core.mail import Folder

pytestmark = pytest.mark.integration


@pytest.fixture
def harness(imap_server: ImapTestServer) -> Iterator[object]:
    made: list[MailConnections] = []

    def make(*accounts: tuple[int, str], passwords: dict[int, str] | None = None):
        connections = connections_for(
            imap_server, [test_account(i, user, imap_server) for i, user in accounts], passwords
        )
        made.append(connections)
        return connections

    yield make
    for connections in made:
        connections.close_all()


def subjects(server: ImapTestServer, user: str, folder: str) -> list[tuple[str, bool]]:
    return [(m.subject, "\\Seen" in m.flags) for m in folder_contents(server, user, folder)]


def test_flags_what_matches_a_batch_at_a_time_with_the_count_confirmed(
    imap_server, harness, monkeypatch
) -> None:
    monkeypatch.setattr("mailroom_core.mail.bulk.BULK_BATCH", 2)
    user = imap_server.unique_user("bulkflag")
    create_folders(imap_server, user, "Lists")
    for subject in ("news 1", "news 2", "news 3"):
        seed(imap_server, user, "Lists", mime(subject))
    seed(imap_server, user, "Lists", mime("news read"), flags=("\\Seen",))
    seed(imap_server, user, "Lists", mime("other"))
    mail = harness((1, user))
    news = SearchQuery(subject="news")
    read = BulkFlag(seen=True)
    items: list[AuditItem] = []

    assert count_matching(mail, [1], "Lists", news).total == 4
    assert count_matching(mail, [1], "Lists", news, action=read).total == 3

    with pytest.raises(MatchCountError) as changed:
        act_on_matching(mail, [1], "Lists", news, read, expected=4, note=items.append)
    assert (changed.value.code, changed.value.matching) == ("count_changed", 3)
    assert [i.record for i in items] == [
        {"account": 1, "folder": "Lists", "messages": 3, "result": "count_changed"}
    ]
    items.clear()

    first = act_on_matching(mail, [1], "Lists", news, read, expected=3, note=items.append)
    assert (first.done, first.remaining, first.failures) == (2, 1, ())
    [group] = first.groups
    assert (group.ok, group.count, group.flags) == (True, 2, ())
    assert subjects(imap_server, user, "Lists") == [
        ("news 1", False),
        ("news 2", True),
        ("news 3", True),
        ("news read", True),
        ("other", False),
    ]
    assert [(i.uid, i.seen, i.result) for i in items] == [(2, True, "ok"), (3, True, "ok")]

    last = act_on_matching(mail, [1], "Lists", news, read, expected=1, note=items.append)
    assert (last.done, last.remaining) == (1, 0)
    assert [seen for _, seen in subjects(imap_server, user, "Lists")] == [
        True,
        True,
        True,
        True,
        False,
    ]
    done = act_on_matching(mail, [1], "Lists", news, read, expected=0, note=items.append)
    assert (done.done, done.remaining, done.groups) == (0, 0, ())


def test_moves_and_trashes_what_matches_across_subfolders(imap_server, harness) -> None:
    user = imap_server.unique_user("bulkmove")
    create_folders(imap_server, user, "Lists", "Lists/Sub", "Done")
    seed(imap_server, user, "Lists", mime("report a"))
    seed(imap_server, user, "Lists", mime("keep"))
    seed(imap_server, user, "Lists/Sub", mime("report b"))
    mail = harness((1, user))
    reports = SearchQuery(subject="report")
    items: list[AuditItem] = []

    moved = act_on_matching(
        mail,
        [1],
        "Lists",
        reports,
        BulkMove("Done"),
        expected=2,
        subfolders=True,
        note=items.append,
    )
    assert (moved.done, moved.remaining) == (2, 0)
    assert [(g.folder, g.count, g.ok) for g in moved.groups] == [
        ("Lists", 1, True),
        ("Lists/Sub", 1, True),
    ]
    assert sorted(s for s, _ in subjects(imap_server, user, "Done")) == ["report a", "report b"]
    assert [(i.folder, i.destination, i.result) for i in items] == [
        ("Lists", "Done", "ok"),
        ("Lists/Sub", "Done", "ok"),
    ]

    trashed = act_on_matching(
        mail, [1], "Done", reports, BulkTrash(), expected=2, note=items.append
    )
    assert (trashed.done, trashed.remaining) == (2, 0)
    assert subjects(imap_server, user, "Done") == []
    assert sorted(s for s, _ in subjects(imap_server, user, "Trash")) == ["report a", "report b"]


def test_refuses_what_would_still_match_or_is_too_many(imap_server, harness, monkeypatch) -> None:
    user = imap_server.unique_user("bulkrefuse")
    create_folders(imap_server, user, "Lists", "Lists/Sub")
    for subject in ("one", "two", "three"):
        seed(imap_server, user, "Lists", mime(subject))
    mail = harness((1, user))
    items: list[AuditItem] = []
    everything = SearchQuery()

    for folder, action, subfolders in (
        ("Lists/Sub", BulkMove("Lists/Sub"), False),
        ("Trash", BulkTrash(), False),
        ("Lists", BulkMove("Sent"), False),
        ("Lists", BulkFlag(), False),
    ):
        with pytest.raises(MailError) as refused:
            act_on_matching(
                mail,
                [1],
                folder,
                everything,
                action,
                expected=3,
                subfolders=subfolders,
                note=items.append,
            )
        assert refused.value.code == "invalid_argument", action
    with pytest.raises(MailError) as missing:
        count_matching(mail, [1], "Gone", everything)
    assert missing.value.code == "mailbox_missing"

    monkeypatch.setattr("mailroom_core.mail.bulk.BULK_MAX", 2)
    with pytest.raises(MatchCountError) as too_many:
        act_on_matching(mail, [1], "Lists", everything, BulkTrash(), expected=3, note=items.append)
    assert (too_many.value.code, too_many.value.matching) == ("too_many", 3)
    assert [(i.folder, i.messages, i.result) for i in items] == [
        ("Lists/Sub", None, "invalid_argument"),
        ("Trash", None, "invalid_argument"),
        ("Lists", None, "invalid_argument"),
        ("Lists", None, "invalid_argument"),
        ("Lists", 3, "too_many"),
    ]
    assert len(subjects(imap_server, user, "Lists")) == 3


def test_a_move_over_several_folders_leaves_its_destination_out(imap_server, harness) -> None:
    user = imap_server.unique_user("bulkdest")
    create_folders(imap_server, user, "Lists", "Lists/Sub", "Done")
    seed(imap_server, user, "INBOX", mime("report a"))
    seed(imap_server, user, "Lists", mime("report b"))
    seed(imap_server, user, "Lists/Sub", mime("report c"))
    seed(imap_server, user, "Done", mime("report done"))
    mail = harness((1, user))
    reports = SearchQuery(subject="report")
    items: list[AuditItem] = []

    # Over every folder: the destination's own match is not counted, nor moved again.
    assert count_matching(mail, [1], ALL_FOLDERS, reports).total == 4
    done = BulkMove("Done")
    assert count_matching(mail, [1], ALL_FOLDERS, reports, action=done).total == 3
    with pytest.raises(MatchCountError) as changed:
        act_on_matching(mail, [1], ALL_FOLDERS, reports, done, expected=4, note=items.append)
    assert (changed.value.code, changed.value.matching) == ("count_changed", 3)
    moved = act_on_matching(mail, [1], ALL_FOLDERS, reports, done, expected=3, note=items.append)
    assert (moved.done, moved.remaining) == (3, 0)
    assert sorted(s for s, _ in subjects(imap_server, user, "Done")) == [
        "report a",
        "report b",
        "report c",
        "report done",
    ]
    assert count_matching(mail, [1], ALL_FOLDERS, reports, action=done).total == 0

    # With subfolders, into one of them: the others' matches go there.
    seed(imap_server, user, "Lists", mime("report d"))
    sub = BulkMove("Lists/Sub")
    assert count_matching(mail, [1], "Lists", reports, subfolders=True, action=sub).total == 1
    into_sub = act_on_matching(
        mail, [1], "Lists", reports, sub, expected=1, subfolders=True, note=items.append
    )
    assert (into_sub.done, into_sub.remaining) == (1, 0)
    assert subjects(imap_server, user, "Lists/Sub") == [("report d", False)]


def test_a_move_over_every_folder_of_an_account_with_all_mail_reads_its_labels(
    imap_server, harness, monkeypatch
) -> None:
    """On Gmail a search over every folder reads All Mail alone; a move cannot take mail out of
    it, so it reads the folders a message can leave: the inbox and the labels, not All Mail,
    Starred, Important or the destination."""
    user = imap_server.unique_user("bulkgmail")
    create_folders(imap_server, user, "All Mail", "Starred", "Important", "Work", "Done")
    roles = {"All Mail": "all", "Starred": "flagged"}
    listed = MailConnections.folders

    def as_gmail(self: MailConnections, account_id: int, **kwargs: bool) -> list[Folder]:
        return [
            replace(f, role=roles[f.path])
            if f.path in roles
            else replace(f, flags=(*f.flags, "\\Important"))
            if f.path == "Important"
            else f
            for f in listed(self, account_id, **kwargs)
        ]

    monkeypatch.setattr(MailConnections, "folders", as_gmail)
    for folder in ("INBOX", "Work", "All Mail", "All Mail", "Starred", "Important"):
        seed(imap_server, user, folder, mime(f"report in {folder}"))
    seed(imap_server, user, "All Mail", mime("report archived"))
    mail = harness((1, user))
    reports = SearchQuery(subject="report")
    done = BulkMove("Done")
    items: list[AuditItem] = []

    assert count_matching(mail, [1], ALL_FOLDERS, reports).total == 3
    assert count_matching(mail, [1], ALL_FOLDERS, reports, action=done).total == 2
    moved = act_on_matching(mail, [1], ALL_FOLDERS, reports, done, expected=2, note=items.append)
    assert (moved.done, moved.remaining) == (2, 0)
    assert [(g.folder, g.count) for g in moved.groups] == [("INBOX", 1), ("Work", 1)]
    assert sorted(s for s, _ in subjects(imap_server, user, "Done")) == [
        "report in INBOX",
        "report in Work",
    ]
    for kept in ("All Mail", "Starred", "Important"):
        assert subjects(imap_server, user, kept), kept

    # A search of All Mail itself still cannot move out of it.
    with pytest.raises(MailError) as refused:
        count_matching(mail, [1], "All Mail", reports, action=done)
    assert refused.value.code == "invalid_argument"


def test_reports_a_failing_account_and_acts_on_the_others(imap_server, harness) -> None:
    good = imap_server.unique_user("bulkgood")
    seed(imap_server, good, "INBOX", mime("match me"))
    mail = harness((1, good), (2, imap_server.unique_user("bulkbad")), passwords={2: "wrong"})
    query = SearchQuery(subject="match")
    items: list[AuditItem] = []

    counted = count_matching(mail, [1, 2], ALL_FOLDERS, query)
    assert counted.total == 1
    assert [(f.account, f.folder, f.code) for f in counted.failures] == [(2, None, "auth_failed")]

    flagged = act_on_matching(
        mail, [1, 2], "INBOX", query, BulkFlag(flagged=True), expected=1, note=items.append
    )
    assert (flagged.done, flagged.remaining) == (1, 0)
    assert [(f.account, f.code) for f in flagged.failures] == [(2, "auth_failed")]
    assert [(i.account, i.flagged, i.result) for i in items] == [
        (2, None, "auth_failed"),
        (1, True, "ok"),
    ]


def test_never_moves_again_what_was_copied_but_not_removed(
    imap_server, harness, monkeypatch
) -> None:
    user = imap_server.unique_user("bulkcopied")
    create_folders(imap_server, user, "Lists", "Done")
    for subject in ("report a", "report b"):
        seed(imap_server, user, "Lists", mime(subject))
    has_capability = IMAPClient.has_capability
    monkeypatch.setattr(
        IMAPClient,
        "has_capability",
        lambda self, name: name.upper() != "MOVE" and has_capability(self, name),
    )

    def fails(self: IMAPClient, *args: object) -> None:
        raise imaplib.IMAP4.error("no")

    monkeypatch.setattr(IMAPClient, "uid_expunge", fails)
    mail = harness((1, user))
    reports = SearchQuery(subject="report")
    move = BulkMove("Done")
    items: list[AuditItem] = []

    first = act_on_matching(mail, [1], "Lists", reports, move, expected=2, note=items.append)
    [group] = first.groups
    assert (group.ok, group.destination, first.done, first.remaining) == (False, "Done", 0, 0)
    assert len(subjects(imap_server, user, "Done")) == 2

    # Marked \Deleted in Lists, still there: neither counted nor copied again.
    assert count_matching(mail, [1], "Lists", reports, action=move).total == 0
    again = act_on_matching(mail, [1], "Lists", reports, move, expected=0, note=items.append)
    assert (again.done, again.remaining, again.groups) == (0, 0, ())
    assert len(subjects(imap_server, user, "Done")) == 2
