"""The operations on a selected folder over a fake client: what each sends, what each refuses
before sending anything, the move without MOVE, COPYUID, and SEARCH groups ending in a literal."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from imapclient.imapclient import _literal

from mailroom_core.mail import (
    CopiedNotRemovedError,
    Folder,
    MailboxContext,
    MailError,
    Moved,
    delete_uids,
    move_target,
    move_uids,
    search_arguments,
    special_folder_in,
    store_flags,
)
from mailroom_core.mail.selected import _copy_uids

if TYPE_CHECKING:
    from collections.abc import Callable


class FakeClient:
    """The selected folder's connection, recording every command; COPYUID as `copyuid` says."""

    def __init__(self, capabilities: set[str], copyuid: bytes | None = None) -> None:
        self.capabilities = capabilities
        self.copyuid = copyuid
        self.calls: list[tuple[object, ...]] = []
        self.fail: dict[str, Exception] = {}
        self._imap = SimpleNamespace(untagged_responses={})

    def has_capability(self, name: str) -> bool:
        return name in self.capabilities

    def _record(self, name: str, *args: object) -> None:
        self.calls.append((name, *args))
        if name in self.fail:
            raise self.fail[name]

    def _copied(self, name: str, uids: list[int], folder: str) -> None:
        self._record(name, uids, folder)
        if self.copyuid is not None:
            self._imap.untagged_responses.setdefault("COPYUID", []).append(self.copyuid)

    def move(self, uids: list[int], folder: str) -> None:
        self._copied("move", uids, folder)

    def copy(self, uids: list[int], folder: str) -> None:
        self._copied("copy", uids, folder)

    def add_flags(self, uids: list[int], flags: list[bytes], *, silent: bool) -> None:
        self._record("add_flags", uids, flags)

    def remove_flags(self, uids: list[int], flags: list[bytes], *, silent: bool) -> None:
        self._record("remove_flags", uids, flags)

    def uid_expunge(self, uids: list[int]) -> None:
        self._record("uid_expunge", uids)


def selected(client: FakeClient) -> MailboxContext:
    return MailboxContext(
        cast("Any", client),
        "INBOX",
        False,
        uid_validity=1,
        exists=3,
        uid_next=None,
        account="acc",
    )


def refused(call: Callable[[], object], code: str = "invalid_argument") -> MailError:
    with pytest.raises(MailError) as caught:
        call()
    assert caught.value.code == code
    return caught.value


class TestMove:
    def test_with_move(self) -> None:
        client = FakeClient({"MOVE", "UIDPLUS"}, b"9 5,7 11:12")
        assert move_uids(selected(client), [5, 7], "Archive") == Moved("Archive", 9, {5: 11, 7: 12})
        assert client.calls == [("move", [5, 7], "Archive")]

    def test_without_move_copies_and_expunges_exactly_those_uids(self) -> None:
        client = FakeClient({"UIDPLUS"}, b"9 5,7 11:12")
        assert move_uids(selected(client), [5, 7], "Archive") == Moved("Archive", 9, {5: 11, 7: 12})
        assert client.calls == [
            ("copy", [5, 7], "Archive"),
            ("add_flags", [5, 7], [b"\\Deleted"]),
            ("uid_expunge", [5, 7]),
        ]

    def test_without_copyuid_says_nothing_of_where_they_landed(self) -> None:
        client = FakeClient({"UIDPLUS"})
        assert move_uids(selected(client), [5], "Archive") == Moved("Archive", None)

    def test_a_server_with_neither_is_refused_before_anything(self) -> None:
        client = FakeClient({"IMAP4rev1"})
        error = refused(lambda: move_uids(selected(client), [5], "Archive"), "unsupported")
        assert "only move mail by also deleting" in error.message
        assert client.calls == []

    @pytest.mark.parametrize("step", ["add_flags", "uid_expunge"])
    @pytest.mark.parametrize(
        ("failure", "code"),
        [(OSError("connection reset"), "unreachable"), (MailError("not_found"), "server_error")],
    )
    def test_a_copy_left_in_the_source(self, step: str, failure: Exception, code: str) -> None:
        """The copies exist, so even a refusal is a failure, and where they went is kept."""
        client = FakeClient({"UIDPLUS"}, b"9 5 11")
        client.fail[step] = failure
        with pytest.raises(CopiedNotRemovedError) as caught:
            move_uids(selected(client), [5], "Archive")
        assert caught.value.code == code
        assert caught.value.moved == Moved("Archive", 9, {5: 11})
        assert caught.value.detail is not None
        assert "do not repeat the move" in caught.value.detail
        assert client.calls[0] == ("copy", [5], "Archive")

    def test_a_failed_copy_is_raised_as_it_is(self) -> None:
        client = FakeClient({"UIDPLUS"})
        client.fail["copy"] = OSError("lost")
        with pytest.raises(OSError, match="lost"):
            move_uids(selected(client), [5], "Archive")
        assert [call[0] for call in client.calls] == ["copy"]


