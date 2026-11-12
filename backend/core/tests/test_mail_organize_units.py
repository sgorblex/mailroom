"""Organizing over stub connections: `src/mail/organize.test.ts` (which fallback a server
without MOVE gets), `AuditTrail.outcome` of `src/mcp/tool.test.ts`, and what needs no server:
refusing the whole call before any write, COPYUID, the result's text."""

import hashlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from imapclient.response_types import Address, Envelope

from mailroom_core.mail import (
    Folder,
    GroupResult,
    MailboxContext,
    MailConnections,
    MailError,
    MessageRef,
    OrganizeResult,
    archive_messages,
    audit_outcome,
    format_ref,
    move_messages,
    organize_text,
    parse_ref,
    set_flags,
    trash_messages,
)
from mailroom_core.mail.organize import FlagState

if TYPE_CHECKING:
    from collections.abc import Callable


def envelope(subject: bytes = b"s") -> Envelope:
    sender = (Address(b"Ana", None, b"ana", b"example.org"),)
    return Envelope(None, subject, sender, sender, sender, (), (), (), None, b"<id@x>")


class StubClient:
    """An IMAPClient with a few messages in the selected folder, recording each command."""

    def __init__(self, capabilities: set[str], uids: set[int], copyuid: bytes | None) -> None:
        self.capabilities = capabilities
        self.uids = uids
        self.copyuid = copyuid
        self.calls: list[tuple[object, ...]] = []
        self._imap = SimpleNamespace(untagged_responses={})

    def has_capability(self, name: str) -> bool:
        return name in self.capabilities

    def fetch(self, uids: list[int], items: list[str]) -> dict[int, dict[bytes, object]]:
        return {
            uid: {b"ENVELOPE": envelope(), b"FLAGS": (b"\\Seen",)}
            for uid in uids
            if uid in self.uids
        }

    def _copied(self, name: str, uids: list[int], folder: str) -> None:
        self.calls.append((name, uids, folder))
        if self.copyuid is not None:
            self._imap.untagged_responses.setdefault("COPYUID", []).append(self.copyuid)

    def move(self, uids: list[int], folder: str) -> None:
        self._copied("move", uids, folder)

    def copy(self, uids: list[int], folder: str) -> None:
        self._copied("copy", uids, folder)

    def add_flags(self, uids: list[int], flags: list[bytes], silent: bool = False) -> None:
        self.calls.append(("add_flags", uids, flags))

    def remove_flags(self, uids: list[int], flags: list[bytes], silent: bool = False) -> None:
        self.calls.append(("remove_flags", uids, flags))

    def uid_expunge(self, uids: list[int]) -> None:
        self.calls.append(("uid_expunge", uids))


FOLDERS = [
    Folder("INBOX", "/", "inbox", ()),
    Folder("Drafts", "/", "drafts", ()),
    Folder("Sent", "/", "sent", ()),
    Folder("Archive", "/", "archive", ()),
    Folder("Junk", "/", "junk", ()),
    Folder("Trash", "/", "trash", ()),
    Folder("Projects", "/", None, ()),
    Folder("Parent", "/", None, ("\\Noselect",)),
]


class StubConnections:
    """Connections serving `acc`, every folder with UIDVALIDITY 1 and the same stub client."""

    def __init__(self, client: StubClient, folders: list[Folder] = FOLDERS) -> None:
        self.client = client
        self._folders = folders
        self.selected: list[tuple[str, bool]] = []

    def serves(self, account_id: str) -> bool:
        return account_id == "acc"

    def folders(self, account_id: str) -> list[Folder]:
        return self._folders

    def with_mailbox[T](
        self, account_id: str, path: str, read_only: bool, fn: Callable[[MailboxContext], T]
    ) -> T:
        self.selected.append((path, read_only))
        return fn(
            MailboxContext(
                self.client,  # type: ignore[arg-type]
                path,
                read_only,
                uid_validity=1,
                exists=len(self.client.uids),
                uid_next=None,
                account=account_id,
            )
        )


