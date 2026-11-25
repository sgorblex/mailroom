"""Acting on every message a search matches rather than on refs: counting them, then flagging,
moving or trashing them a batch at a time, on the paths of `organize`.

The caller shows the count and the user confirms it. Each call searches again and refuses
(`count_changed`) when the count is no longer the one confirmed, and past `BULK_MAX`
(`too_many`); otherwise it acts on at most `BULK_BATCH` of the messages and says how many
remain, the count the caller confirms on its next call. The count is of what the action still
changes: a flag change leaves out the messages that already have it, a move over several folders
leaves out its destination and All Mail (`_moved_from`), and a move or a trash is refused when
its destination is still one of the folders searched, so what was done no longer matches and a
repeated call never acts on a message twice. A move or a trash also leaves out messages
marked `\\Deleted`, as a move without MOVE leaves them when its expunge fails. A group that was
copied but not removed from its folder (`CopiedNotRemovedError`) is not counted as remaining
and must not be repeated: its messages are in both folders.
"""

from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Final

from mailroom_core.mail.audit import AuditItem
from mailroom_core.mail.errors import MailError
from mailroom_core.mail.handled import HandledKey
from mailroom_core.mail.organize import (
    _each_group,
    _flag_group,
    _Group,
    _junk,
    _move,
)
from mailroom_core.mail.pages import PageFailure
from mailroom_core.mail.ref import MessageRef
from mailroom_core.mail.search import ALL_FOLDERS, _any_of, check_subfolders, search_sources
from mailroom_core.mail.selected import move_target, search_uids, special_folder_in

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mailroom_core.mail.audit import Note
    from mailroom_core.mail.errors import MailErrorCode
    from mailroom_core.mail.imap import Folder, FolderRole, MailboxContext, MailConnections
    from mailroom_core.mail.organize import GroupResult
    from mailroom_core.mail.search import SearchQuery
    from mailroom_core.mail.selected import Criteria

BULK_BATCH: Final = 500
"""The most messages one call acts on."""
BULK_MAX: Final = 5000
"""The most messages a call acts on the remainder of: past it, `too_many`."""
_STAYING_ROLES: Final[frozenset[FolderRole]] = frozenset(
    {"flagged", "sent", "drafts", "all", "junk", "trash"}
)
"""Roles of the folders a move over an account with All Mail does not take mail out of."""
_IMPORTANT: Final = "\\important"
"""Gmail's Important (RFC 8457), a view as Starred is, which a move does not take mail out of."""


@dataclass(frozen=True, slots=True)
class BulkFlag:
    """Set (True) or clear (False) `\\Seen`, `\\Flagged` and the Handled mark; at least one."""

    seen: bool | None = None
    flagged: bool | None = None
    handled: bool | None = None


@dataclass(frozen=True, slots=True)
class BulkMove:
    """Move to `folder` of each message's account, never Drafts or Sent."""

    folder: str


@dataclass(frozen=True, slots=True)
class BulkTrash:
    """Move to each account's Trash."""


type BulkAction = BulkFlag | BulkMove | BulkTrash


class MatchCountError(MailError):
    """A bulk call refused before anything was written: `count_changed` when the scope no
    longer matches the count the caller confirmed, `too_many` past `BULK_MAX`. `matching` is the
    count now."""

    matching: int

    def __init__(self, code: MailErrorCode, matching: int) -> None:
        """Sets the count now, which the message names."""
        self.matching = matching
        noun = "message matches" if matching == 1 else "messages match"
        detail = (
            f"{matching} {noun} now; nothing was changed."
            if code == "count_changed"
            else f"{matching} {noun}; at most {BULK_MAX} are acted on together. Narrow the search."
        )
        super().__init__(code, detail)


