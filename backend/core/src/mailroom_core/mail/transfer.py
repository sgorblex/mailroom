"""Moving messages to a folder of another account the connections serve: each is copied there
with its flags and arrival date, the copy confirmed by its Message-ID, then removed from where
it was, to that account's Trash or, when the caller asks, for good.

Each source folder's messages go in chunks of at most `max_bytes`, each in three sessions that
never overlap: reading the chunk from the source, writing it to the target, then removing what
arrived. Holding one account's session while opening another's could deadlock against a move
the other way round.

A message already in the target folder (the same Message-ID, checked on its ENVELOPE) is not
copied again, so a move cut off after its copy can be repeated without copying twice. Nothing is
removed from the source before its copy is confirmed; a removal that fails leaves the message in
both, which the group's error says, with the copies' refs. Deleting for good expunges exactly
the moved UIDs (UIDPLUS); on Gmail, where that would only drop a label, it moves them to Trash
and expunges them there.
"""

from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Final, Literal

from mailroom_core.mail._mime import text_value
from mailroom_core.mail.audit import AuditResult, Removal, message_item
from mailroom_core.mail.errors import MailError, mail_error
from mailroom_core.mail.organize import (
    GroupResult,
    OrganizeResult,
    _CopiedGroupError,
    _each_group,
    _envelopes,
    _folder_lookup,
    _Group,
    _groups,
)
from mailroom_core.mail.ref import MessageRef, check_fresh, format_ref
from mailroom_core.mail.selected import (
    CopiedNotRemovedError,
    abandon_read_back,
    append_raw,
    delete_uids,
    fetch_envelopes,
    fetch_source,
    message_id_uids,
    move_target,
    move_uids,
    special_folder_in,
)
from mailroom_core.render import clean_header, format_size

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from mailroom_core.mail.audit import Note
    from mailroom_core.mail.imap import MailboxContext, MailConnections
    from mailroom_core.mail.selected import MessageSource

type TransferRemoval = Literal["trash", "delete"]
"""What becomes of a moved message where it was: moved to Trash, or expunged for good."""

_NOT_COPIED: Final = frozenset({"\\Recent", "\\Deleted"})


@dataclass(frozen=True, slots=True)
class _Planned:
    """A message to move, as the source had it before anything was written."""

    uid: int
    envelope: object
    message_id: str
    size: int


@dataclass(frozen=True, slots=True)
class _Plan:
    """Where a group's messages go, and how they leave."""

    account: str
    folder: str
    removal: TransferRemoval
    trash: str | None
    gmail_source: bool
    gmail_target: bool
    max_bytes: int


@dataclass(slots=True)
class _Written:
    """What one chunk's session on the target confirmed, and the error that cut it short."""

    uid_validity: int
    copies: dict[int, int] = field(default_factory=dict)
    """Each source UID whose copy is confirmed, with the copy's UID."""
    error: MailError | None = None
    uncertain: int | None = None
    """The source UID being copied when `error` came: its copy may exist, unconfirmed."""