def test_copyuid() -> None:
    assert _copy_uids([b"1 1:2 3:4", b"9 5,7 11:12"], 2) == (9, {5: 11, 7: 12})
    assert _copy_uids([b"9 3:1 4:6"], 3) == (9, {3: 4, 2: 5, 1: 6})
    assert _copy_uids(None, 1) is None
    assert _copy_uids([b"9 1:2 3"], 2) is None  # sets of different sizes
    assert _copy_uids([b"9 1:4294967295 1:4294967295"], 2) is None  # more than was copied
    assert _copy_uids([b"nonsense"], 1) is None


def test_store_flags_sets_only_the_three_it_may() -> None:
    client = FakeClient(set())
    store_flags(selected(client), [5, 7], add=["\\Answered", "\\Seen"], remove=["\\Flagged"])
    assert client.calls == [
        ("add_flags", [5, 7], [b"\\Answered", b"\\Seen"]),
        ("remove_flags", [5, 7], [b"\\Flagged"]),
    ]
    client.calls.clear()
    for change in (
        {"add": ["\\Seen", "\\Deleted"]},
        {"remove": ["\\Deleted"]},
        {"add": ["\\Draft"]},
        {"add": ["\\seen"]},
        {"add": ["\\Seen"], "remove": ["\\Seen"]},
    ):
        with pytest.raises(MailError) as caught:
            store_flags(selected(client), [5], **change)
        assert caught.value.code == "invalid_argument"
    store_flags(selected(client), [], add=["\\Seen"])
    assert client.calls == []


def test_delete_needs_uidplus_and_expunges_exactly_those_uids() -> None:
    client = FakeClient({"MOVE"})
    refused(lambda: delete_uids(selected(client), [5]), "unsupported")
    assert client.calls == []
    client = FakeClient({"UIDPLUS"})
    delete_uids(selected(client), [5, 9])
    assert client.calls == [("add_flags", [5, 9], [b"\\Deleted"]), ("uid_expunge", [5, 9])]


FOLDERS = [
    Folder("INBOX", "/", "inbox", ()),
    Folder("Drafts", "/", "drafts", ()),
    Folder("Sent", "/", "sent", ()),
    Folder("Trash", "/", "trash", ()),
    Folder("Projects", "/", None, ()),
    Folder("Parent", "/", None, ("\\Noselect",)),
]


def test_move_target() -> None:
    assert move_target("Projects", "inbox", FOLDERS, account="acc") == FOLDERS[0]
    assert move_target("INBOX", "Trash", FOLDERS, account="acc") == FOLDERS[3]
    for target, code, text in [
        ("Nowhere", "mailbox_missing", '"Nowhere" is not a folder of acc; see folders.'),
        ("Parent", "mailbox_missing", '"Parent" is not a folder of acc'),
        ("Projects", "invalid_argument", "They are already in that folder."),
        ("Drafts", "invalid_argument", "cannot be moved into Drafts or Sent"),
        ("Sent", "invalid_argument", "cannot be moved into Drafts or Sent"),
    ]:
        with pytest.raises(MailError) as caught:
            move_target("Projects", target, FOLDERS, account="acc")
        assert (caught.value.code, text in caught.value.message) == (code, True), target
    assert special_folder_in(FOLDERS, "trash") == FOLDERS[3]
    assert special_folder_in(FOLDERS, "archive") is None


def test_a_group_ending_in_non_ascii_text_ends_with_all() -> None:
    """A literal cannot be followed by the closing parenthesis: `ALL`, ANDed in, goes between."""
    tokens, utf8 = search_arguments(["OR", ["SUBJECT", "perché"], ["SUBJECT", "città"]])
    assert utf8
    assert tokens == [
        b"OR",
        b"(SUBJECT",
        "perché".encode(),
        b"ALL)",
        b"(SUBJECT",
        "città".encode(),
        b"ALL)",
    ]
    assert isinstance(tokens[2], _literal)
    assert not isinstance(tokens[3], _literal)

    tokens, _ = search_arguments(["NOT", ["FROM", "ana", ["SUBJECT", "perché"]]])
    assert tokens == [b"NOT", b"(FROM", b"ana", b"(SUBJECT", "perché".encode(), b"ALL))"]

    # At the top level nothing closes, so nothing is added.
    tokens, _ = search_arguments(["SUBJECT", "perché"])
    assert tokens == [b"SUBJECT", "perché".encode()]
    # An ASCII value closes its group as before.
    tokens, utf8 = search_arguments(["NOT", ["SUBJECT", "plain"]])
    assert (tokens, utf8) == ([b"NOT", b"(SUBJECT", b"plain)"], False)