def stubbed(
    capabilities: set[str],
    uids: set[int] = frozenset({5}),  # type: ignore[assignment]
    copyuid: bytes | None = None,
    folders: list[Folder] = FOLDERS,
) -> tuple[MailConnections, StubClient]:
    client = StubClient(capabilities, set(uids), copyuid)
    return cast("MailConnections", StubConnections(client, folders)), client


def ref(uid: int = 5, folder: str = "INBOX", account: str = "acc", validity: int = 1) -> str:
    return format_ref(MessageRef(account, folder, validity, uid))


class TestMove:
    def test_refuses_a_server_with_neither_move_nor_uidplus(self) -> None:
        connections, client = stubbed({"IMAP4rev1"})
        result = move_messages(connections, [ref()], "Archive")
        [group] = result.groups
        assert group.error is not None
        assert group.error.code == "unsupported"
        assert "only move mail by also deleting" in group.error.message
        assert client.calls == []
        assert result.outcome == "refused"

    def test_moves_with_move(self) -> None:
        connections, client = stubbed({"MOVE"})
        result = move_messages(connections, [ref()], "Archive")
        assert client.calls == [("move", [5], "Archive")]
        assert result.groups == (
            GroupResult("acc", "INBOX", count=1, new_refs=None, destination="Archive"),
        )
        assert cast("StubConnections", connections).selected == [("INBOX", False)]

    def test_without_move_copies_and_expunges_only_those_uids(self) -> None:
        connections, client = stubbed({"UIDPLUS"}, {5, 7}, b"9 5,7 11:12")
        result = move_messages(connections, [ref(7), ref(5)], "Archive")
        assert client.calls == [
            ("copy", [5, 7], "Archive"),
            ("add_flags", [5, 7], [b"\\Deleted"]),
            ("uid_expunge", [5, 7]),
        ]
        [group] = result.groups
        assert group.new_refs is not None
        assert [parse_ref(r) for r in group.new_refs] == [
            MessageRef("acc", "Archive", 9, 11),
            MessageRef("acc", "Archive", 9, 12),
        ]
        assert [(i.uid, i.sender, i.destination, i.result) for i in result.items] == [
            (5, "ana@example.org", "Archive", "ok"),
            (7, "ana@example.org", "Archive", "ok"),
        ]
        assert result.items[0].subject_sha256 == hashlib.sha256(b"s").hexdigest()
        assert "s" not in result.items[0].record.values()
        assert result.items[0].record["from"] == "ana@example.org"
        assert "sender" not in result.items[0].record

    @pytest.mark.parametrize("step", ["add_flags", "uid_expunge"])
    def test_a_copy_left_in_the_source_fails_with_its_new_refs(self, step: str) -> None:
        """Copied but not removed: the group fails, but says where the copies are and that the
        move must not be repeated, since the messages may now be in both folders."""
        connections, client = stubbed({"UIDPLUS"}, {5, 7}, b"9 5,7 11:12")

        def fail(*args: object, **kwargs: object) -> None:
            client.calls.append((step, *args))
            raise OSError("connection reset")

        setattr(client, step, fail)
        result = move_messages(connections, [ref(5), ref(7)], "Archive")
        assert client.calls[0] == ("copy", [5, 7], "Archive")
        assert client.calls[-1][0] == step
        [group] = result.groups
        assert not group.ok
        assert group.error is not None
        assert group.error.code == "unreachable"
        assert group.new_refs is not None
        assert [parse_ref(r) for r in group.new_refs] == [
            MessageRef("acc", "Archive", 9, 11),
            MessageRef("acc", "Archive", 9, 12),
        ]
        # The error itself names the copies, for a host that raises a lone folder's error.
        assert group.error.detail == (
            "They were copied to the destination but may not have been removed from this "
            f"folder: do not repeat the move. New refs: {', '.join(group.new_refs)}"
        )
        assert (group.count, group.destination) == (0, "Archive")
        assert [(i.uid, i.destination, i.copied, i.result) for i in result.items] == [
            (5, "Archive", True, "unreachable"),
            (7, "Archive", True, "unreachable"),
        ]
        assert result.items[0].record["copied"] is True
        assert result.outcome == "failed"
        text = organize_text(result)
        assert text.startswith("acc/INBOX: The mail server for this account could not be reached.")
        assert text == f"acc/INBOX: {group.error.message}"
        assert text.count("New refs:") == 1

    def test_refusals(self) -> None:
        connections, client = stubbed({"MOVE"})
        cases = [
            ("Nowhere", "mailbox_missing", '"Nowhere" is not a folder of acc; see folders.'),
            ("Parent", "mailbox_missing", "is not a folder of acc"),
            ("inbox", "invalid_argument", "They are already in that folder."),
            ("Drafts", "invalid_argument", "cannot be moved into Drafts or Sent"),
            ("Sent", "invalid_argument", "cannot be moved into Drafts or Sent"),
        ]
        for folder, code, message in cases:
            [group] = move_messages(connections, [ref()], folder).groups
            assert group.error is not None
            assert (group.error.code, message in group.error.message) == (code, True), folder
        assert client.calls == []

    def test_stale_and_missing_refs_fail_their_group_only(self) -> None:
        connections, client = stubbed({"MOVE"}, {5})
        result = move_messages(
            connections, [ref(5), ref(6, "Projects"), ref(5, "Junk", validity=2)], "Archive"
        )
        ok, missing, stale = result.groups
        assert ok.ok
        assert missing.error is not None
        assert missing.error.message == (
            "No such message or part. 1 of these messages are no longer in Projects; search again."
        )
        assert stale.error is not None
        assert stale.error.code == "stale_reference"
        assert (result.succeeded, result.failed, result.outcome) == (1, 2, "partial")
        assert [(i.folder, i.result) for i in result.items] == [
            ("INBOX", "ok"),
            ("Projects", "not_found"),
            ("Junk", "stale_reference"),
        ]
        assert client.calls == [("move", [5], "Archive")]

    def test_junk_training(self) -> None:
        connections, _client = stubbed({"MOVE"})
        assert move_messages(connections, [ref()], "Junk").groups[0].junk == "spam"
        assert move_messages(connections, [ref(folder="Junk")], "INBOX").groups[0].junk == "ham"
        assert move_messages(connections, [ref(folder="Junk")], "Trash").groups[0].junk is None
        assert move_messages(connections, [ref(folder="Junk")], "Archive").groups[0].junk == "ham"

    def test_a_server_error_is_noted_and_never_retried(self) -> None:
        connections, client = stubbed({"MOVE"})

        def refuse(uids: list[int], folder: str) -> None:
            client.calls.append(("move", uids, folder))
            raise OSError("connection reset")

        client.move = refuse  # type: ignore[method-assign]
        result = move_messages(connections, [ref()], "Archive")
        assert result.groups[0].error is not None
        assert [i.result for i in result.items] == ["unreachable"]
        assert result.outcome == "failed"
        assert client.calls == [("move", [5], "Archive")]


