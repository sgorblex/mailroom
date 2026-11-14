"""Moving messages to another account against the test Dovecot, two users standing for two
accounts: the copy keeps the source's bytes, flags and arrival date; the original goes to
Trash or, when asked, for good; nothing is written when a refusal applies; a move repeated after
a removal that failed copies nothing twice."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from mailroom_core.mail import (
    MailConnections,
    MailError,
    list_messages,
    move_to_account,
    organize_text,
)
from mailroom_core.mail import transfer as transfer_module
from mailroom_core.testing import (
    ImapTestServer,
    connections_for,
    folder_contents,
    mime,
    raw_client,
    seed,
    test_account,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.integration

CAP = 10 * 1024 * 1024
ARRIVED = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)


@pytest.fixture
def users(imap_server: ImapTestServer) -> tuple[str, str]:
    return imap_server.unique_user("from"), imap_server.unique_user("to")


@pytest.fixture
def mail(imap_server: ImapTestServer, users: tuple[str, str]) -> Iterator[MailConnections]:
    source, target = users
    connections = connections_for(
        imap_server,
        [test_account("a", source, imap_server), test_account("b", target, imap_server)],
    )
    yield connections
    connections.close_all()


def refs_in(mail: MailConnections, account: str = "a", folder: str = "INBOX") -> list[str]:
    return [m.ref for m in list_messages(mail, account, folder, previews=False).messages]


def subjects(server: ImapTestServer, user: str, folder: str) -> list[str]:
    return sorted(m.subject for m in folder_contents(server, user, folder))


def arrival(server: ImapTestServer, user: str, folder: str) -> list[datetime]:
    with raw_client(server, user) as client:
        client.normalise_times = False  # aware, at the server's own offset
        client.select_folder(folder, readonly=True)
        fetched = client.fetch(client.search(), ["INTERNALDATE"])
    return [data[b"INTERNALDATE"].astimezone(UTC) for data in fetched.values()]


def test_moves_a_message_with_its_flags_and_date_and_trashes_the_original(
    imap_server, users, mail
) -> None:
    source, target = users
    raw = mime("quarterly report", sender="Ana <ana@example.org>", text="numbers")
    seed(imap_server, source, "INBOX", raw, ["\\Seen", "\\Flagged"], ARRIVED)
    [ref] = refs_in(mail)

    result = move_to_account(mail, [ref], "b", "INBOX", removal="trash", max_bytes=CAP)

    assert result.outcome == "ok"
    [group] = result.groups
    assert (group.count, group.destination, group.destination_account) == (1, "INBOX", "b")
    assert group.new_refs is not None
    assert organize_text(result).startswith("a/INBOX: moved 1 to b/INBOX. New refs: ")
    [copy] = folder_contents(imap_server, target, "INBOX")
    assert copy.source == raw.encode()
    assert set(copy.flags) - {"\\Recent"} == {"\\Seen", "\\Flagged"}
    assert arrival(imap_server, target, "INBOX") == [ARRIVED]
    assert subjects(imap_server, source, "INBOX") == []
    assert subjects(imap_server, source, "Trash") == ["quarterly report"]
    [from_item, to_item] = sorted(result.items, key=lambda item: item.account)
    assert from_item.record | {"uid": 0} == {
        "account": "a",
        "folder": "INBOX",
        "uid": 0,
        "message_id": from_item.message_id,
        "from": "ana@example.org",
        "subject_sha256": from_item.subject_sha256,
        "destination": "INBOX",
        "destination_account": "b",
        "removed": "trashed",
        "result": "ok",
    }
    assert (to_item.account, to_item.folder, to_item.result) == ("b", "INBOX", "ok")
    assert (to_item.source_account, to_item.source_folder) == ("a", "INBOX")
    assert to_item.message_id == from_item.message_id


def test_deletes_the_original_for_good_and_nothing_else(imap_server, users, mail) -> None:
    source, target = users
    seed(imap_server, source, "INBOX", mime("someone else's"), ["\\Deleted"])
    seed(imap_server, source, "INBOX", mime("move me"))
    seed(imap_server, source, "INBOX", mime("and me"))
    refs = refs_in(mail)[:2]  # newest first

    result = move_to_account(mail, refs, "b", "INBOX", removal="delete", max_bytes=CAP)

    assert result.outcome == "ok"
    assert subjects(imap_server, target, "INBOX") == ["and me", "move me"]
    assert subjects(imap_server, source, "INBOX") == ["someone else's"]
    assert subjects(imap_server, source, "Trash") == []
    assert {item.removed for item in result.items if item.account == "a"} == {"expunged"}


def test_a_gmail_source_deletes_for_good_from_its_trash(
    imap_server, users, mail, monkeypatch
) -> None:
    """On Gmail an expunge from a label folder only drops the label: the original goes to Trash
    and is expunged there."""
    source, _ = users
    seed(imap_server, source, "INBOX", mime("gmail one"))
    seed(imap_server, source, "Trash", mime("already binned"))
    real = MailConnections.capabilities

    def gmail_a(self: MailConnections, account: str) -> object:
        found = real(self, account)
        return replace(found, gmail=True) if account == "a" else found

    monkeypatch.setattr(MailConnections, "capabilities", gmail_a)
    [ref] = refs_in(mail)

    result = move_to_account(mail, [ref], "b", "INBOX", removal="delete", max_bytes=CAP)

    assert result.outcome == "ok"
    assert subjects(imap_server, source, "INBOX") == []
    assert subjects(imap_server, source, "Trash") == ["already binned"]


def test_moves_in_chunks_within_the_size_limit(imap_server, users, mail) -> None:
    source, target = users
    for subject in ("one", "two", "three"):
        seed(imap_server, source, "INBOX", mime(subject, text="x" * 2000))
    refs = refs_in(mail)
    biggest = max(len(m.source) for m in folder_contents(imap_server, source, "INBOX"))

    result = move_to_account(mail, refs, "b", "INBOX", removal="trash", max_bytes=biggest)

    assert result.outcome == "ok"
    assert subjects(imap_server, target, "INBOX") == ["one", "three", "two"]
    assert subjects(imap_server, source, "Trash") == ["one", "three", "two"]


def test_repeating_a_move_whose_removal_failed_copies_nothing_twice(
    imap_server, users, mail, monkeypatch
) -> None:
    source, target = users
    seed(imap_server, source, "INBOX", mime("stuck"))
    [ref] = refs_in(mail)

    def fails(*_args: object) -> None:
        raise MailError("server_error")

    monkeypatch.setattr(transfer_module, "move_uids", fails)
    stuck = move_to_account(mail, [ref], "b", "INBOX", removal="trash", max_bytes=CAP)
    monkeypatch.undo()

    [group] = stuck.groups
    assert group.error is not None
    assert "in both" in group.error.message
    assert group.new_refs is not None
    assert group.new_refs[0] in group.error.message
    assert stuck.outcome == "partial"  # the copy was made
    source_item = next(item for item in stuck.items if item.account == "a")
    assert (source_item.copied, source_item.removed) == (True, "failed")
    assert subjects(imap_server, target, "INBOX") == ["stuck"]
    assert subjects(imap_server, source, "INBOX") == ["stuck"]

    again = move_to_account(mail, [ref], "b", "INBOX", removal="trash", max_bytes=CAP)

    assert again.outcome == "ok"
    assert subjects(imap_server, target, "INBOX") == ["stuck"]
    assert subjects(imap_server, source, "Trash") == ["stuck"]


def test_a_message_already_in_the_target_is_not_copied_again(imap_server, users, mail) -> None:
    source, target = users
    raw = mime("delivered to both", message_id="<both@example.org>")
    seed(imap_server, source, "INBOX", raw)
    seed(imap_server, target, "INBOX", raw)
    [ref] = refs_in(mail)

    result = move_to_account(mail, [ref], "b", "INBOX", removal="trash", max_bytes=CAP)

    assert result.outcome == "ok"
    assert subjects(imap_server, target, "INBOX") == ["delivered to both"]
    assert subjects(imap_server, source, "Trash") == ["delivered to both"]


def test_refuses_what_it_cannot_do_safely_before_writing_anything(
    imap_server, users, mail, monkeypatch
) -> None:
    source, target = users
    seed(imap_server, source, "INBOX", mime("fine"))
    [ref] = refs_in(mail)

    def refused(call: Callable[[], object], message: str) -> None:
        with pytest.raises(MailError, match=message):
            call()

    refused(
        lambda: move_to_account(mail, [ref], "a", "Trash", removal="trash", max_bytes=CAP),
        "in a already",
    )
    refused(
        lambda: move_to_account(mail, [ref], "c", "INBOX", removal="trash", max_bytes=CAP),
        'Unknown account "c"',
    )
    refused(
        lambda: move_to_account(mail, [ref], "b", "Nowhere", removal="trash", max_bytes=CAP),
        "is not a folder of b",
    )
    refused(
        lambda: move_to_account(mail, [ref], "b", "Drafts", removal="trash", max_bytes=CAP),
        "into Drafts or Sent",
    )
    real = MailConnections.capabilities
    monkeypatch.setattr(
        MailConnections,
        "capabilities",
        lambda self, account: replace(real(self, account), uidplus=False),
    )
    refused(
        lambda: move_to_account(mail, [ref], "b", "INBOX", removal="delete", max_bytes=CAP),
        "cannot delete exactly these messages",
    )
    monkeypatch.undo()
    assert subjects(imap_server, target, "INBOX") == []
    assert subjects(imap_server, source, "INBOX") == ["fine"]


def test_refuses_a_folder_with_a_message_it_could_not_confirm_or_too_large(
    imap_server, users, mail
) -> None:
    source, target = users
    seed(imap_server, source, "INBOX", mime("big", text="y" * 5000))
    unnamed = b"From: ana@example.org\r\nSubject: no id\r\n\r\nhello\r\n"
    seed(imap_server, source, "Junk", unnamed)
    [big] = refs_in(mail)
    [no_id] = refs_in(mail, folder="Junk")

    result = move_to_account(mail, [big, no_id], "b", "INBOX", removal="trash", max_bytes=1000)

    errors = {group.folder: group.error for group in result.groups}
    assert errors["INBOX"] is not None
    assert errors["INBOX"].code == "too_large"
    assert errors["Junk"] is not None
    assert errors["Junk"].code == "unsupported"
    assert "no Message-ID" in errors["Junk"].message
    assert result.outcome == "refused"
    assert subjects(imap_server, target, "INBOX") == []
    assert subjects(imap_server, source, "INBOX") == ["big"]
    assert subjects(imap_server, source, "Junk") == ["no id"]


def test_messages_sharing_a_message_id_each_get_a_copy(imap_server, users, mail) -> None:
    """A list's copy and the direct one share the Message-ID: in two folders, or chunks, of
    one call, neither may pass for the other's copy."""
    source, target = users
    seed(imap_server, source, "INBOX", mime("direct", message_id="<same@example.org>"))
    seed(
        imap_server,
        source,
        "Junk",
        mime("via the list", message_id="<same@example.org>", text="[list footer]"),
    )
    refs = [refs_in(mail)[0], refs_in(mail, folder="Junk")[0]]

    result = move_to_account(mail, refs, "b", "INBOX", removal="delete", max_bytes=CAP)

    assert result.outcome == "ok"
    assert subjects(imap_server, target, "INBOX") == ["direct", "via the list"]
    assert subjects(imap_server, source, "INBOX") == []
    assert subjects(imap_server, source, "Junk") == []


