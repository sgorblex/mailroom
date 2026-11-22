"""Snoozing a message: taking it out of its folder into the account's Snoozed folder (a label on
Gmail), and at its time moving it back marked unread. The host keeps what finds it again and
when: `snooze_target` reads it and readies the folder, the host's move takes it there
(`move_messages`, then `leave_inbox`), and `wake_snoozed` brings it back.

On Gmail a snoozed message is always out of INBOX, snoozed from a label's view too (the move takes
off only the label it came from, `leave_inbox` INBOX's), and always comes back to INBOX, with the
label it was snoozed from.

A message is found again by its Message-ID and size in Snoozed, its ref there first when the
host has one, never by subject: a ref goes stale when another client moved it, and a header
search can miss on a server whose full-text index leaves Message-ID out (`message_id_uids`).
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mailroom_core.mail._mime import addresses, header_text, text_value
from mailroom_core.mail.errors import MailError
from mailroom_core.mail.folders import create_folder
from mailroom_core.mail.organize import OrganizeResult, move_messages, set_flags
from mailroom_core.mail.ref import MessageRef, check_fresh, format_ref, parse_ref
from mailroom_core.mail.selected import message_id_uids
from mailroom_core.render import clean_header

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mailroom_core.mail.audit import Note
    from mailroom_core.mail.imap import Folder, FolderRole, MailboxContext, MailConnections

_log = logging.getLogger("mailroom_core.mail")

SNOOZED_FOLDER: Final = "Snoozed"
"""The folder a snoozed message waits in, at the top level, made when missing."""
_NOT_FROM: Final[frozenset[FolderRole]] = frozenset({"all", "drafts", "sent", "junk", "trash"})
"""Folders a message is not snoozed from: Gmail's All Mail keeps every message (a move out of it
only adds a label), Drafts and Sent take no message back, and Junk and Trash are no place to wake
up in."""
INBOX_LABEL: Final = "\\Inbox"
"""Gmail's label for INBOX, as X-GM-LABELS names it."""


@dataclass(frozen=True, slots=True)
class SnoozeTarget:
    """A message ready to be snoozed: where it is, what finds it again, and the Snoozed folder."""

    ref: MessageRef
    message_id: str
    size: int
    subject: str
    sender: str | None
    """Its From address, for the host's list of snoozed messages."""
    snoozed: str
    """The Snoozed folder's path, made by this call when `created`."""
    created: bool


@dataclass(frozen=True, slots=True)
class Woken:
    """A snoozed message brought back: found at `ref` in Snoozed, marked unread (`unread`), then
    moved (`moved`) to `folder`; on Gmail to INBOX, `folder`'s label added there. `refs`: where
    it is now, each folder's ref (INBOX's first on Gmail), for the host's guard against taking it
    for new mail; empty when the server did not say."""

    ref: str
    folder: str
    unread: OrganizeResult
    moved: OrganizeResult
    refs: tuple[str, ...] = ()


def _snoozed_in(folders: Sequence[Folder]) -> Folder | None:
    exact = next((f for f in folders if f.path == SNOOZED_FOLDER), None)
    if exact is not None:
        return exact
    wanted = SNOOZED_FOLDER.casefold()
    return next((f for f in folders if f.path.casefold() == wanted), None)


def snooze_target(connections: MailConnections, ref: str, note: Note) -> SnoozeTarget:
    """Reads the message `ref` names (read-only) and the account's Snoozed folder, creating it at
    the top level when missing (`create_folder`, noting its audit item). Refused
    `invalid_argument` for a message in Snoozed already, in Gmail's All Mail, Drafts, Sent, Junk
    or Trash, or without a Message-ID; `not_found` when it is gone, `stale_reference` as any
    ref."""
    message = parse_ref(ref)
    account = message.account
    folders = connections.folders(account)
    source = next((f for f in folders if f.path == message.folder), None)
    snoozed = _snoozed_in(folders)
    if snoozed is not None and snoozed.path == message.folder:
        raise MailError("invalid_argument", "This message is snoozed already.")
    if source is not None and source.role in _NOT_FROM:
        raise MailError(
            "invalid_argument",
            "Only a message in the inbox or one of your own folders can be snoozed.",
        )
    if snoozed is not None and not snoozed.selectable:
        raise MailError("mailbox_missing", f"{SNOOZED_FOLDER} cannot hold mail on this account.")

    def read(context: MailboxContext) -> tuple[object, int]:
        check_fresh(message, context.uid_validity)
        fetched = context.client.fetch([message.uid], ["ENVELOPE", "RFC822.SIZE"])
        data = fetched.get(message.uid)
        if data is None:
            raise MailError("not_found")
        size = data.get(b"RFC822.SIZE")
        return data.get(b"ENVELOPE"), size if isinstance(size, int) else 0

    envelope, size = connections.with_mailbox(account, message.folder, True, read)
    message_id = text_value(getattr(envelope, "message_id", None)).strip()
    if not message_id:
        raise MailError(
            "invalid_argument", "This message has no Message-ID to find it by when it is due."
        )
    sender = next(iter(addresses(getattr(envelope, "from_", None))), None)
    created = False
    path = SNOOZED_FOLDER if snoozed is None else snoozed.path
    if snoozed is None:
        try:
            path = create_folder(connections, account, None, SNOOZED_FOLDER, note).path
            created = True
        except MailError as error:
            if error.code != "folder_exists":  # made meanwhile by another call
                raise
    return SnoozeTarget(
        ref=message,
        message_id=clean_header(message_id, 998),
        size=size,
        subject=header_text(getattr(envelope, "subject", None)),
        sender=None if sender is None else sender.address or None,
        snoozed=path,
        created=created,
    )


