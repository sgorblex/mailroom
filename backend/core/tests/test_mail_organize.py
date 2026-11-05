"""The organize tools' behaviour (`src/mcp/tools/organize-tools.it.test.ts`) at function level,
against the test Dovecot: the audit record is checked on the result's items and outcome, the
text on `organize_text`. The profile case belongs to the tool layers."""

import json
from typing import TYPE_CHECKING

import pytest
from imapclient import IMAPClient

from mailroom_core.mail import (
    MailConnections,
    MailError,
    archive_messages,
    list_messages,
    move_messages,
    organize_text,
    parse_ref,
    read_message,
    set_flags,
    trash_messages,
)
from mailroom_core.testing import (
    ImapTestServer,
    connections_for,
    create_folders,
    folder_contents,
    mime,
    raw_client,
    seed,
    test_account,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration


@pytest.fixture
def harness(imap_server: ImapTestServer) -> Iterator[object]:
    made: list[MailConnections] = []

    def make(*accounts: tuple[str, str]) -> MailConnections:
        connections = connections_for(
            imap_server, [test_account(i, user, imap_server) for i, user in accounts]
        )
        made.append(connections)
        return connections

    yield make
    for connections in made:
        connections.close_all()


def refs_in(mail: MailConnections, folder: str = "INBOX", account: str = "acc") -> list[str]:
    return [m.ref for m in list_messages(mail, account, folder, previews=False).messages]


def contents(server: ImapTestServer, user: str, folder: str) -> list[tuple[str, set[str]]]:
    return [(m.subject, set(m.flags)) for m in folder_contents(server, user, folder)]


def test_moves_messages_hands_back_new_refs_and_audits_without_the_subject(
    imap_server, harness
) -> None:
    user = imap_server.unique_user("move")
    create_folders(imap_server, user, "Archive")
    seed(imap_server, user, "INBOX", mime("quarterly report", sender="Ana <ana@example.org>"))
    mail = harness(("acc", user))
    [ref] = refs_in(mail)

    moved = move_messages(mail, [ref], "Archive")
    assert moved.outcome == "ok"
    text = organize_text(moved)
    assert text.startswith("acc/INBOX: moved 1 to Archive. New refs: ")
    [group] = moved.groups
    assert group.new_refs is not None
    [new_ref] = group.new_refs
    assert text.endswith(new_ref)
    assert read_message(mail, new_ref).subject == "quarterly report"
    with pytest.raises(MailError):
        read_message(mail, ref)
    assert len(folder_contents(imap_server, user, "Archive")) == 1

    [item] = moved.items
    assert (item.account, item.folder, item.destination, item.sender, item.result) == (
        "acc",
        "INBOX",
        "Archive",
        "ana@example.org",
        "ok",
    )
    assert "quarterly" not in json.dumps([i.record for i in moved.items])


def test_moves_refs_from_several_folders_in_one_call(imap_server, harness) -> None:
    user = imap_server.unique_user("batch")
    create_folders(imap_server, user, "Projects", "Archive")
    seed(imap_server, user, "INBOX", mime("one"))
    seed(imap_server, user, "Projects", mime("two"))
    mail = harness(("acc", user))
    refs = refs_in(mail) + refs_in(mail, "Projects")

    text = organize_text(move_messages(mail, refs, "Archive"))
    assert "acc/INBOX: moved 1 to Archive." in text
    assert "acc/Projects: moved 1 to Archive." in text
    assert len(folder_contents(imap_server, user, "Archive")) == 2


def test_refuses_anything_it_cannot_do_safely(imap_server, harness) -> None:
    user = imap_server.unique_user("refuse")
    create_folders(imap_server, user, "Projects")
    seed(imap_server, user, "Projects", mime("old"))
    other = imap_server.unique_user("refuse-other")
    seed(imap_server, other, "INBOX", mime("not yours"))
    mail = harness(("acc", user))
    [ref] = refs_in(mail, "Projects")
    foreign = refs_in(harness(("other", other)), account="other")

    for folder, message in (
        ("Nowhere", "is not a folder of acc"),
        ("Projects", "already in that folder"),
    ):
        result = move_messages(mail, [ref], folder)
        [group] = result.groups
        assert group.error is not None
        assert message in group.error.message
        assert result.outcome == "refused"
    for refs, message in ((foreign, 'Unknown account "other"'), (["garbage"], "Not a message")):
        with pytest.raises(MailError, match=message):
            move_messages(mail, refs, "INBOX")

    with raw_client(imap_server, user) as client:
        client.delete_folder("Projects")
        client.create_folder("Projects")
    seed(imap_server, user, "Projects", mime("new one with the same uid"))
    stale = move_messages(mail, [ref], "INBOX")
    [group] = stale.groups
    assert group.error is not None
    assert "out of date" in group.error.message
    assert (stale.outcome, [i.result for i in stale.items]) == ("refused", ["stale_reference"])
    assert len(folder_contents(imap_server, user, "Projects")) == 1


def test_a_batch_where_one_folder_fails_is_partial(imap_server, harness) -> None:
    user = imap_server.unique_user("partial")
    create_folders(imap_server, user, "Projects", "Archive")
    seed(imap_server, user, "INBOX", mime("fine"))
    seed(imap_server, user, "Projects", mime("gone soon"))
    mail = harness(("acc", user))
    inbox = refs_in(mail)
    projects = refs_in(mail, "Projects")
    # Another client moves the Projects message away first.
    move_messages(mail, projects, "Archive")

    batch = move_messages(mail, inbox + projects, "Archive")
    assert (batch.succeeded, batch.failed, batch.outcome) == (1, 1, "partial")
    text = organize_text(batch)
    assert "acc/INBOX: moved 1 to Archive." in text
    assert (
        "acc/Projects: No such message or part. 1 of these messages are no longer in Projects"
        in text
    )


def test_refuses_drafts_and_sent_as_destinations(imap_server, harness) -> None:
    user = imap_server.unique_user("drafts")
    seed(imap_server, user, "INBOX", mime("please send me"))
    mail = harness(("acc", user))
    [ref] = refs_in(mail)
    for folder in ("Drafts", "Sent"):
        [group] = move_messages(mail, [ref], folder).groups
        assert group.error is not None
        assert "cannot be moved into Drafts or Sent" in group.error.message
    assert len(folder_contents(imap_server, user, "INBOX")) == 1


def test_moves_into_junk_saying_the_spam_filter_learns_from_it(imap_server, harness) -> None:
    user = imap_server.unique_user("junk")
    seed(imap_server, user, "INBOX", mime("win a prize"))
    mail = harness(("acc", user))
    [ref] = refs_in(mail)
    moved = move_messages(mail, [ref], "Junk")
    assert "The spam filter learns them as spam." in organize_text(moved)
    assert [i.junk for i in moved.items] == ["spam"]

    [junk_ref] = refs_in(mail, "Junk")
    back = organize_text(move_messages(mail, [junk_ref], "inbox"))
    assert "moved 1 to INBOX. " in back
    assert "The spam filter learns them as not spam." in back

    # Junk to Trash trains nothing on Mailu, so it claims nothing.
    seed(imap_server, user, "Junk", mime("more spam"))
    [spam_ref] = refs_in(mail, "Junk")
    binned = move_messages(mail, [spam_ref], "Trash")
    assert "spam filter" not in organize_text(binned)
    assert [i.junk for i in binned.items] == [None]


def test_trashes_without_deleting_anything_and_refuses_what_is_in_trash(
    imap_server, harness
) -> None:
    user = imap_server.unique_user("trash")
    seed(imap_server, user, "INBOX", mime("keep me"), ["\\Deleted"])
    seed(imap_server, user, "INBOX", mime("bin me"))
    mail = harness(("acc", user))
    bin_ref = refs_in(mail)[0]  # newest first

    trashed = trash_messages(mail, [bin_ref])
    assert organize_text(trashed).startswith("acc/INBOX: moved 1 to Trash.")
    # Another client's \Deleted message is still there: no EXPUNGE reached it.
    assert contents(imap_server, user, "INBOX") == [("keep me", {"\\Deleted"})]
    assert [s for s, _ in contents(imap_server, user, "Trash")] == ["bin me"]

    [in_trash] = refs_in(mail, "Trash")
    again = trash_messages(mail, [in_trash])
    assert again.groups[0].error is not None
    assert "never deletes permanently" in again.groups[0].error.message
    assert (trashed.outcome, again.outcome) == ("ok", "refused")


def test_without_move_expunges_only_the_moved_uids(imap_server, harness, monkeypatch) -> None:
    user = imap_server.unique_user("uidplus")
    create_folders(imap_server, user, "Archive")
    seed(imap_server, user, "INBOX", mime("keep me"), ["\\Deleted"])
    seed(imap_server, user, "INBOX", mime("file me"))
    seed(imap_server, user, "INBOX", mime("and me"))
    has_capability = IMAPClient.has_capability
    monkeypatch.setattr(
        IMAPClient,
        "has_capability",
        lambda self, name: name.upper() != "MOVE" and has_capability(self, name),
    )
    mail = harness(("acc", user))
    refs = refs_in(mail)[:2]

    moved = move_messages(mail, refs, "Archive")
    assert moved.outcome == "ok"
    assert contents(imap_server, user, "INBOX") == [("keep me", {"\\Deleted"})]
    archived = folder_contents(imap_server, user, "Archive")
    assert sorted(m.subject for m in archived) == ["and me", "file me"]
    assert not any("\\Deleted" in m.flags for m in archived)
    [group] = moved.groups
    assert group.new_refs is not None
    assert sorted(read_message(mail, r).subject for r in group.new_refs) == ["and me", "file me"]


def test_archives_to_the_archive_folder(imap_server, harness) -> None:
    user = imap_server.unique_user("archive")
    seed(imap_server, user, "INBOX", mime("file me"))
    mail = harness(("acc", user))
    [ref] = refs_in(mail)
    [missing] = archive_messages(mail, [ref]).groups
    assert missing.error is not None
    assert (missing.error.code, missing.error.detail) == (
        "unsupported",
        "acc has no Archive folder.",
    )

    create_folders(imap_server, user, "Archive")
    archived = archive_messages(mail, [ref])
    assert organize_text(archived).startswith("acc/INBOX: moved 1 to Archive. New refs: ")
    assert [s for s, _ in contents(imap_server, user, "Archive")] == ["file me"]


def test_marks_messages_read_unread_and_flagged_idempotently(imap_server, harness) -> None:
    user = imap_server.unique_user("flag")
    seed(imap_server, user, "INBOX", mime("flag me"))
    mail = harness(("acc", user))
    [ref] = refs_in(mail)

    read = set_flags(mail, [ref], seen=True, flagged=True)
    assert f"{ref} read, flagged" in organize_text(read)
    assert contents(imap_server, user, "INBOX") == [("flag me", {"\\Flagged", "\\Seen"})]
    assert set_flags(mail, [ref], seen=True, flagged=True).outcome == "ok"

    unread = set_flags(mail, [ref], seen=False)
    assert organize_text(unread) == f"acc/INBOX: updated 1: {ref} unread, flagged"
    assert contents(imap_server, user, "INBOX") == [("flag me", {"\\Flagged"})]
    assert parse_ref(unread.groups[0].flags[0].ref) == parse_ref(ref)

    with pytest.raises(MailError, match="Give `seen`"):
        set_flags(mail, [ref])
