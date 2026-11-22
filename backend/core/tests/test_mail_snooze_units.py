"""Snoozing over a fake Gmail account, where a folder is a label: the Snoozed label made when
missing, a move into it dropping INBOX's (from a label's view, `leave_inbox` drops it), and the
wake adding INBOX back, unread, with the label it was snoozed from; the message
found again by its Message-ID when its ref went stale, and None once it left Snoozed."""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from mailroom_core.mail import (
    SNOOZED_FOLDER,
    MailboxContext,
    MailConnections,
    MailError,
    MessageRef,
    find_snoozed,
    format_ref,
    leave_inbox,
    move_messages,
    snooze_target,
    wake_snoozed,
)
from mailroom_core.mail.imap import folders_from
from mailroom_core.mail.snooze import INBOX_LABEL

if TYPE_CHECKING:
    from collections.abc import Callable

    from mailroom_core.mail import AuditItem

ALL_MAIL = "[Gmail]/All Mail"
_SYSTEM = {ALL_MAIL: b"\\All", "[Gmail]/Trash": b"\\Trash", "[Gmail]/Sent Mail": b"\\Sent"}


@dataclass
class Message:
    message_id: str
    size: int
    labels: set[str]
    seen: bool = True
    uids: dict[str, int] = field(default_factory=dict)


def _folder_of(label: str) -> str:
    """The folder a label stored by X-GM-LABELS shows as: `\\Inbox` is INBOX."""
    return "INBOX" if label == INBOX_LABEL else label


class Label:
    """A label's folder as one SELECT sees it: the messages carrying it, by their UIDs there."""

    def __init__(self, account: FakeGmail, path: str) -> None:
        self.account = account
        self.path = path
        self._imap = SimpleNamespace(untagged_responses={})

    def has_capability(self, name: str) -> bool:
        return name in ("MOVE", "UIDPLUS", "X-GM-EXT-1")

    def _by_uid(self) -> dict[int, Message]:
        return {m.uids[self.path]: m for m in self.account.messages if self.path in m.labels}

    def fetch(self, uids: list[int], items: list[str]) -> dict[int, dict[bytes, object]]:
        found = self._by_uid()
        return {
            uid: {
                b"ENVELOPE": SimpleNamespace(
                    message_id=found[uid].message_id.encode(),
                    subject=b"Hello",
                    from_=(SimpleNamespace(name=b"Ana", mailbox=b"ana", host=b"example.org"),),
                ),
                b"RFC822.SIZE": found[uid].size,
                b"FLAGS": (b"\\Seen",) if found[uid].seen else (),
            }
            for uid in uids
            if uid in found
        }

    def _raw_command_untagged(self, command: bytes, arguments: list[object]) -> list[bytes]:
        assert command == b"SEARCH"
        self.account.searches.append(arguments)
        if arguments[0] == b"X-GM-RAW":
            wanted = bytes(cast("bytes", arguments[1])).strip(b'"').removeprefix(b"rfc822msgid:")
            uids = [
                uid
                for uid, m in self._by_uid().items()
                if m.message_id.strip("<>").encode() == wanted
            ]
        else:
            uids = list(self._by_uid())
        return [" ".join(str(uid) for uid in sorted(uids)).encode()]

    def move(self, uids: list[int], destination: str) -> None:
        found = self._by_uid()
        target = self.account.label(destination)
        pairs = []
        for uid in uids:
            message = found[uid]
            message.labels.discard(self.path)
            message.labels.add(destination)
            message.uids[destination] = target.next_uid()
            pairs.append((uid, message.uids[destination]))
        self.account.moves.append((self.path, destination))
        source_set = ",".join(str(old) for old, _ in pairs)
        target_set = ",".join(str(new) for _, new in pairs)
        self._imap.untagged_responses["COPYUID"] = [
            f"{target.uid_validity} {source_set} {target_set}".encode()
        ]

    def add_gmail_labels(self, uids: list[int], labels: list[str], *, silent: bool) -> None:
        found = self._by_uid()
        for uid in uids:
            for label in map(_folder_of, labels):
                if label not in found[uid].labels:
                    found[uid].labels.add(label)
                    found[uid].uids[label] = self.account.label(label).next_uid()
        self.account.stored.append(("+", self.path, *labels))

    def remove_gmail_labels(self, uids: list[int], labels: list[str], *, silent: bool) -> None:
        found = self._by_uid()
        for uid in uids:
            found[uid].labels.difference_update(map(_folder_of, labels))
        self.account.stored.append(("-", self.path, *labels))

    def add_flags(self, uids: list[int], flags: list[bytes], *, silent: bool) -> None:
        self._set_seen(uids, flags, seen=True)

    def remove_flags(self, uids: list[int], flags: list[bytes], *, silent: bool) -> None:
        self._set_seen(uids, flags, seen=False)

    def _set_seen(self, uids: list[int], flags: list[bytes], *, seen: bool) -> None:
        found = self._by_uid()
        if b"\\Seen" in flags:
            for uid in uids:
                found[uid].seen = seen