def move_to_account(
    connections: MailConnections,
    refs: Sequence[str],
    account: str,
    folder: str,
    *,
    removal: TransferRemoval,
    max_bytes: int,
) -> OrganizeResult:
    """Moves to `folder` of `account`, another account than any ref's, the target rules of
    `move_messages` applying (never Drafts or Sent). Each message is copied with its flags and
    arrival date, confirmed by Message-ID, then removed per `removal`. Refused before anything
    is written: a message with no Message-ID (`unsupported`: its copy could not be confirmed) or
    over `max_bytes` (`too_large`) refuses its folder's group; `delete` needs UIDPLUS on the
    source, `trash` its Trash and MOVE or UIDPLUS. Each moved message gives two audit items, the
    source's and the copy's."""
    groups = _groups(connections, refs)
    if not connections.serves(account):
        raise MailError("invalid_argument", f'Unknown account "{clean_header(account, 200)}".')
    if any(group.account == account for group in groups):
        raise MailError(
            "invalid_argument",
            f"Some of these messages are in {account} already: move them within it.",
        )
    folders_of = _folder_lookup(connections)
    target = move_target(None, folder, folders_of(account), account=account)
    gmail_target = connections.capabilities(account).gmail
    plans: dict[str, _Plan] = {}
    for source in dict.fromkeys(group.account for group in groups):
        capabilities = connections.capabilities(source)
        trash = special_folder_in(folders_of(source), "trash")
        if removal == "delete" and not capabilities.uidplus:
            raise MailError(
                "unsupported", f"{source} cannot delete exactly these messages for good."
            )
        if trash is None and (removal == "trash" or capabilities.gmail):
            raise MailError("unsupported", f"{source} has no Trash folder.")
        if removal == "trash" and not (capabilities.move or capabilities.uidplus):
            raise MailError(
                "unsupported", f"{source} can only move mail by also deleting other messages."
            )
        plans[source] = _Plan(
            account=account,
            folder=target.path,
            removal=removal,
            trash=None if trash is None else trash.path,
            gmail_source=capabilities.gmail,
            gmail_target=gmail_target,
            max_bytes=max_bytes,
        )

    # The target's messages taken as a copy so far, whatever the chunk or source folder: two
    # messages sharing a Message-ID each need a copy of their own.
    taken: set[int] = set()

    def act(group: _Group, note: Note) -> GroupResult:
        return _transfer(connections, group, plans[group.account], taken, note)

    return _each_group(groups, act)


def _transfer(
    connections: MailConnections, group: _Group, plan: _Plan, taken: set[int], note: Note
) -> GroupResult:
    """One source folder's messages, chunk by chunk; stops at the first error, noting what it
    did not reach as failed with that error."""
    planned = connections.with_mailbox(
        group.account, group.folder, True, lambda context: _planned(context, group, plan)
    )
    moved = 0
    copies: dict[int, int] = {}
    in_both: list[int] = []
    trashed: list[int] = []
    target_validity: int | None = None
    failure: MailError | None = None
    unconfirmed = False
    left = list(planned)

    def note_source(
        item: _Planned,
        result: AuditResult,
        *,
        removed: Removal | None = None,
        copied: bool | None = None,
    ) -> None:
        note(
            message_item(
                group.account,
                group.folder,
                item.uid,
                item.envelope,
                result,
                destination=plan.folder,
                destination_account=plan.account,
                removed=removed,
                copied=copied,
            )
        )

    for chunk in _chunks(planned, plan.max_bytes):
        try:
            sources = connections.with_mailbox(
                group.account,
                group.folder,
                True,
                partial(_read, group=group, chunk=chunk, plan=plan),
            )
            written = connections.with_mailbox(
                plan.account,
                plan.folder,
                False,
                partial(_write, chunk=chunk, sources=sources, plan=plan, taken=taken),
            )
        except Exception as error:
            failure = mail_error(error, group.account)
            break
        target_validity = written.uid_validity
        confirmed = [item for item in chunk if item.uid in written.copies]
        removed, removal_error = _remove_originals(connections, group, confirmed, plan)
        for item in confirmed:
            copy = written.copies[item.uid]
            copies[item.uid] = copy
            note(
                message_item(
                    plan.account,
                    plan.folder,
                    copy,
                    item.envelope,
                    "ok",
                    source=(group.account, group.folder),
                )
            )
            if removal_error is None:
                moved += 1
                note_source(item, "ok", removed=removed)
            else:
                # On Gmail, deleting for good can stop between Trash and the expunge there.
                (trashed if removed == "trashed" else in_both).append(item.uid)
                note_source(item, removal_error.code, copied=True, removed=removed or "failed")
        left = left[len(chunk) :]
        if written.error is not None or removal_error is not None:
            failure = removal_error or written.error
            unconfirmed = written.uncertain is not None
            left = [item for item in chunk if item.uid not in written.copies] + left
            break
    if failure is not None:
        for item in left:
            note_source(item, failure.code)
    new_refs = (
        None
        if target_validity is None
        else tuple(
            format_ref(MessageRef(plan.account, plan.folder, target_validity, copies[uid]))
            for uid in sorted(copies)
        )
    )
    result = GroupResult(
        group.account,
        group.folder,
        count=moved,
        new_refs=new_refs,
        destination=plan.folder,
        destination_account=plan.account,
    )
    if failure is None:
        return result
    stopped = _failure(
        failure, plan, moved, len(planned), in_both, trashed, copies, new_refs, unconfirmed
    )
    raise _CopiedGroupError(
        stopped,
        GroupResult(
            group.account,
            group.folder,
            count=moved,
            new_refs=new_refs,
            error=stopped,
            destination=plan.folder,
            destination_account=plan.account,
        ),
    )


