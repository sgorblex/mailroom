"""Moving, trashing, archiving and flagging messages: `src/mail/organize.ts`, with the grouping,
refusals and Junk training of `src/mcp/tools/organize.ts`, on the operations of `selected`.

Refs may come from several folders and accounts. Every one is parsed and its account checked
before anything is written; then each folder is one group, done in one mailbox session, and a
group that fails does not stop the others. Nothing is ever expunged but the messages a move
copied away, and a write is never retried.
"""

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mailroom_core.mail.audit import (
    AuditItem,
    AuditOutcome,
    Junk,
    Removal,
    audit_outcome,
    message_item,
)
from mailroom_core.mail.errors import MailError, is_connection_error, mail_error
from mailroom_core.mail.ref import MessageRef, check_fresh, format_ref, parse_ref
from mailroom_core.mail.selected import (
    CopiedNotRemovedError,
    check_movable,
    fetch_envelopes,
    move_target,
    move_uids,
    special_folder_in,
    store_flags,
)
from mailroom_core.render import clean_header

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mailroom_core.mail.audit import AuditResult
    from mailroom_core.mail.imap import Folder, MailboxContext, MailConnections
    from mailroom_core.mail.selected import Moved

MAX_REFS: Final = 50
_SEEN: Final = "\\Seen"
_FLAGGED: Final = "\\Flagged"


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
        """Whether the group succeeded."""
        return self.error is None


@dataclass(frozen=True, slots=True)
class OrganizeResult:
    """The result of an organize call: one result per group, one audit item per message."""

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
        """The call's audit outcome, from its items' results."""
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
        target = move_target(group.folder, folder, folders, account=group.account)
        return _move_to(connections, group, folders, target, note)

    return _each_group(groups, act)


def archive_messages(connections: MailConnections, refs: Sequence[str]) -> OrganizeResult:
    """Moves to each account's folder with role `archive`, as `move_messages` would."""
    groups = _groups(connections, refs)
    folders_of = _folder_lookup(connections)

    def act(group: _Group, note: _Note) -> GroupResult:
        folders = folders_of(group.account)
        archive = special_folder_in(folders, "archive")
        if archive is None or not archive.selectable:
            raise MailError("unsupported", f"{group.account} has no Archive folder.")
        target = move_target(group.folder, archive.path, folders, account=group.account)
        return _move_to(connections, group, folders, target, note)

    return _each_group(groups, act)


def trash_messages(
    connections: MailConnections, refs: Sequence[str], *, removed: Removal | None = None
) -> OrganizeResult:
    """Moves to each account's Trash; what is in Trash already stays, never deleted. `removed`
    tags the audit items, when this is how a draft is removed: `failed` on those it failed for."""
    groups = _groups(connections, refs)
    folders_of = _folder_lookup(connections)

    def act(group: _Group, note: _Note) -> GroupResult:
        trash = special_folder_in(folders_of(group.account), "trash")
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
            envelopes = _envelopes(context, group)
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
                store_flags(context, uids, add=add, remove=remove)
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
                    FlagState(ref_of(uid), _SEEN.encode() in flags, _FLAGGED.encode() in flags)
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
        except _CopiedGroupError as error:
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


class _CopiedGroupError(MailError):
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


def _move_to(
    connections: MailConnections,
    group: _Group,
    folders: Sequence[Folder],
    target: Folder,
    note: _Note,
) -> GroupResult:
    """A move's Junk training, then the move; `move_target` refused what it had to."""
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
    """Moves the group's messages to `destination` with `move_uids` and notes each one. When
    the copy succeeded and the removal did not, the group fails with the copies' refs and an
    error saying not to repeat the move."""

    def run(context: MailboxContext) -> GroupResult:
        for ref in group.refs:
            check_fresh(ref, context.uid_validity)
        check_movable(context)
        envelopes = _envelopes(context, group)
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

        def refs_of(moved: Moved) -> tuple[str, ...] | None:
            validity = moved.uid_validity
            if validity is None:
                return None
            return tuple(
                format_ref(MessageRef(group.account, destination, validity, moved.new_uids[uid]))
                for uid in uids
                if uid in moved.new_uids
            )

        try:
            moved = move_uids(context, uids, destination)
        except CopiedNotRemovedError as failure:
            # The copies exist: a failure, but one that must not be repeated.
            note_all(failure.code, copied=True)
            new_refs = refs_of(failure.moved)
            # The refs are core's own text, so the error carries them: a host that raises a
            # lone folder's error still shows where the copies are.
            detail = failure.detail or ""
            failed = MailError(
                failure.code, f"{detail} New refs: {', '.join(new_refs)}" if new_refs else detail
            )
            raise _CopiedGroupError(
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
            new_refs=refs_of(moved),
            junk=junk,
            destination=destination,
        )

    return connections.with_mailbox(group.account, group.folder, False, run)


def _envelopes(context: MailboxContext, group: _Group) -> dict[int, object]:
    """The ENVELOPE of each of the group's messages, all of which must still be in the selected
    folder."""
    found = fetch_envelopes(context, (ref.uid for ref in group.refs))
    missing = sum(1 for ref in group.refs if ref.uid not in found)
    if missing > 0:
        raise MailError(
            "not_found",
            f"{missing} of these messages are no longer in {clean_header(group.folder, 200)}; "
            "search again.",
        )
    return dict(found)