class Folder:
    def __init__(self, uid_validity: int) -> None:
        self.uid_validity = uid_validity
        self._next = 1

    def next_uid(self) -> int:
        uid = self._next
        self._next += 1
        return uid


class FakeGmail:
    """One Gmail account (id 1) served as `MailConnections` serve it."""

    def __init__(self, labels: tuple[str, ...] = ("INBOX", "Work")) -> None:
        self.paths = [*labels, *_SYSTEM]
        self.folders_by_path = {path: Folder(10 + n) for n, path in enumerate(self.paths)}
        self.messages: list[Message] = []
        self.moves: list[tuple[str, str]] = []
        self.searches: list[list[object]] = []
        self.created: list[str] = []
        self.stored: list[tuple[str, ...]] = []

    def label(self, path: str) -> Folder:
        return self.folders_by_path[path]

    def add(self, message_id: str, *labels: str, size: int = 100) -> str:
        message = Message(message_id, size, {*labels, ALL_MAIL})
        for path in message.labels:
            message.uids[path] = self.label(path).next_uid()
        self.messages.append(message)
        return self.ref(message, labels[0])

    def ref(self, message: Message, path: str) -> str:
        return format_ref(MessageRef(1, path, self.label(path).uid_validity, message.uids[path]))

    # MailConnections
    def serves(self, account: int) -> bool:
        return account == 1

    def capabilities(self, account: int) -> SimpleNamespace:
        return SimpleNamespace(gmail=True)

    def delimiter(self, account: int) -> str:
        return "/"

    def _listing(self) -> list[tuple[tuple[bytes, ...], bytes, str]]:
        return [((_SYSTEM[p],) if p in _SYSTEM else (), b"/", p) for p in self.paths]

    def folders(self, account: int, **_: object) -> list[Any]:
        return folders_from(self._listing())

    def list_folders(self) -> list[tuple[tuple[bytes, ...], bytes, str]]:
        return self._listing()

    def create_folder(self, path: str) -> None:
        self.created.append(path)
        self.paths.append(path)
        self.folders_by_path[path] = Folder(90 + len(self.created))

    def subscribe_folder(self, path: str) -> None:
        pass

    def with_connection(self, account: int, fn: Callable[[Any], object]) -> object:
        return fn(self)

    def with_mailbox(
        self, account: int, path: str, read_only: bool, fn: Callable[[MailboxContext], object]
    ) -> object:
        if path not in self.folders_by_path:
            raise MailError("mailbox_missing")
        context = MailboxContext(
            cast("Any", Label(self, path)),
            path,
            read_only,
            uid_validity=self.label(path).uid_validity,
            exists=0,
            uid_next=None,
            account=account,
        )
        return fn(context)


def connections(gmail: FakeGmail) -> MailConnections:
    return cast("MailConnections", gmail)


def test_snoozing_makes_the_label_and_the_move_drops_inbox() -> None:
    gmail = FakeGmail()
    ref = gmail.add("<a@example.org>", "INBOX", "Work", size=321)
    notes: list[AuditItem] = []

    target = snooze_target(connections(gmail), ref, notes.append)

    assert (target.message_id, target.size, target.subject) == ("<a@example.org>", 321, "Hello")
    assert target.sender == "ana@example.org"
    assert (target.snoozed, target.created) == (SNOOZED_FOLDER, True)
    assert gmail.created == [SNOOZED_FOLDER]
    assert [(n.folder, n.result) for n in notes] == [(SNOOZED_FOLDER, "ok")]

    moved = move_messages(connections(gmail), [ref], target.snoozed)
    assert moved.succeeded == 1
    [message] = gmail.messages
    assert message.labels == {SNOOZED_FOLDER, "Work", ALL_MAIL}

    # Another snooze finds the label there.
    other = gmail.add("<b@example.org>", "INBOX")
    again = snooze_target(connections(gmail), other, notes.append)
    assert (again.snoozed, again.created) == (SNOOZED_FOLDER, False)
    assert gmail.created == [SNOOZED_FOLDER]


@pytest.mark.parametrize("folder", [ALL_MAIL, "[Gmail]/Trash", "[Gmail]/Sent Mail"])
def test_all_mail_and_the_system_folders_are_refused(folder: str) -> None:
    gmail = FakeGmail()
    ref = gmail.add("<a@example.org>", folder)
    with pytest.raises(MailError) as caught:
        snooze_target(connections(gmail), ref, lambda _: None)
    assert caught.value.code == "invalid_argument"
    assert gmail.created == []