def _failure(
    failure: MailError,
    plan: _Plan,
    moved: int,
    total: int,
    in_both: Sequence[int],
    trashed: Sequence[int],
    copies: dict[int, int],
    new_refs: tuple[str, ...] | None,
    unconfirmed: bool,
) -> MailError:
    """The group's error: what went wrong, how far it got, and where copies were left."""
    parts = [failure.detail or "", f"Moved {moved} of {total} to {plan.account}."]
    if in_both:
        parts.append(
            f"{len(in_both)} were copied there but not removed from here, so they are in both: "
            "do not move them again."
        )
    if trashed:
        parts.append(
            f"{len(trashed)} were copied there and moved to Trash here, but not deleted for good."
        )
    if unconfirmed:
        parts.append(
            "One may have been copied there without being confirmed: moving it again copies "
            "nothing twice."
        )
    if in_both and new_refs:
        ordered = sorted(copies)
        parts.append(
            "New refs: " + ", ".join(new_refs[ordered.index(uid)] for uid in sorted(in_both))
        )
    return MailError(failure.code, " ".join(part for part in parts if part))


def _planned(context: MailboxContext, group: _Group, plan: _Plan) -> list[_Planned]:
    """The group's messages with their Message-IDs and sizes; refuses the group before
    anything is written when one is gone, has no Message-ID or is over the cap."""
    for ref in group.refs:
        check_fresh(ref, context.uid_validity)
    envelopes = _envelopes(context, group)
    uids = sorted(envelopes)
    fetched = context.client.fetch(uids, ["RFC822.SIZE"])
    planned: list[_Planned] = []
    unnamed = over = 0
    for uid in uids:
        message_id = text_value(getattr(envelopes[uid], "message_id", None)).strip()
        size = fetched.get(uid, {}).get(b"RFC822.SIZE")
        size = size if isinstance(size, int) else 0
        unnamed += not message_id
        over += size > plan.max_bytes
        planned.append(_Planned(uid, envelopes[uid], message_id, size))
    if unnamed:
        raise MailError(
            "unsupported",
            f"{unnamed} of these have no Message-ID, so a copy of them could not be confirmed.",
        )
    if over:
        raise MailError(
            "too_large",
            f"{over} of these are over the {format_size(plan.max_bytes)} a moved message may have.",
        )
    return planned


def _chunks(planned: Sequence[_Planned], max_bytes: int) -> Iterator[list[_Planned]]:
    """`planned` in order, in runs of at most `max_bytes` (at least one message each)."""
    chunk: list[_Planned] = []
    size = 0
    for item in planned:
        if chunk and size + item.size > max_bytes:
            yield chunk
            chunk, size = [], 0
        chunk.append(item)
        size += item.size
    if chunk:
        yield chunk


def _read(
    context: MailboxContext, group: _Group, chunk: Sequence[_Planned], plan: _Plan
) -> dict[int, MessageSource]:
    for ref in group.refs:
        check_fresh(ref, context.uid_validity)
    return {item.uid: fetch_source(context, item.uid, plan.max_bytes) for item in chunk}