@dataclass(frozen=True, slots=True)
class Matching:
    """How many messages a scope matches, and the sources that could not be searched."""

    total: int
    failures: tuple[PageFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class BulkResult:
    """What one bulk call did: `done` messages, per folder a `GroupResult`, and `remaining`, the
    count to confirm on the next call (0: finished)."""

    done: int
    remaining: int
    groups: tuple[GroupResult, ...]
    failures: tuple[PageFailure, ...] = ()
    """The sources that could not be searched, as `Matching.failures`."""


@dataclass(frozen=True, slots=True)
class _Found:
    account: int
    folder: str
    uid_validity: int
    uids: tuple[int, ...]
    """Newest (highest UID) first."""


@dataclass(frozen=True, slots=True)
class _Plan:
    """Where an account's messages go: None for a flag change."""

    folders: tuple[Folder, ...] = ()
    destination: Folder | None = None


def count_matching(
    connections: MailConnections,
    accounts: Sequence[int],
    folder: str,
    query: SearchQuery,
    *,
    subfolders: bool = False,
    action: BulkAction | None = None,
) -> Matching:
    """How many messages `query` matches over `accounts` in `folder` (with `subfolders`, its
    subfolders too; `ALL_FOLDERS`: every folder a search reads), as `search_messages` reads
    them; with `action`, only those it would change, the count `act_on_matching` checks. Over one
    account a failure is raised, as it is over one folder of it; otherwise each account or
    folder that fails is a failure and the others still count."""
    _check(action)
    found, failures, _ = _find(connections, accounts, folder, query, action, subfolders)
    return Matching(sum(len(f.uids) for f in found), tuple(failures))


def act_on_matching(
    connections: MailConnections,
    accounts: Sequence[int],
    folder: str,
    query: SearchQuery,
    action: BulkAction,
    *,
    expected: int,
    note: Note,
    subfolders: bool = False,
) -> BulkResult:
    """`action` on up to `BULK_BATCH` of the messages `count_matching` with `action` counts,
    newest first in each folder, the folders in search order: refused (`MatchCountError`)
    unless they are `expected`, the count the caller confirmed, and at most `BULK_MAX`. Each
    folder is a group as an organize call's, a failing one leaving the others done; `remaining`
    is what the next call should find. Notes an audit item per message acted on, and per account
    for a refusal (`messages`: its count) or a source that failed."""

    def refuse(searched: Sequence[int], error: MailError, counts: dict[int, int]) -> None:
        for account in searched:
            note(
                AuditItem(
                    account=account, folder=folder, messages=counts.get(account), result=error.code
                )
            )

    try:
        _check(action)
        found, failures, plans = _find(connections, accounts, folder, query, action, subfolders)
    except MailError as error:
        refuse(accounts, error, {})
        raise
    for failure in failures:
        note(AuditItem(account=failure.account, folder=failure.folder, result=failure.code))
    total = sum(len(f.uids) for f in found)
    if total > BULK_MAX or total != expected:
        counts: dict[int, int] = {}
        for f in found:
            counts[f.account] = counts.get(f.account, 0) + len(f.uids)
        refused = MatchCountError("too_many" if total > BULK_MAX else "count_changed", total)
        refuse(list(plans), refused, counts)
        raise refused

    groups: list[_Group] = []
    budget = BULK_BATCH
    for f in found:
        taken = f.uids[:budget]
        if taken:
            refs = tuple(MessageRef(f.account, f.folder, f.uid_validity, uid) for uid in taken)
            groups.append(_Group(f.account, f.folder, refs))
            budget -= len(taken)

    def act(group: _Group, noted: Note) -> GroupResult:
        if isinstance(action, BulkFlag):
            flagged = _flag_group(
                connections,
                group,
                noted,
                seen=action.seen,
                flagged=action.flagged,
                handled=action.handled,
            )
            return replace(flagged, flags=())
        plan = plans[group.account]
        destination = plan.destination
        if destination is None:  # _plan gives a move or a trash one
            raise MailError("invalid_argument")
        moving = isinstance(action, BulkMove)
        junk = _junk(plan.folders, group.folder, destination) if moving else None
        return _move(connections, group, destination.path, junk, noted)

    result = _each_group(groups, act)
    for item in result.items:
        note(item)
    done = sum(group.count for group in result.groups if group.ok)
    # Copied but not removed: in both folders, never to be moved again.
    copied = sum(
        len(group.refs)
        for group, outcome in zip(groups, result.groups, strict=True)
        if not outcome.ok and outcome.destination is not None
    )
    return BulkResult(done, total - done - copied, result.groups, tuple(failures))


def _check(action: BulkAction | None) -> None:
    if isinstance(action, BulkFlag) and (
        action.seen is None and action.flagged is None and action.handled is None
    ):
        raise MailError("invalid_argument", "Give `seen`, `flagged`, `handled` or several.")


def _find(
    connections: MailConnections,
    accounts: Sequence[int],
    folder: str,
    query: SearchQuery,
    action: BulkAction | None,
    subfolders: bool,
) -> tuple[list[_Found], list[PageFailure], dict[int, _Plan]]:
    """Each folder's matching UIDs, the sources that failed, and per account searched where its
    messages go."""
    check_subfolders(folder, subfolders=subfolders)
    lone = len(accounts) == 1
    one_folder = lone and folder != ALL_FOLDERS and not subfolders
    found: list[_Found] = []
    failures: list[PageFailure] = []
    plans: dict[int, _Plan] = {}
    for account in accounts:
        try:
            searched, criteria = search_sources(
                connections, account, folder, query, subfolders=subfolders
            )
            plans[account], paths = _plan(
                connections, account, folder, searched, action, subfolders=subfolders
            )
        except MailError as error:
            if lone:
                raise
            failures.append(PageFailure.of(account, None, error))
            continue
        wanted = [*criteria, *_pending(action)]
        for path in paths:
            try:
                found.append(
                    connections.with_mailbox(
                        account, path, True, partial(_search, account, path, wanted)
                    )
                )
            except MailError as error:
                if one_folder:
                    raise
                failures.append(PageFailure.of(account, path, error))
    return found, failures, plans


def _search(account: int, path: str, criteria: Criteria, context: MailboxContext) -> _Found:
    uids = sorted(search_uids(context, criteria), reverse=True)
    return _Found(account, path, context.uid_validity, tuple(uids))


def _pending(action: BulkAction | None) -> list[object]:
    """For a flag change, the messages it would change: those without a flag it sets or with
    one it clears; for a move or a trash, those not marked `\\Deleted`."""
    if action is None:
        return []
    if not isinstance(action, BulkFlag):
        return ["UNDELETED"]
    keys: list[list[object]] = []
    if action.seen is not None:
        keys.append(["UNSEEN" if action.seen else "SEEN"])
    if action.flagged is not None:
        keys.append(["UNFLAGGED" if action.flagged else "FLAGGED"])
    if action.handled is not None:
        keys.append([HandledKey(not action.handled)])
    return [_any_of(keys)]


def _plan(
    connections: MailConnections,
    account: int,
    folder: str,
    paths: Sequence[str],
    action: BulkAction | None,
    *,
    subfolders: bool,
) -> tuple[_Plan, list[str]]:
    """Where a move or a trash takes `account`'s messages, refused as an organize call would
    refuse it, and the folders it takes them from: `paths`, the folders a search of `folder`
    reads, except that a move over several (`ALL_FOLDERS`, or with `subfolders`) reads
    `_moved_from`'s. Refused (`invalid_argument`) when the destination is still one of them, or
    a move leaves from All Mail: what was done would still match."""
    if action is None or isinstance(action, BulkFlag):
        return _Plan(), list(paths)
    folders = tuple(connections.folders(account))
    if isinstance(action, BulkMove):
        destination = move_target(None, action.folder, folders, account=account)
        if folder == ALL_FOLDERS or subfolders:
            paths = _moved_from(folders, paths, destination, every_folder=folder == ALL_FOLDERS)
        if any(f.role == "all" and f.path in paths for f in folders):
            raise MailError(
                "invalid_argument",
                "Mail moved out of All Mail stays in it: search another folder to move from.",
            )
        if destination.path in paths:
            raise MailError("invalid_argument", "They cannot move into a folder searched.")
    else:
        trash = special_folder_in(folders, "trash")
        if trash is None:
            raise MailError("unsupported", f"Account {account} has no Trash folder.")
        if trash.path in paths:
            raise MailError(
                "invalid_argument",
                "Trash is among the folders searched; mailroom never deletes permanently.",
            )
        destination = trash
    return _Plan(folders, destination), list(paths)


def _moved_from(
    folders: Sequence[Folder], paths: Sequence[str], destination: Folder, *, every_folder: bool
) -> list[str]:
    """The folders a move over several takes mail out of: those searched but its destination
    and All Mail, out of which nothing moves (Gmail keeps the message there). A search over
    all folders (`every_folder`) of an account with All Mail reads it alone
    (`searchable_folders`); a move reads instead each folder a message can leave, the inbox and
    the account's own (Gmail's labels), not Starred, Important, Sent, Drafts, Junk or Trash.
    There a message under several labels is counted, and moved, once per label."""
    every = {f.path for f in folders if f.role == "all"}
    if every_folder and every:
        paths = [
            f.path
            for f in folders
            if f.selectable
            and f.role not in _STAYING_ROLES
            and _IMPORTANT not in (flag.lower() for flag in f.flags)
        ]
    return [path for path in paths if path != destination.path and path not in every]