def leave_inbox(connections: MailConnections, ref: str) -> None:
    """On Gmail, takes INBOX's label off the snoozed message at `ref` (in Snoozed), which a move
    from a label's folder leaves on it; nothing elsewhere, where the move took it out of its one
    folder. Raises `MailError`."""
    message = parse_ref(ref)
    if not connections.capabilities(message.account).gmail:
        return

    def drop(context: MailboxContext) -> None:
        check_fresh(message, context.uid_validity)
        context.client.remove_gmail_labels([message.uid], [INBOX_LABEL], silent=True)

    connections.with_mailbox(message.account, message.folder, False, drop)


def find_snoozed(
    connections: MailConnections,
    account: int,
    snoozed: str,
    message_id: str,
    *,
    size: int | None = None,
    ref: str | None = None,
) -> MessageRef | None:
    """The message with `message_id` (and `size`, when known) in the Snoozed folder `snoozed`:
    at `ref` when that still holds it, else the first a lookup by Message-ID finds (on Gmail
    `X-GM-RAW rfc822msgid:`). None when it is not there, or the folder is gone."""
    known = None
    if ref is not None:
        try:
            known = parse_ref(ref)
        except MailError:
            known = None
    gmail = connections.capabilities(account).gmail

    def find(context: MailboxContext) -> MessageRef | None:
        def at(uid: int) -> MessageRef:
            return MessageRef(account, snoozed, context.uid_validity, uid)

        if (
            known is not None
            and known.folder == snoozed
            and known.uid_validity == (context.uid_validity)
        ):
            data = context.client.fetch([known.uid], ["ENVELOPE"]).get(known.uid)
            found_id = getattr(None if data is None else data.get(b"ENVELOPE"), "message_id", None)
            if text_value(found_id).strip() == message_id:
                return at(known.uid)
        uids = message_id_uids(context, message_id, size=size, gmail=gmail)
        return at(uids[0]) if uids else None

    try:
        return connections.with_mailbox(account, snoozed, True, find)
    except MailError as error:
        if error.code == "mailbox_missing":
            return None
        raise


def wake_snoozed(
    connections: MailConnections,
    account: int,
    snoozed: str,
    message_id: str,
    folder: str,
    *,
    size: int | None = None,
    ref: str | None = None,
) -> Woken | None:
    """Brings the snoozed message back (`find_snoozed`): marks it unread, then moves it to
    `folder`, where it was, or to INBOX when that is no longer a selectable folder of the
    account. On Gmail it goes back to INBOX (the move out of the Snoozed label adds INBOX's),
    then gets `folder`'s label back when that is another; a label that could not be added is
    logged, the message back in INBOX all the same. None when it is not in Snoozed any more:
    moved, or deleted, by another client."""
    found = find_snoozed(connections, account, snoozed, message_id, size=size, ref=ref)
    if found is None:
        return None
    listed = {f.path for f in connections.folders(account) if f.selectable}
    back = folder if folder in listed or folder.upper() == "INBOX" else "INBOX"
    labelled = connections.capabilities(account).gmail and back.upper() != "INBOX"
    text = format_ref(found)
    unread = set_flags(connections, [text], seen=False)
    moved = move_messages(connections, [text], "INBOX" if labelled else back)
    [group] = moved.groups
    refs = tuple(group.new_refs or ()) if group.error is None else ()
    if labelled and refs:
        refs += _label_back(connections, refs[0], back, message_id, size)
    return Woken(ref=text, folder=back, unread=unread, moved=moved, refs=refs)


def _label_back(
    connections: MailConnections, inbox_ref: str, label: str, message_id: str, size: int | None
) -> tuple[str, ...]:
    """On Gmail, adds `label` to the message woken into INBOX at `inbox_ref`: its refs in the
    label's folder, found by its Message-ID, or none when that failed (logged)."""
    message = parse_ref(inbox_ref)
    account = message.account

    def add(context: MailboxContext) -> None:
        check_fresh(message, context.uid_validity)
        context.client.add_gmail_labels([message.uid], [label], silent=True)

    def find(context: MailboxContext) -> tuple[str, ...]:
        uids = message_id_uids(context, message_id, size=size, gmail=True)
        return tuple(format_ref(MessageRef(account, label, context.uid_validity, u)) for u in uids)

    try:
        connections.with_mailbox(account, message.folder, False, add)
        return connections.with_mailbox(account, label, True, find)
    except MailError as error:
        _log.warning(
            "snooze_label_not_restored",
            extra={"event": "snooze_label_not_restored", "account": account, "code": error.code},
        )
        return ()