def _write(
    context: MailboxContext,
    chunk: Sequence[_Planned],
    sources: dict[int, MessageSource],
    plan: _Plan,
    taken: set[int],
) -> _Written:
    """Copies the chunk into the selected target folder, each copy confirmed by Message-ID; one
    already there, of the same size and not `taken` for another message, is taken as the copy.
    Stops at the first error, which it returns."""
    written = _Written(context.uid_validity)
    first = context.uid_next or 0
    for item in chunk:
        try:
            copy = _existing_copy(context, item.message_id, taken, 0, plan.gmail_target, item.size)
            if copy is None:
                source = sources[item.uid]
                flags = sorted(source.flags - _NOT_COPIED)
                appended = append_raw(
                    context, source.source, flags, internal_date=source.internal_date
                )
                # RFC 3501, 6.3.11: the new message need not be in view until a NOOP.
                context.client.noop()
                if appended is not None and appended not in taken:
                    found = fetch_envelopes(context, [appended]).get(appended)
                    if _message_id(found) == item.message_id:
                        copy = appended
                if copy is None:
                    copy = _existing_copy(
                        context, item.message_id, taken, first, plan.gmail_target, None
                    )
                if copy is None:
                    written.uncertain = item.uid
                    written.error = MailError(
                        "server_error", f"A copy in {plan.account} could not be confirmed."
                    )
                    return written
        except Exception as error:
            written.uncertain = item.uid
            written.error = abandon_read_back(context, error)
            return written
        written.copies[item.uid] = copy
        taken.add(copy)
    return written


def _existing_copy(
    context: MailboxContext,
    message_id: str,
    taken: set[int],
    since_uid: int,
    gmail: bool,
    size: int | None,
) -> int | None:
    """The newest message of the selected folder from `since_uid` on carrying `message_id`, as
    its ENVELOPE says, of `size` bytes when given, and not taken by another message of this
    call. The size keeps a different message that shares the Message-ID (a list's copy of a
    message, or one written to look like it) from passing for the copy."""
    found = [
        uid
        for uid in message_id_uids(context, message_id, since_uid=since_uid, gmail=gmail)
        if uid not in taken
    ]
    if not found:
        return None
    fetched = context.client.fetch(found, ["ENVELOPE", "RFC822.SIZE"])
    matching = [
        uid
        for uid, data in fetched.items()
        if _message_id(data.get(b"ENVELOPE")) == message_id
        and (size is None or data.get(b"RFC822.SIZE") == size)
    ]
    return max(matching, default=None)


def _message_id(envelope: object) -> str:
    return text_value(getattr(envelope, "message_id", None)).strip()


def _remove_originals(
    connections: MailConnections, group: _Group, confirmed: Sequence[_Planned], plan: _Plan
) -> tuple[Removal | None, MailError | None]:
    """Removes the messages whose copies are confirmed, as `plan.removal` says: how they left,
    and the error that stopped it (None when they did). On Gmail, deleting for good is a move
    to Trash, then an expunge there: a failure in between leaves them `trashed`."""
    if not confirmed:
        return None, None
    uids = [item.uid for item in confirmed]
    in_trash = plan.trash == group.folder
    removed: Removal = "trashed" if plan.removal == "trash" else "expunged"

    def run(context: MailboxContext) -> tuple[int, dict[int, int]] | None:
        for ref in group.refs:
            check_fresh(ref, context.uid_validity)
        present = sorted(fetch_envelopes(context, uids))
        if not present:
            return None  # gone already, by another client or a filter
        if plan.removal == "trash":
            if not in_trash and present:
                move_uids(context, present, plan.trash or "")
            return None
        if plan.gmail_source and not in_trash:
            moved = move_uids(context, present, plan.trash or "")
            if moved.uid_validity is None or len(moved.new_uids) != len(present):
                raise MailError(
                    "server_error",
                    "They were moved to Trash, but where is not known, so not deleted for good.",
                )
            return moved.uid_validity, dict(moved.new_uids)
        delete_uids(context, present)
        return None

    try:
        in_gmail_trash = connections.with_mailbox(group.account, group.folder, False, run)
    except CopiedNotRemovedError as error:
        # Copied to Trash, not removed: its detail speaks of a move's destination, here Trash.
        return None, MailError(error.code)
    except Exception as error:
        return None, mail_error(error, group.account)
    if in_gmail_trash is None:
        return removed, None
    validity, new_uids = in_gmail_trash

    def expunge(context: MailboxContext) -> None:
        if context.uid_validity != validity:
            raise MailError("stale_reference")
        delete_uids(context, sorted(new_uids.values()))

    try:
        connections.with_mailbox(group.account, plan.trash or "", False, expunge)
    except Exception as error:
        return "trashed", mail_error(error, group.account)
    return removed, None