class TestWholeCallRefusals:
    @pytest.mark.parametrize(
        "refs",
        [[], [ref(uid) for uid in range(1, 52)], [ref(), "garbage"], [ref(), ref(account="other")]],
    )
    def test_nothing_is_written(self, refs: list[str]) -> None:
        connections, client = stubbed({"MOVE"})
        for call in (
            lambda: move_messages(connections, refs, "Archive"),
            lambda: trash_messages(connections, refs),
            lambda: archive_messages(connections, refs),
            lambda: set_flags(connections, refs, seen=True),
        ):
            with pytest.raises(MailError) as raised:
                call()
            assert raised.value.code == "invalid_argument"
        assert client.calls == []
        assert cast("StubConnections", connections).selected == []

    def test_unknown_account(self) -> None:
        connections, _client = stubbed({"MOVE"})
        with pytest.raises(MailError, match='Unknown account "other"'):
            move_messages(connections, [ref(account="other")], "INBOX")

    def test_flags_need_a_change(self) -> None:
        connections, _client = stubbed({"MOVE"})
        with pytest.raises(MailError, match=r"Give `seen`, `flagged` or both\."):
            set_flags(connections, [ref()])


def test_trash_and_archive_refuse_what_they_cannot_do() -> None:
    connections, _client = stubbed({"MOVE"}, folders=[Folder("INBOX", "/", "inbox", ())])
    [no_trash] = trash_messages(connections, [ref()]).groups
    assert no_trash.error is not None
    assert no_trash.error.message.endswith("acc has no Trash folder.")
    [no_archive] = archive_messages(connections, [ref()]).groups
    assert no_archive.error is not None
    assert (no_archive.error.code, no_archive.error.detail) == (
        "unsupported",
        "acc has no Archive folder.",
    )

    connections, client = stubbed({"MOVE"})
    [in_trash] = trash_messages(connections, [ref(folder="Trash")]).groups
    assert in_trash.error is not None
    assert in_trash.error.detail == "They are already in Trash; mailroom never deletes permanently."
    [in_archive] = archive_messages(connections, [ref(folder="Archive")]).groups
    assert in_archive.error is not None
    assert in_archive.error.detail == "They are already in that folder."
    assert client.calls == []
    assert archive_messages(connections, [ref()]).groups[0].destination == "Archive"
    assert trash_messages(connections, [ref(folder="Junk")]).groups[0].junk is None


