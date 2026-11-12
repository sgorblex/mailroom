"""Drafts in the account's `\\Drafts` folder (`src/mail/drafts.ts`, and the saving of
`src/mcp/tools/drafts.ts`).

An IMAP message cannot be edited, so a save is `check_removable`, APPEND, then removing the old
version, in that order: whatever fails, the last good draft is still there. A draft mailroom wrote
is expunged by its UID alone (docs/core.md, *Safety*); any other goes to Trash.
"""

import re
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from mailroom_core.mail._mime import header_text, text_value
from mailroom_core.mail.attachments import attachments_from
from mailroom_core.mail.audit import (
    AuditItem,
    Removal,
    audit_result,
    message_item,
    outgoing_item,
)
from mailroom_core.mail.compose import (
    DRAFT_HEADER,
    compose,
    draft_answers,
    is_own_draft,
    new_message_id,
)
from mailroom_core.mail.download import body_text, part_content
from mailroom_core.mail.errors import MailError, is_connection_error, mail_error
from mailroom_core.mail.organize import trash_messages
from mailroom_core.mail.outgoing import check_sendable, check_uploads
from mailroom_core.mail.read import fetch_structure, fetched_headers
from mailroom_core.mail.recipients import envelope_mailboxes
from mailroom_core.mail.ref import MessageRef, check_fresh, format_ref, parse_ref
from mailroom_core.mail.reply import references_in
from mailroom_core.mail.selected import delete_uids
from mailroom_core.render import html_to_text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from imapclient import IMAPClient

    from mailroom_core.config import AccountConfig
    from mailroom_core.mail.attachments import AttachmentSource
    from mailroom_core.mail.compose import MessageInput
    from mailroom_core.mail.imap import MailboxContext, MailConnections
    from mailroom_core.mail.recipients import Mailbox
    from mailroom_core.mail.structure import Structure
    from mailroom_core.render import PartInfo

MAX_DRAFT_BYTES: Final = 2 * 1024 * 1024
"""Of a draft's text when read back; a longer one reads as `lossy`."""
DRAFT_FLAGS: Final = ("\\Draft", "\\Seen")
_MARK: Final = f"BODY.PEEK[HEADER.FIELDS ({DRAFT_HEADER.upper()})]"
_MAX_UID: Final = 0xFFFFFFFF
_APPENDUID: Final = re.compile(rb"\[APPENDUID ([0-9]{1,10}) ([0-9]{1,10})\]", re.IGNORECASE)
_COMPOSE_ID: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")
"""A client's UUID, or what `new_message_id` makes up: nothing a search needs to quote."""
_OWN_ID: Final = re.compile(r"<([A-Za-z0-9_-]{1,64})@[^<>]+>")