def test_a_message_without_a_message_id_is_refused() -> None:
    gmail = FakeGmail()
    ref = gmail.add("", "INBOX")
    with pytest.raises(MailError) as caught:
        snooze_target(connections(gmail), ref, lambda _: None)
    assert "Message-ID" in caught.value.message


def test_waking_adds_inbox_back_unread() -> None:
    gmail = FakeGmail(("INBOX", "Work", SNOOZED_FOLDER))
    ref = gmail.add("<a@example.org>", "INBOX", "Work")
    moved = move_messages(connections(gmail), [ref], SNOOZED_FOLDER)
    [snoozed_ref] = moved.groups[0].new_refs or ()

    woken = wake_snoozed(
        connections(gmail), 1, SNOOZED_FOLDER, "<a@example.org>", "INBOX", ref=snoozed_ref
    )

    assert woken is not None
    assert woken.folder == "INBOX"
    assert (woken.unread.succeeded, woken.moved.succeeded) == (1, 1)
    [message] = gmail.messages
    assert message.labels == {"INBOX", "Work", ALL_MAIL}
    assert message.seen is False
    assert gmail.moves[-1] == (SNOOZED_FOLDER, "INBOX")
    assert gmail.searches == []  # the ref still held it
    assert woken.refs == (gmail.ref(message, "INBOX"),)
    assert gmail.stored == []  # back in INBOX, its other labels never left


def test_snoozed_from_a_label_it_leaves_inbox_and_wakes_in_inbox_with_that_label() -> None:
    gmail = FakeGmail(("INBOX", "Work", SNOOZED_FOLDER))
    gmail.add("<a@example.org>", "INBOX", "Work")
    [message] = gmail.messages
    moved = move_messages(connections(gmail), [gmail.ref(message, "Work")], SNOOZED_FOLDER)
    [snoozed_ref] = moved.groups[0].new_refs or ()
    # The move took off Work's label only: snoozed, it would still be in INBOX.
    assert message.labels == {SNOOZED_FOLDER, "INBOX", ALL_MAIL}

    leave_inbox(connections(gmail), snoozed_ref)

    assert message.labels == {SNOOZED_FOLDER, ALL_MAIL}

    woken = wake_snoozed(
        connections(gmail), 1, SNOOZED_FOLDER, "<a@example.org>", "Work", ref=snoozed_ref
    )

    assert woken is not None
    assert woken.folder == "Work"
    assert message.labels == {"INBOX", "Work", ALL_MAIL}
    assert message.seen is False
    assert gmail.moves[-1] == (SNOOZED_FOLDER, "INBOX")
    assert gmail.stored == [("-", SNOOZED_FOLDER, INBOX_LABEL), ("+", "INBOX", "Work")]
    assert woken.refs == (gmail.ref(message, "INBOX"), gmail.ref(message, "Work"))


def test_a_stale_ref_is_found_again_by_its_message_id() -> None:
    gmail = FakeGmail(("INBOX", SNOOZED_FOLDER))
    gmail.add("<other@example.org>", SNOOZED_FOLDER)
    gmail.add("<a@example.org>", SNOOZED_FOLDER)
    stale = format_ref(MessageRef(1, SNOOZED_FOLDER, 1, 1))

    found = find_snoozed(connections(gmail), 1, SNOOZED_FOLDER, "<a@example.org>", ref=stale)

    assert found == MessageRef(1, SNOOZED_FOLDER, gmail.label(SNOOZED_FOLDER).uid_validity, 2)
    assert gmail.searches == [[b"X-GM-RAW", b"rfc822msgid:a@example.org"]]


def test_a_folder_gone_since_wakes_in_inbox() -> None:
    gmail = FakeGmail(("INBOX", SNOOZED_FOLDER))
    gmail.add("<a@example.org>", SNOOZED_FOLDER)
    woken = wake_snoozed(connections(gmail), 1, SNOOZED_FOLDER, "<a@example.org>", "Projects")
    assert woken is not None
    assert woken.folder == "INBOX"
    assert gmail.messages[0].labels == {"INBOX", ALL_MAIL}


def test_a_message_gone_from_snoozed_is_none() -> None:
    gmail = FakeGmail(("INBOX", SNOOZED_FOLDER))
    gmail.add("<a@example.org>", "INBOX")
    assert wake_snoozed(connections(gmail), 1, SNOOZED_FOLDER, "<a@example.org>", "INBOX") is None
    assert wake_snoozed(connections(gmail), 1, "Gone", "<a@example.org>", "INBOX") is None
    assert gmail.moves == []
