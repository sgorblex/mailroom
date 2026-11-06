"""Moving, trashing, archiving and flagging messages: `src/mail/organize.ts`, with the grouping,
refusals and Junk training of `src/mcp/tools/organize.ts`.

Refs may come from several folders and accounts. Every one is parsed and its account checked
before anything is written; then each folder is one group, done in one mailbox session, and a
group that fails does not stop the others. Nothing is ever expunged but the messages a move
copied away, and a write is never retried.
"""

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mailroom_core.mail.audit import (
    REFUSALS,
    AuditItem,
    AuditOutcome,
    Junk,
    Removal,
    audit_outcome,
    message_item,
)
from mailroom_core.mail.errors import MailError, is_connection_error, mail_error
from mailroom_core.mail.ref import MessageRef, check_fresh, format_ref, parse_ref
from mailroom_core.render import clean_header

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from imapclient import IMAPClient

    from mailroom_core.mail.audit import AuditResult
    from mailroom_core.mail.errors import MailErrorCode
    from mailroom_core.mail.imap import Folder, MailboxContext, MailConnections

MAX_REFS: Final = 50
_SEEN: Final = b"\\Seen"
_FLAGGED: Final = b"\\Flagged"
_DELETED: Final = b"\\Deleted"
_NOT_REMOVED: Final = (
    "They were copied to the destination but may not have been removed from this folder: do not "
    "repeat the move."
)


@dataclass(frozen=True, slots=True)
class FlagState:
    """A message's flags read back after a flag change; None when that read failed (the change
    stands)."""

    ref: str
    seen: bool | None
    flagged: bool | None


@dataclass(frozen=True, slots=True)
class GroupResult:
    """What happened to the refs of one folder of one account."""

    account: str
    folder: str
    count: int = 0
    """Messages changed."""
    new_refs: tuple[str, ...] | None = None
    """A move's refs in the destination, in UID order; None when the server did not say where
    they landed (no COPYUID). Empty for a flag change, whose refs stay valid. Also set on a move
    that failed after copying them (`error` says so)."""
    junk: Junk | None = None
    flags: tuple[FlagState, ...] = ()
    """A flag change's flags afterwards."""
    error: MailError | None = None
    """None when the group succeeded."""
    destination: str | None = None
    """Where a move took them, or copied them before it failed; None for a flag change."""

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class OrganizeResult:
    groups: tuple[GroupResult, ...]
    items: tuple[AuditItem, ...]
    """One per message acted on or refused, for the host's audit record."""

    @property
    def succeeded(self) -> int:
        """Groups that succeeded."""
        return sum(group.ok for group in self.groups)

    @property
    def failed(self) -> int:
        """Groups that failed."""
        return len(self.groups) - self.succeeded

    @property
    def outcome(self) -> AuditOutcome:
        return audit_outcome(item.result for item in self.items)


@dataclass(frozen=True, slots=True)
class _Group:
    """Refs of one folder of one account: what one mailbox session acts on."""

    account: str
    folder: str
    refs: tuple[MessageRef, ...]


type _Note = Callable[[AuditItem], None]


def move_messages(connections: MailConnections, refs: Sequence[str], folder: str) -> OrganizeResult:
    """Moves to `folder`, an existing selectable folder of each ref's account (INBOX in any
    case), never Drafts or Sent. Into Junk teaches the spam filter `spam`, out of Junk (except
    into Trash) `ham`."""
    groups = _groups(connections, refs)
    folders_of = _folder_lookup(connections)

    def act(group: _Group, note: _Note) -> GroupResult:
        folders = folders_of(group.account)
        target = next(
            (f for f in folders if f.path == folder or (_is_inbox(f.path) and _is_inbox(folder))),
            None,
        )
        if target is None or not target.selectable:
            raise MailError(
                "mailbox_missing",
                f'"{clean_header(folder, 200)}" is not a folder of {group.account}; see folders.',
            )
        return _move_to(connections, group, folders, target, note)

    return _each_group(groups, act)


def archive_messages(connections: MailConnections, refs: Sequence[str]) -> OrganizeResult:
    """Moves to each account's folder with role `archive`, as `move_messages` would."""
    groups = _groups(connections, refs)
    folders_of = _folder_lookup(connections)

    def act(group: _Group, note: _Note) -> GroupResult:
        folders = folders_of(group.account)
        archive = next((f for f in folders if f.role == "archive" and f.selectable), None)
        if archive is None:
            raise MailError("unsupported", f"{group.account} has no Archive folder.")
        return _move_to(connections, group, folders, archive, note)

    return _each_group(groups, act)