type Note = Callable[[AuditItem], None]
"""Takes each audit item as it happens, so what was done is on record even if the call fails."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Draft:
    """A draft as a composer or an update starts from it. All of it is the writer's text."""

    ref: MessageRef
    own: bool
    """Written by mailroom: it carries `DRAFT_HEADER`."""
    message_id: str | None = None
    compose_id: str | None = None
    """What its Message-ID was built on; None unless `own`."""
    sender: tuple[Mailbox, ...] = ()
    to: tuple[Mailbox, ...] = ()
    cc: tuple[Mailbox, ...] = ()
    bcc: tuple[Mailbox, ...] = ()
    subject: str = ""
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    answers: MessageRef | None = None
    """The message a send is to mark `\\Answered`, in this draft's account or None: the header is
    only as trustworthy as whoever wrote the draft."""
    text: str = ""
    lossy: bool = False
    """Its text is not all of it: it had HTML, inline images or an invite with no name, which a
    save would not keep, or more text than is read."""
    attachments: tuple[PartInfo, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class SavedDraft:
    """A save that went through: from here on, the message is in `folder`."""

    ref: MessageRef | None
    """None when it could not be read back: it is saved all the same, `find_drafts` finds it, and
    the version it was to replace is still there."""
    folder: str
    message_id: str
    parts: tuple[PartInfo, ...]
    """Its attachments, as a read lists them; empty when `ref` is None."""
    replaced: Removal | None
    """What became of the old version; `failed` when it is still there, None when there was none
    or it was left in place because `ref` is None."""
    autosave: bool
    """False without UIDPLUS, where every replaced version lands in Trash: save only on request."""


def appended_uid(response: bytes | None, uid_validity: int) -> int | None:
    """The UID an APPEND's tagged response reports (APPENDUID, RFC 4315), which imapclient hands
    over as raw bytes; None without one, or for a UIDVALIDITY other than the folder's."""
    found = _APPENDUID.match(response or b"")
    if found is None or int(found.group(1)) != uid_validity:
        return None
    uid = int(found.group(2))
    return uid if 0 < uid <= _MAX_UID else None


def _structure_if(client: IMAPClient, uid: int, message_id: str) -> Structure | None:
    """The message's structure, if `uid` is there and carries `message_id`."""
    try:
        data, structure = fetch_structure(client, uid, ["ENVELOPE"])
    except MailError:
        return None
    found = getattr(data.get(b"ENVELOPE"), "message_id", None)
    return structure if text_value(found) == message_id else None


def _read_back(
    context: MailboxContext, response: bytes | None, message_id: str
) -> tuple[int, Structure] | None:
    """Where the APPEND put the message: APPENDUID when it checks out, else the newest match of
    a search by Message-ID, which a full-text index that has not caught up yet can miss (Mailu).
    UIDNEXT from before the APPEND rules out the older versions sharing its Message-ID: taking
    one of them for the new message would remove it as "the old version", with the ref of what
    is gone."""
    client = context.client
    # RFC 3501, 6.3.11: the new message need not be in view until a NOOP.
    client.noop()
    uid = appended_uid(response, context.uid_validity)
    if uid is not None:
        structure = _structure_if(client, uid, message_id)
        if structure is not None:
            return uid, structure
    first = context.uid_next or 0
    matches = client.search(["HEADER", "Message-ID", message_id])
    newest = max((found for found in matches if found >= first), default=None)
    if newest is None:
        return None
    structure = _structure_if(client, newest, message_id)
    return None if structure is None else (newest, structure)


def _append(
    connections: MailConnections,
    account: str,
    folder: str,
    raw: bytes,
    flags: Sequence[str],
    message_id: str,
) -> tuple[MessageRef, Structure] | None:
    def run(context: MailboxContext) -> tuple[MessageRef, Structure] | None:
        client = context.client
        response = client.append(folder, raw, tuple(flags))
        try:
            located = _read_back(context, response, message_id)
        except Exception as error:
            mail_error(error, account)  # logged; the message is saved
            if is_connection_error(error):
                # Its state is unknown: the next caller's SELECT fails and reconnects.
                with suppress(OSError):
                    client.shutdown()
            return None
        if located is None:
            return None
        uid, structure = located
        return MessageRef(account, folder, context.uid_validity, uid), structure

    # Read-write: Dovecot drops the flags of a message appended to a folder the same connection
    # has open read-only, as after a listing.
    return connections.with_mailbox(account, folder, False, run)


def append_message(
    connections: MailConnections,
    account: str,
    folder: str,
    raw: bytes,
    flags: Sequence[str],
    message_id: str,
) -> MessageRef | None:
    """APPENDs a message to `folder` with `flags`, holding the folder selected read-write. The
    ref comes from APPENDUID, checked against `message_id`, else from a search by it. None when
    neither finds it, or the read-back fails: the message is saved all the same. Never retried:
    an error means it may or may not be there."""
    located = _append(connections, account, folder, raw, flags, message_id)
    return None if located is None else located[0]


def check_in_drafts(connections: MailConnections, ref: MessageRef) -> None:
    """Refuses a ref outside the account's Drafts folder: nothing here touches anything else."""
    if ref.folder != connections.special_folder(ref.account, "drafts").path:
        raise MailError("invalid_argument", "That message is not in Drafts.")


def draft_source(connections: MailConnections, ref: MessageRef, max_bytes: int) -> bytes:
    """A draft exactly as stored, for sending it: `too_large` over `max_bytes` rather than cut,
    since a truncated message must never go out."""
    check_in_drafts(connections, ref)
    too_large = MailError("too_large", "The draft is over the attachment size limit.")

    def run(context: MailboxContext) -> bytes:
        check_fresh(ref, context.uid_validity)
        client = context.client
        sized = client.fetch([ref.uid], ["RFC822.SIZE"]).get(ref.uid)
        if sized is None:
            raise MailError("not_found")
        size = sized.get(b"RFC822.SIZE")
        if isinstance(size, int) and size > max_bytes:
            raise too_large
        # One byte over the cap is enough to know it is too large.
        fetched = client.fetch([ref.uid], [f"BODY.PEEK[]<0.{max_bytes + 1}>"]).get(ref.uid)
        raw = None if fetched is None else fetched.get(b"BODY[]<0>")
        if not isinstance(raw, bytes):
            raise MailError("not_found")
        if len(raw) > max_bytes:
            raise too_large
        return raw

    return connections.with_mailbox(ref.account, ref.folder, True, run)


def read_draft(connections: MailConnections, ref: MessageRef) -> Draft:
    """Reads a draft back under EXAMINE."""
    check_in_drafts(connections, ref)

    def run(context: MailboxContext) -> Draft:
        check_fresh(ref, context.uid_validity)
        client = context.client
        fields = f"BODY.PEEK[HEADER.FIELDS (REFERENCES {DRAFT_HEADER.upper()})]"
        data, structure = fetch_structure(client, ref.uid, ["ENVELOPE", fields])
        headers = fetched_headers(data)
        own = is_own_draft(headers)
        text = ""
        lossy = bool(
            structure.html
            or structure.inline_parts
            or (structure.calendar is not None and structure.calendar.name is None)
        )
        fmt = structure.body_format
        if structure.body is not None and fmt is not None:
            content = part_content(client, ref.uid, structure.body, MAX_DRAFT_BYTES)
            complete = len(content) < MAX_DRAFT_BYTES
            lossy = lossy or not complete
            text = body_text(content, structure.body, complete=complete)
            if fmt == "html":
                text = html_to_text(text, False).text
        envelope = data.get(b"ENVELOPE")
        message_id = getattr(envelope, "message_id", None)
        identifier = None if message_id is None else header_text(message_id) or None
        built_on = None if identifier is None or not own else _OWN_ID.fullmatch(identifier)
        in_reply_to = getattr(envelope, "in_reply_to", None)
        answers = draft_answers(headers)
        return Draft(
            ref=ref,
            own=own,
            message_id=identifier,
            compose_id=None if built_on is None else built_on.group(1),
            sender=envelope_mailboxes(getattr(envelope, "from_", None)),
            to=envelope_mailboxes(getattr(envelope, "to", None)),
            cc=envelope_mailboxes(getattr(envelope, "cc", None)),
            bcc=envelope_mailboxes(getattr(envelope, "bcc", None)),
            subject=header_text(getattr(envelope, "subject", None)),
            in_reply_to=None if in_reply_to is None else header_text(in_reply_to) or None,
            references=references_in(headers),
            answers=answers if answers is None or answers.account == ref.account else None,
            text=text.replace("\r\n", "\n"),
            lossy=lossy,
            attachments=structure.carried,
        )

    return connections.with_mailbox(ref.account, ref.folder, True, run)


def _check_compose_id(compose_id: str) -> None:
    if not _COMPOSE_ID.fullmatch(compose_id):
        raise MailError("invalid_argument", "Not a compose id.")


def find_drafts(
    connections: MailConnections, account: str, compose_id: str
) -> tuple[MessageRef, ...]:
    """The versions of a draft in Drafts, newest first: those whose Message-ID was built on
    `compose_id`, whichever identity's domain it took. Finds a save whose answer was lost; it
    can miss one a full-text index has not caught up with."""
    _check_compose_id(compose_id)
    drafts = connections.special_folder(account, "drafts")

    def run(context: MailboxContext) -> tuple[MessageRef, ...]:
        client = context.client
        start = f"<{compose_id}@"
        uids = client.search(["HEADER", "Message-ID", start])
        if not uids:
            return ()
        # A server's search may go by words, and match `<id-2@` for `<id@`: the ENVELOPE decides.
        fetched = client.fetch(uids, ["ENVELOPE"])
        return tuple(
            MessageRef(account, drafts.path, context.uid_validity, uid)
            for uid in sorted(fetched, reverse=True)
            if text_value(getattr(fetched[uid].get(b"ENVELOPE"), "message_id", None))
            .lower()
            .startswith(start.lower())
        )

    return connections.with_mailbox(account, drafts.path, True, run)


def check_removable(connections: MailConnections, ref: MessageRef) -> None:
    """Raises now what `remove_draft` would raise later, so a save never adds a new version it
    then cannot replace the old one with: a draft that is not expunged goes to Trash, which needs
    a Trash folder and MOVE or UIDPLUS."""
    check_in_drafts(connections, ref)

    def run(context: MailboxContext) -> bool:
        check_fresh(ref, context.uid_validity)
        client = context.client
        data = client.fetch([ref.uid], [_MARK]).get(ref.uid)
        if data is None:
            raise MailError("not_found")
        return is_own_draft(fetched_headers(data)) and client.has_capability("UIDPLUS")

    if connections.with_mailbox(ref.account, ref.folder, True, run):
        return
    connections.special_folder(ref.account, "trash")
    capabilities = connections.capabilities(ref.account)
    if not capabilities.move and not capabilities.uidplus:
        raise MailError("unsupported", "This server can only move mail by also deleting others.")


def remove_draft(connections: MailConnections, ref: MessageRef, note: Note) -> Removal:
    """Removes a draft and notes it. One mailroom wrote is expunged by its UID alone, which
    leaves another message marked `\\Deleted` where it is and needs UIDPLUS; any other is moved
    to Trash. Returns which of the two it was; raises when it is still there, however that came
    about, having noted it as `removed="failed"`."""
    envelope: object = None

    def run(context: MailboxContext) -> bool:
        nonlocal envelope
        check_fresh(ref, context.uid_validity)
        client = context.client
        data = client.fetch([ref.uid], ["ENVELOPE", _MARK]).get(ref.uid)
        if data is None:
            raise MailError("not_found")
        if not is_own_draft(fetched_headers(data)) or not client.has_capability("UIDPLUS"):
            return False
        envelope = data.get(b"ENVELOPE")
        try:
            delete_uids(context, [ref.uid])
        except Exception as error:
            raise mail_error(error, ref.account) from None
        note(message_item(ref.account, ref.folder, ref.uid, envelope, "ok", removed="expunged"))
        return True

    try:
        check_in_drafts(connections, ref)
        if connections.with_mailbox(ref.account, ref.folder, False, run):
            return "expunged"
        result = trash_messages(connections, [format_ref(ref)], removed="trashed")
    except Exception as error:
        note(
            message_item(
                ref.account, ref.folder, ref.uid, envelope, audit_result(error), removed="failed"
            )
        )
        raise
    for item in result.items:
        note(item)
    for group in result.groups:
        if group.error is not None:
            raise group.error
    return "trashed"


def try_remove_draft(connections: MailConnections, ref: MessageRef, note: Note) -> Removal:
    """`remove_draft` where the work is already done, a save that went through or a message
    delivered, and whatever goes wrong here must not undo it: `failed` instead of raising."""
    try:
        return remove_draft(connections, ref, note)
    except Exception as error:
        mail_error(error, ref.account)  # logged; a MailError was noted, and needs no more
        return "failed"


def save_draft(
    connections: MailConnections,
    account: AccountConfig,
    message: MessageInput,
    *,
    cap: int,
    note: Note,
    compose_id: str | None = None,
    replaces: MessageRef | None = None,
    keep: Iterable[AttachmentSource] = (),
) -> SavedDraft:
    """Saves `message` in Drafts, as a new draft or as the next version of `replaces`.

    `message.attachments` are new files; `keep` names parts of existing messages, the draft being
    replaced included, and the two together stay under `cap`. `compose_id` keeps the Message-ID
    of every version the same; without it each save gets a new one. Anything that fails before
    the APPEND is answered, or the APPEND itself, raises with the old version untouched. After
    an APPEND error the message may be saved all the same: `find_drafts` tells. The old version
    goes only once the new one is confirmed, and failing to remove it does not fail the save.

    A host has checked the caps of `check_fields`, the recipient count and the identity, and
    that every ref in `replaces`, `keep` and `message.answers` is the caller's. `send_disabled`
    when the account's `send_mode` is `off`.
    """
    check_sendable(account)
    if replaces is not None and replaces.account != account.id:
        raise MailError("invalid_argument", "That draft belongs to another account.")
    if message.answers is not None and parse_ref(message.answers).account != account.id:
        raise MailError("invalid_argument", "The message answered belongs to another account.")
    if compose_id is not None:
        _check_compose_id(compose_id)
    uploads = [len(attachment.content) for attachment in message.attachments]
    check_uploads(uploads, cap)
    autosave = connections.capabilities(account.id).uidplus
    if replaces is not None:
        check_removable(connections, replaces)
    kept = attachments_from(connections, keep, cap, used=sum(uploads))
    draft = replace(message, draft=True, attachments=(*kept, *message.attachments))
    composed = compose(
        draft,
        None if compose_id is None else new_message_id(message.sender.address, compose_id),
    )
    folder = connections.special_folder(account.id, "drafts").path
    located = _append(
        connections, account.id, folder, composed.stored, DRAFT_FLAGS, composed.message_id
    )
    note(outgoing_item(account.id, draft, "ok", folder=folder, message_id=composed.message_id))
    ref = None if located is None else located[0]
    replaced: Removal | None = None
    if replaces is not None and ref is not None and ref != replaces:
        replaced = try_remove_draft(connections, replaces, note)
    return SavedDraft(
        ref=ref,
        folder=folder,
        message_id=composed.message_id,
        parts=() if located is None else located[1].carried,
        replaced=replaced,
        autosave=autosave,
    )