def test_set_flags_stores_and_reads_back() -> None:
    connections, client = stubbed(set())
    result = set_flags(connections, [ref()], seen=True, flagged=False)
    assert client.calls == [
        ("add_flags", [5], [b"\\Seen"]),
        ("remove_flags", [5], [b"\\Flagged"]),
    ]
    [group] = result.groups
    assert group.flags == (FlagState(ref(), seen=True, flagged=False),)
    assert (group.count, group.new_refs, group.destination) == (1, (), None)
    assert [(i.seen, i.flagged, i.result) for i in result.items] == [(True, False, "ok")]

    def broken(uids: list[int], items: list[str]) -> dict[int, dict[bytes, object]]:
        if items == ["FLAGS"]:
            raise OSError("lost")
        return StubClient.fetch(client, uids, items)

    client.fetch = broken  # type: ignore[method-assign]
    shut: list[bool] = []
    client.shutdown = lambda: shut.append(True)  # type: ignore[attr-defined]
    [unread_back] = set_flags(connections, [ref()], flagged=True).groups
    assert unread_back.ok
    assert unread_back.flags == (FlagState(ref(), None, None),)
    assert shut == [True]  # the connection's state is unknown after a lost read

    def refuse_shutdown() -> None:
        raise OSError("not connected")

    client.shutdown = refuse_shutdown  # type: ignore[attr-defined]
    [still_ok] = set_flags(connections, [ref()], flagged=True).groups
    assert still_ok.ok  # a failing shutdown does not undo the change


def test_audit_outcome() -> None:
    assert audit_outcome(["ok"], "not_found") == "partial"
    assert audit_outcome([], "not_found") == "refused"
    assert audit_outcome([]) == "ok"
    assert audit_outcome(["not_found", "server_error"]) == "failed"
    assert audit_outcome(["busy", "stale_reference"]) == "refused"


def test_organize_text() -> None:
    new = ref(3, "Archive")
    result = OrganizeResult(
        (
            GroupResult("acc", "INBOX", count=1, new_refs=(new,), destination="Archive"),
            GroupResult("acc", "Junk", count=2, new_refs=None, junk="ham", destination="INBOX"),
            GroupResult("acc", "Projects", error=MailError("not_found", "1 of these …")),
            GroupResult(
                "acc",
                "Sent",
                count=2,
                new_refs=(),
                flags=(FlagState("r1", True, True), FlagState("r2", None, None)),
            ),
            GroupResult(
                "acc", "Lists", count=1, new_refs=(), flags=(FlagState("r3", False, False),)
            ),
        ),
        (),
    )
    assert organize_text(result).split("\n") == [
        f"acc/INBOX: moved 1 to Archive. New refs: {new}",
        (
            "acc/Junk: moved 2 to INBOX. Search there for their new refs. "
            "The spam filter learns them as not spam."
        ),
        "acc/Projects: No such message or part. 1 of these …",
        "acc/Sent: updated 2: r1 read, flagged; r2 (flags not read back)",
        "acc/Lists: updated 1: r3 unread",
    ]