def trash_messages(
    connections: MailConnections, refs: Sequence[str], *, removed: Removal | None = None
) -> OrganizeResult:
    """Moves to each account's Trash; what is in Trash already stays, never deleted. `removed`
    tags the audit items, when this is how a draft is removed: `failed` on those it failed for."""
    groups = _groups(connections, refs)
    folders_of = _folder_lookup(connections)

    def act(group: _Group, note: _Note) -> GroupResult:
        trash = next((f for f in folders_of(group.account) if f.role == "trash"), None)
        if trash is None:
            raise MailError("unsupported", f"{group.account} has no Trash folder.")
        if trash.path == group.folder:
            raise MailError(
                "invalid_argument",
                "They are already in Trash; mailroom never deletes permanently.",
            )
        return _move(connections, group, trash.path, None, note, removed=removed)

    return _each_group(groups, act, removed=None if removed is None else "failed")


def set_flags(
    connections: MailConnections,
    refs: Sequence[str],
    *,
    seen: bool | None = None,
    flagged: bool | None = None,
) -> OrganizeResult:
    """Sets (True) or clears (False) \\Seen and \\Flagged; None leaves that flag as it is. Refs
    stay valid."""
    if seen is None and flagged is None:
        raise MailError("invalid_argument", "Give `seen`, `flagged` or both.")
    groups = _groups(connections, refs)
    add = [flag for flag, wanted in ((_SEEN, seen), (_FLAGGED, flagged)) if wanted is True]
    remove = [flag for flag, wanted in ((_SEEN, seen), (_FLAGGED, flagged)) if wanted is False]

    def act(group: _Group, note: _Note) -> GroupResult:
        def run(context: MailboxContext) -> GroupResult:
            for ref in group.refs:
                check_fresh(ref, context.uid_validity)
            client = context.client
            envelopes = _envelopes(client, group)
            uids = sorted(envelopes)

            def note_all(result: AuditResult) -> None:
                for uid in uids:
                    note(
                        message_item(
                            group.account,
                            group.folder,
                            uid,
                            envelopes[uid],
                            result,
                            seen=seen,
                            flagged=flagged,
                        )
                    )

            try:
                if add:
                    client.add_flags(uids, add, silent=True)
                if remove:
                    client.remove_flags(uids, remove, silent=True)
            except Exception as error:
                mapped = mail_error(error, group.account)
                note_all(mapped.code)
                raise mapped from None
            note_all("ok")

            def ref_of(uid: int) -> str:
                return format_ref(
                    MessageRef(group.account, group.folder, context.uid_validity, uid)
                )

            try:
                after = client.fetch(uids, ["FLAGS"])
                read_back = {
                    uid: tuple(after[uid].get(b"FLAGS", ())) for uid in uids if uid in after
                }
                states = tuple(
                    FlagState(ref_of(uid), _SEEN in flags, _FLAGGED in flags)
                    for uid, flags in read_back.items()
                )
            except Exception as error:
                mail_error(error, group.account)  # logged; the change stands
                if is_connection_error(error):
                    # Its state is unknown: the next caller's SELECT fails and reconnects.
                    with suppress(OSError):
                        client.shutdown()
                states = tuple(FlagState(ref_of(uid), None, None) for uid in uids)
            return GroupResult(
                group.account, group.folder, count=len(uids), new_refs=(), flags=states
            )

        return connections.with_mailbox(group.account, group.folder, False, run)

    return _each_group(groups, act)


def _groups(connections: MailConnections, texts: Sequence[str]) -> list[_Group]:
    """Every ref parsed and its account checked, before anything is written: one malformed ref,
    or one for an account these connections do not serve, refuses the whole call."""
    if not 1 <= len(texts) <= MAX_REFS:
        raise MailError("invalid_argument", f"Give between 1 and {MAX_REFS} message refs.")
    by_folder: dict[tuple[str, str], list[MessageRef]] = {}
    for text in texts:
        ref = parse_ref(text)
        if not connections.serves(ref.account):
            raise MailError(
                "invalid_argument", f'Unknown account "{clean_header(ref.account, 200)}".'
            )
        by_folder.setdefault((ref.account, ref.folder), []).append(ref)
    return [_Group(account, folder, tuple(refs)) for (account, folder), refs in by_folder.items()]