def test_a_different_message_with_the_same_id_in_the_target_is_no_copy(
    imap_server, users, mail
) -> None:
    source, target = users
    seed(imap_server, source, "INBOX", mime("the original", message_id="<same@example.org>"))
    seed(
        imap_server,
        target,
        "INBOX",
        mime("a look-alike", message_id="<same@example.org>", text="something else entirely"),
    )
    [ref] = refs_in(mail)

    result = move_to_account(mail, [ref], "b", "INBOX", removal="delete", max_bytes=CAP)

    assert result.outcome == "ok"
    assert subjects(imap_server, target, "INBOX") == ["a look-alike", "the original"]


def test_an_unconfirmed_copy_leaves_its_original_where_it_was(
    imap_server, users, mail, monkeypatch
) -> None:
    source, target = users
    seed(imap_server, source, "INBOX", mime("first"))
    seed(imap_server, source, "INBOX", mime("second"))
    refs = refs_in(mail)
    real = transfer_module.append_raw
    calls: list[int] = []

    def second_fails(*args: object, **kwargs: object) -> object:
        calls.append(1)
        if len(calls) == 2:
            raise MailError("server_error")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(transfer_module, "append_raw", second_fails)
    result = move_to_account(mail, refs, "b", "INBOX", removal="trash", max_bytes=CAP)
    monkeypatch.undo()

    [group] = result.groups
    assert group.error is not None
    assert group.count == 1
    assert "Moved 1 of 2" in group.error.message
    assert "without being confirmed" in group.error.message
    assert result.outcome == "partial"
    assert subjects(imap_server, target, "INBOX") == ["first"]
    assert subjects(imap_server, source, "INBOX") == ["second"]
    assert subjects(imap_server, source, "Trash") == ["first"]
    noted = [item.uid for item in result.items if item.account == "a"]
    assert len(noted) == len(set(noted)) == 2
