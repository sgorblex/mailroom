"""A folder's UIDs and flags over a fake client: the UID SEARCH ranges, CHANGEDSINCE and the
refusals, without a server."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError

from mailroom_core.mail import (
    MailboxContext,
    MailConnections,
    MailError,
    changed_flags_of,
    changes,
    flags_of,
    uids_of,
)


class _Client:
    """Messages by sequence number (their UIDs); records each FETCH and SEARCH."""

    def __init__(
        self,
        uids: list[int],
        flags: dict[int, tuple[bytes, ...]] | None = None,
        labels: dict[int, tuple[bytes, ...]] | None = None,
    ) -> None:
        self.uids = uids
        self.flags = flags or {}
        self.labels = labels
        """Gmail's, when given: the server is then Gmail."""
        self.use_uid = True
        self.normalise_times = False
        self.expunged: set[int] = set()
        """Gone once the range bounds were fetched."""
        self.unsolicited: list[bytes] = []
        """Answer lines sent ahead of a FETCH's own."""
        self.calls: list[tuple[object, ...]] = []

    def fetch(
        self, messages: object, data: list[str], modifiers: list[str] | None = None
    ) -> dict[int, dict[bytes, object]]:
        self.calls.append(("fetch", self.use_uid, messages, tuple(data), modifiers))
        assert not self.use_uid
        bounds = {seq: {b"UID": self.uids[seq - 1]} for seq in cast("list[int]", messages)}
        self.uids = [uid for uid in self.uids if uid not in self.expunged]
        return bounds

    def has_capability(self, name: str) -> bool:
        return self.labels is not None and name == "X-GM-EXT-1"

    def _raw_command_untagged(self, command: bytes, args: list[bytes]) -> list[bytes]:
        self.calls.append((command.decode(), *(arg.decode() for arg in args)))

        def labelled(uid: int) -> bytes:
            if self.labels is None:
                return b""
            return b" X-GM-LABELS (%s)" % b" ".join(self.labels.get(uid, ()))

        return [
            *self.unsolicited,
            *(
                b"%d (UID %d FLAGS (%s)%s)"
                % (seq, uid, b" ".join(self.flags.get(uid, ())), labelled(uid))
                for seq, uid in enumerate(self.uids, 1)
            ),
        ]

    def search(self, criteria: list[str]) -> list[int]:
        self.calls.append(("search", *criteria))
        low, high = criteria[1].split(":")
        top = max(self.uids) if high == "*" else int(high)
        found = [uid for uid in self.uids if int(low) <= uid <= top]
        # `n:*` with n above every UID still matches the highest one.
        return found or ([max(self.uids)] if high == "*" else [])


def _context(client: _Client, highest_modseq: int | None = 40) -> MailboxContext:
    return MailboxContext(
        cast("Any", client),
        "INBOX",
        True,
        uid_validity=7,
        exists=len(client.uids),
        uid_next=None,
        account=1,
        highest_modseq=highest_modseq,
    )


def test_searches_uids_in_ranges_bounded_by_every_chunkth_message(monkeypatch) -> None:
    monkeypatch.setattr(changes, "UID_CHUNK", 2)
    client = _Client([3, 9, 10, 40, 41])

    listed = uids_of(_context(client))

    assert listed.uids == (3, 9, 10, 40, 41)
    assert (listed.uid_validity, listed.highest_modseq) == (7, 40)
    assert client.calls == [
        ("fetch", False, [2, 4], ("UID",), None),
        ("search", "UID", "1:9"),
        ("search", "UID", "10:40"),
        ("search", "UID", "41:*"),
    ]
    assert client.use_uid


def test_a_message_expunged_meanwhile_only_shrinks_a_range(monkeypatch) -> None:
    monkeypatch.setattr(changes, "UID_CHUNK", 2)
    client = _Client([5, 7, 9])
    client.expunged = {9}

    # `8:*` matches 7, the highest UID left, which belongs to the range before.
    assert uids_of(_context(client)).uids == (5, 7)
    assert client.calls[1:] == [("search", "UID", "1:7"), ("search", "UID", "8:*")]


def test_one_range_for_a_folder_within_a_chunk(monkeypatch) -> None:
    monkeypatch.setattr(changes, "UID_CHUNK", 2)
    client = _Client([5, 6])

    assert uids_of(_context(client)).uids == (5, 6)
    assert client.calls == [("search", "UID", "1:*")]


def test_an_empty_folder_asks_nothing() -> None:
    client = _Client([])
    context = _context(client)

    assert uids_of(context).uids == ()
    assert flags_of(context).flags == {}
    assert changed_flags_of(context, 1).flags == {}
    assert client.calls == []