def _each_group(
    groups: Sequence[_Group],
    act: Callable[[_Group, _Note], GroupResult],
    *,
    removed: Removal | None = None,
) -> OrganizeResult:
    """`act` per group; a group that fails is noted as failed for every ref it had not noted, with
    `removed` as its tag."""
    items: list[AuditItem] = []
    results: list[GroupResult] = []
    for group in groups:
        noted = len(items)
        try:
            results.append(act(group, items.append))
        except _CopiedNotRemovedError as error:
            results.append(error.result)
        except MailError as error:
            if len(items) == noted:
                items.extend(
                    AuditItem(
                        account=group.account,
                        folder=group.folder,
                        uid=ref.uid,
                        removed=removed,
                        result=error.code,
                    )
                    for ref in group.refs
                )
            results.append(GroupResult(group.account, group.folder, error=error))
    return OrganizeResult(tuple(results), tuple(items))


class _CopiedNotRemovedError(MailError):
    """A move without MOVE that copied the messages but could not remove them from the source:
    the group failed, and `result` says where the copies are. Its items are noted already."""

    def __init__(self, error: MailError, result: GroupResult) -> None:
        super().__init__(error.code, error.detail)
        self.result = result


def _folder_lookup(connections: MailConnections) -> Callable[[str], list[Folder]]:
    """The account's folders, listed once per account per call."""
    cache: dict[str, list[Folder]] = {}

    def lookup(account: str) -> list[Folder]:
        if account not in cache:
            cache[account] = connections.folders(account)
        return cache[account]

    return lookup


def _is_inbox(path: str) -> bool:
    return path.upper() == "INBOX"


def _move_to(
    connections: MailConnections,
    group: _Group,
    folders: Sequence[Folder],
    target: Folder,
    note: _Note,
) -> GroupResult:
    """A move's refusals and Junk training, then the move."""
    if target.path == group.folder:
        raise MailError("invalid_argument", "They are already in that folder.")
    # Drafts are what a draft send sends, and Sent is the owner's own history: neither may be
    # filled with someone else's message.
    if target.role in ("drafts", "sent"):
        raise MailError(
            "invalid_argument",
            "Messages cannot be moved into Drafts or Sent; drafts come only from the draft tools.",
        )
    source = next((f for f in folders if f.path == group.folder), None)
    # Mailu's imapsieve learns spam on the way into Junk and ham on the way out, except into
    # Trash.
    junk: Junk | None = None
    if target.role == "junk":
        junk = "spam"
    elif source is not None and source.role == "junk" and target.role != "trash":
        junk = "ham"
    return _move(connections, group, target.path, junk, note)


def _move(
    connections: MailConnections,
    group: _Group,
    destination: str,
    junk: Junk | None,
    note: _Note,
    *,
    removed: Removal | None = None,
) -> GroupResult:
    """Moves the group's messages to `destination` and notes each one. Without MOVE it copies,
    marks \\Deleted and expunges exactly those UIDs, which only UIDPLUS allows: a server with
    neither is refused, since a plain EXPUNGE would also remove other messages marked
    \\Deleted. When the copy succeeded and the removal did not, the group fails with the copies'
    refs and an error saying not to repeat the move."""

    def run(context: MailboxContext) -> GroupResult:
        for ref in group.refs:
            check_fresh(ref, context.uid_validity)
        client = context.client
        move = client.has_capability("MOVE")
        if not move and not client.has_capability("UIDPLUS"):
            raise MailError(
                "unsupported",
                "This server can only move mail by also deleting other messages marked for "
                "deletion.",
            )
        envelopes = _envelopes(client, group)
        uids = sorted(envelopes)

        def note_all(result: AuditResult, *, copied: bool | None = None) -> None:
            for uid in uids:
                note(
                    message_item(
                        group.account,
                        group.folder,
                        uid,
                        envelopes[uid],
                        result,
                        destination=destination,
                        junk=junk,
                        copied=copied,
                        removed=removed if removed is None or result == "ok" else "failed",
                    )
                )

        def refs_of(copied: tuple[int, dict[int, int]] | None) -> tuple[str, ...] | None:
            if copied is None:
                return None
            validity, new_uids = copied
            return tuple(
                format_ref(MessageRef(group.account, destination, validity, new_uids[uid]))
                for uid in uids
                if uid in new_uids
            )

        try:
            copied = _transfer(client, uids, destination, move=move)
        except _NotRemovedError as failure:
            # The copies exist: a failure, but one that must not be repeated.
            code: MailErrorCode = mail_error(failure.cause, group.account).code
            if code in REFUSALS:
                code = "server_error"  # something was written
            note_all(code, copied=True)
            new_refs = refs_of(failure.copied)
            # The refs are core's own text, so the error carries them: a host that raises a
            # lone folder's error still shows where the copies are.
            failed = MailError(
                code,
                f"{_NOT_REMOVED} New refs: {', '.join(new_refs)}" if new_refs else _NOT_REMOVED,
            )
            raise _CopiedNotRemovedError(
                failed,
                GroupResult(
                    group.account,
                    group.folder,
                    new_refs=new_refs,
                    junk=junk,
                    error=failed,
                    destination=destination,
                ),
            ) from None
        except Exception as error:
            mapped = mail_error(error, group.account)
            note_all(mapped.code)
            raise mapped from None
        note_all("ok")
        return GroupResult(
            group.account,
            group.folder,
            count=len(uids),
            new_refs=refs_of(copied),
            junk=junk,
            destination=destination,
        )

    return connections.with_mailbox(group.account, group.folder, False, run)


def _envelopes(client: IMAPClient, group: _Group) -> dict[int, object]:
    """The ENVELOPE of each of the group's messages, all of which must still be in the selected
    folder."""
    wanted = list(dict.fromkeys(ref.uid for ref in group.refs))
    fetched = client.fetch(wanted, ["ENVELOPE"])
    found = {uid: fetched[uid].get(b"ENVELOPE") for uid in wanted if uid in fetched}
    missing = sum(1 for ref in group.refs if ref.uid not in found)
    if missing > 0:
        raise MailError(
            "not_found",
            f"{missing} of these messages are no longer in {clean_header(group.folder, 200)}; "
            "search again.",
        )
    return found


def _transfer(
    client: IMAPClient, uids: list[int], destination: str, *, move: bool
) -> tuple[int, dict[int, int]] | None:
    """MOVE, or UID COPY + STORE \\Deleted + UID EXPUNGE of exactly `uids`; returns the
    destination's UIDVALIDITY and each old UID's new one, when the server reported them
    (COPYUID, untagged for MOVE and tagged for COPY: imaplib files both under its code). A
    failure after the COPY raises `_NotRemovedError`."""
    # imapclient's move() and copy() drop the response codes; imaplib keeps them here.
    responses = client._imap.untagged_responses  # noqa: SLF001
    responses.pop("COPYUID", None)
    if move:
        client.move(uids, destination)
        return _copy_uids(responses.pop("COPYUID", None), len(uids))
    client.copy(uids, destination)
    copied = _copy_uids(responses.pop("COPYUID", None), len(uids))
    try:
        client.add_flags(uids, [_DELETED], silent=True)
        client.uid_expunge(uids)
    except Exception as error:
        raise _NotRemovedError(error, copied) from error
    return copied


class _NotRemovedError(Exception):
    """The COPY of a move without MOVE succeeded, the removal from the source did not."""

    def __init__(self, cause: Exception, copied: tuple[int, dict[int, int]] | None) -> None:
        super().__init__(type(cause).__name__)
        self.cause = cause
        self.copied = copied


def _copy_uids(data: Sequence[bytes] | None, count: int) -> tuple[int, dict[int, int]] | None:
    """A COPYUID response code's data (`<uidvalidity> <source set> <destination set>`, RFC
    4315; the last one when several came) as the destination's UIDVALIDITY and old UID → new
    UID; None when there is none, or it does not parse or names more than `count` messages."""
    if not data:
        return None
    try:
        validity, source, target = data[-1].split()
        pairs = zip(_uid_set(source, count), _uid_set(target, count), strict=True)
        return int(validity), dict(pairs)
    except ValueError:
        return None


def _uid_set(text: bytes, limit: int) -> list[int]:
    """A UID set of numbers and ranges, in its order (the two sets of COPYUID correspond);
    ValueError past `limit` UIDs, so a hostile range cannot fill memory."""
    out: list[int] = []
    for piece in text.split(b","):
        low, _, high = piece.partition(b":")
        first, last = int(low), int(high or low)
        if len(out) + abs(last - first) + 1 > limit:
            raise ValueError("more UIDs than were copied")
        out.extend(range(first, last + 1) if first <= last else range(first, last - 1, -1))
    return out