def test_flags_leave_recent_out() -> None:
    client = _Client([1, 2], {1: (b"\\Seen", b"\\Recent"), 2: (b"$MailroomSort",)})

    listed = flags_of(_context(client))

    assert listed.flags == {1: frozenset({"\\Seen"}), 2: frozenset({"$MailroomSort"})}
    assert client.calls == [("FETCH", "1:*", "(FLAGS)")]


def test_an_unsolicited_fetch_without_a_uid_is_left_out() -> None:
    client = _Client([5, 9], {5: (b"\\Seen",)})
    # Another client's flag change, by sequence number: 2 is UID 9's, not a UID.
    client.unsolicited = [b"2 (FLAGS (\\Flagged) MODSEQ (41))"]

    assert flags_of(_context(client)).flags == {5: frozenset({"\\Seen"}), 9: frozenset()}


def test_on_gmail_labels_come_with_the_flags() -> None:
    client = _Client([1, 2], {1: (b"\\Seen",)}, {1: (b"\\Inbox", b"Work"), 2: ()})

    changed = changed_flags_of(_context(client), 12)

    assert changed.flags == {1: frozenset({"\\Seen"}), 2: frozenset()}
    assert changed.labels == {1: ("\\Inbox", "Work"), 2: ()}
    assert client.calls == [("FETCH", "1:*", "(FLAGS X-GM-LABELS)", "(CHANGEDSINCE 12)")]
    assert changed_flags_of(_context(client), 40).labels == {}
    assert flags_of(_context(_Client([1]))).labels is None


def test_changes_since_pass_changedsince_and_skip_an_up_to_date_folder() -> None:
    client = _Client([1])

    assert changed_flags_of(_context(client), 12).flags == {1: frozenset()}
    assert changed_flags_of(_context(client), 40).flags == {}
    assert client.calls == [("FETCH", "1:*", "(FLAGS)", "(CHANGEDSINCE 12)")]


@pytest.mark.parametrize(
    ("highest_modseq", "since", "code"),
    [(None, 1, "unsupported"), (40, -1, "invalid_argument")],
)
def test_changes_since_refusals(highest_modseq: int | None, since: int, code: str) -> None:
    client = _Client([1])
    with pytest.raises(MailError) as caught:
        changed_flags_of(_context(client, highest_modseq), since)
    assert caught.value.code == code
    assert client.calls == []


class _EnablingClient:
    """A newly opened connection offering CONDSTORE and ENABLE, whose ENABLE raises `refusal`."""

    def __init__(self, refusal: Exception) -> None:
        self.refusal = refusal
        self.shut = False
        self._imap = SimpleNamespace(state="AUTH", untagged_responses={})

    def capabilities(self) -> tuple[bytes, ...]:
        return (b"IMAP4REV1", b"CONDSTORE", b"ENABLE", b"UNSELECT")

    def has_capability(self, name: str) -> bool:
        return name.upper().encode() in self.capabilities()

    def enable(self, *_capabilities: str) -> None:
        raise self.refusal

    def select_folder(self, _path: str, readonly: bool = False) -> dict[bytes, int]:
        self._imap.state = "SELECTED"
        return {b"UIDVALIDITY": 7, b"EXISTS": 1}

    def shutdown(self) -> None:
        self.shut = True


def _opening(monkeypatch: pytest.MonkeyPatch, client: _EnablingClient) -> MailConnections:
    connections = MailConnections(lambda _id: cast("Any", SimpleNamespace()), lambda _id: "pw")
    monkeypatch.setattr(connections, "_open_unpooled", lambda _id, read_timeout=None: client)
    return connections


def test_a_refused_enable_leaves_the_folders_without_mod_sequences(monkeypatch) -> None:
    client = _EnablingClient(IMAPClientError("ENABLE failed: NO"))
    connections = _opening(monkeypatch, client)

    context = connections.with_mailbox(1, "INBOX", True, lambda context: context)

    assert context.client is client
    assert context.highest_modseq is None
    assert not client.shut
    with pytest.raises(MailError) as caught:
        changed_flags_of(context, 1)
    assert caught.value.code == "unsupported"


def test_a_connection_lost_on_enable_is_dropped(monkeypatch) -> None:
    client = _EnablingClient(IMAPClientAbortError("socket error: EOF"))
    connections = _opening(monkeypatch, client)

    with pytest.raises(MailError):
        connections.with_mailbox(1, "INBOX", True, lambda context: context)
    assert client.shut
