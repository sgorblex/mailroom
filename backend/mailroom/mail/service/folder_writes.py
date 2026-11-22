"""An account's folders: creating, renaming (the rule sets naming the folder follow it),
deleting, and marking one read."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from mailroom.audit import WEB, AuditTrail, Origin
from mailroom.errors import Refusal
from mailroom.filters.folders import Rewritten, naming_rule_sets, rewrite_folder, segments
from mailroom.filters.transfers import rename_in_ledger
from mailroom.mail import notifications
from mailroom.mail.index import reads as index_reads
from mailroom.mail.index import writes as index_writes
from mailroom.mail.service._shared import _Accounts, _accounts, _run, _Stoppable, _usable
from mailroom_core import mail as core
from mailroom_core.mail import AuditItem, FolderChangedError, MailError
from mailroom_core.render import clean_header

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from mailroom.accounts.service import Via
    from mailroom.context import AppContext
    from mailroom.filters.folders import RuleSetRef
    from mailroom_core.mail import AuditResult, FolderChange, MailConnections, MailErrorCode

logger = logging.getLogger(__name__)

MAX_FOLDER_NAME: Final = core.MAX_FOLDER_NAME
"""Of the name a create or a rename gives a folder (one segment of its path)."""
MAX_MESSAGE_COUNT: Final = 2**32 - 1
"""An IMAP count's bound: of the ``messages`` a folder's delete takes."""
MAX_IN_USE_LISTED: Final = 10
"""The rule sets a folder delete's ``folder_in_use`` refusal names."""
_UIDS_NEEDED: Final = "Give the UIDVALIDITY and UIDNEXT of the folder's listing."


@dataclass(frozen=True, slots=True)
class FolderRenamed:
    """A rename: the folder's old path and its new one (``change``), and the rule sets that
    followed it."""

    change: FolderChange
    rule_sets: tuple[int, ...]
    """Every rule set changed, by its own folder or a filter's action."""
    filters: tuple[int, ...]
    rule_sets_updated: bool
    """False when the database step failed: the folder is renamed, the rule sets still name its
    old path."""
    all_accounts_rule_sets: tuple[int, ...] = ()
    """The rule sets for all accounts naming the old path, left as they are."""


@contextmanager
def _logged_folder_call(
    event: str, user_id: int, via: Via, account_id: int
) -> Iterator[dict[str, object]]:
    """Logs a folder call as ``event`` once it ends: its outcome and duration, and the counts the
    caller puts in the dict it is given; never a folder's path or a rule set's name."""
    started = time.monotonic()
    logged: dict[str, object] = {"outcome": "ok"}
    try:
        yield logged
    except Refusal as refusal:
        logged["outcome"] = refusal.code
        raise
    except Exception:
        logged["outcome"] = "failed"
        raise
    finally:
        logger.info(
            event,
            extra={
                "event": event,
                "user_id": user_id,
                "via": via,
                "account_id": account_id,
                **logged,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )


def _folder_write[T](
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    event: str,
    account_id: int,
    work: Callable[[MailConnections], T],
) -> T:
    """``work`` on the account under the pool's gates and deadline, past which it writes nothing
    more (``_Stoppable``); a folder list that confirms a write whose answer was lost still runs,
    and so may the listing that reads an uncached delimiter, which writes nothing."""
    stop = threading.Event()
    return _run(
        ctx,
        user_id,
        accounts,
        event,
        [account_id],
        lambda connections: work(
            cast("MailConnections", _Stoppable(connections, stop, confirming=True))
        ),
        on_deadline=stop.set,
    )


def create_folder(
    ctx: AppContext,
    user_id: int,
    account_id: int,
    parent: str | None,
    name: str,
    *,
    origin: Origin = WEB,
) -> FolderChange:
    """Creates the folder ``name`` under ``parent`` (None: the top level), subscribed:
    mailroom-core's ``create_folder``, recorded as ``folder_create``."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    trail = AuditTrail(ctx.session_maker, user_id, origin, "folder_create")
    with _logged_folder_call("mail_folder_created", user_id, origin.via, account_id):
        return _folder_write(
            ctx,
            user_id,
            accounts,
            "mail_folder_created",
            account_id,
            lambda connections: core.create_folder(
                connections, account_id, parent, name, trail.note
            ),
        )


def _rewrite_rule_sets(
    ctx: AppContext, user_id: int, account_id: int, change: FolderChange
) -> Rewritten | None:
    """Points the rule sets and filters naming the renamed folder at its new path, and the ledger
    of copies moved there from another account; what it changed, or None, logged, when the
    database step failed (the rename stands)."""
    destination = change.destination if change.destination is not None else change.path
    try:
        with ctx.session_maker() as db:
            rename_in_ledger(db, user_id, account_id, change.path, destination, change.delimiter)
            return rewrite_folder(
                db,
                user_id,
                account_id,
                segments(change.path, change.delimiter),
                segments(destination, change.delimiter),
            )
    except Exception as exc:
        logger.warning(
            "folder_rule_sets_failed",
            extra={
                "event": "folder_rule_sets_failed",
                "user_id": user_id,
                "account_id": account_id,
                "error": type(exc).__name__,
            },
        )
        return None


def _follow_in_notifications(
    ctx: AppContext, user_id: int, account_id: int, change: FolderChange, *, deleted: bool
) -> None:
    """Points the account's desktop-notification folders naming the renamed folder, or one under
    it, at its new path, or drops them once it is ``deleted``; a database failure is logged, and
    the folder change stands."""
    try:
        with ctx.session_maker() as db:
            if deleted:
                notifications.forget_folder(db, user_id, account_id, change.path, change.delimiter)
            else:
                destination = change.destination if change.destination is not None else change.path
                notifications.rename_folder(
                    db, user_id, account_id, change.path, destination, change.delimiter
                )
    except Exception as exc:
        logger.warning(
            "folder_notifications_failed",
            extra={
                "event": "folder_notifications_failed",
                "user_id": user_id,
                "account_id": account_id,
                "error": type(exc).__name__,
            },
        )


def rename_folder(
    ctx: AppContext,
    user_id: int,
    account_id: int,
    folder: str,
    parent: str | None,
    name: str,
    *,
    origin: Origin = WEB,
) -> FolderRenamed:
    """Renames ``folder`` to ``name`` under ``parent`` (None: the top level), its subfolders with
    it (mailroom-core's ``rename_folder``), then points the account's rule sets and filter
    actions that name it, or a folder under it, at the new path, and the other accounts' filters
    moving there (``move_to_account``): enabled ones too; the rule sets for all accounts naming it
    are left, and listed (``all_accounts_rule_sets``); the account's desktop-notification
    folders follow it too. These run on the pool's thread, so they follow a rename the server did
    after the call's deadline. A failure of the rule sets' database step does not fail the call:
    ``rule_sets_updated`` is False.
    Recorded as ``folder_rename``, with the rule sets and filters changed."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    trail = AuditTrail(ctx.session_maker, user_id, origin, "folder_rename")

    def work(connections: MailConnections) -> FolderRenamed:
        noted: list[AuditItem] = []
        try:
            change = core.rename_folder(connections, account_id, folder, parent, name, noted.append)
        except Exception:
            for item in noted:
                trail.note(item)
            raise
        rewritten = _rewrite_rule_sets(ctx, user_id, account_id, change)
        _follow_in_notifications(ctx, user_id, account_id, change, deleted=False)
        extra: dict[str, object] = (
            {"rule_sets_error": True}
            if rewritten is None
            else {"rule_sets": rewritten.rule_sets, "filters": rewritten.filters}
        )
        if rewritten is not None and rewritten.all_accounts:
            extra["all_accounts_rule_sets"] = rewritten.all_accounts
        for item in noted:
            trail.note(item, extra)
        return FolderRenamed(
            change=change,
            rule_sets=() if rewritten is None else tuple(rewritten.rule_sets),
            filters=() if rewritten is None else tuple(rewritten.filters),
            rule_sets_updated=rewritten is not None,
            all_accounts_rule_sets=() if rewritten is None else tuple(rewritten.all_accounts),
        )

    with _logged_folder_call("mail_folder_renamed", user_id, origin.via, account_id) as logged:
        renamed = _folder_write(ctx, user_id, accounts, "mail_folder_renamed", account_id, work)
        logged.update(
            rule_sets=len(renamed.rule_sets),
            filters=len(renamed.filters),
            rule_sets_updated=renamed.rule_sets_updated,
        )
        return renamed


def _in_use(named: Sequence[RuleSetRef]) -> Refusal:
    """``folder_in_use``, naming the first ``MAX_IN_USE_LISTED`` rule sets."""
    listed = named[:MAX_IN_USE_LISTED]
    names = ", ".join(f'"{clean_header(ref.name, 200)}"' for ref in listed)
    more = len(named) - len(listed)
    rest = f" and {more} more" if more else ""
    message = (
        f"Rule sets use this folder or one under it: {names}{rest}. "
        "Point them at another folder or remove them first."
    )
    return Refusal(
        "conflict",
        "folder_in_use",
        message,
        extra={"rule_sets": [{"id": ref.id, "name": ref.name} for ref in listed]},
    )


def delete_folder(
    ctx: AppContext,
    user_id: int,
    account_id: int,
    folder: str,
    *,
    messages: int,
    origin: Origin = WEB,
) -> FolderChange:
    """Deletes ``folder`` and its mail for good, never to Trash: mailroom-core's
    ``delete_folder``, with ``messages`` the count the user confirmed (``folder_changed``,
    carrying the count it holds now, when it differs). Refused first, before the folder is
    touched, while a rule set or a filter's action names it or a folder under it
    (``folder_in_use``, listing them). Once deleted, it and the folders under it leave the
    account's desktop-notification folders. Recorded as ``folder_delete``, refusals too."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    trail = AuditTrail(ctx.session_maker, user_id, origin, "folder_delete")

    def noted(result: AuditResult) -> None:
        trail.note(AuditItem(account=account_id, folder=folder, messages=messages, result=result))

    def work(connections: MailConnections) -> FolderChange:
        try:
            split = segments(folder, connections.delimiter(account_id))
            with ctx.session_maker() as db:
                named = naming_rule_sets(db, user_id, account_id, split)
        except Exception as error:
            noted(core.audit_result(error))
            raise
        if named:
            noted(cast("MailErrorCode", "folder_in_use"))  # the app's own code
            raise _in_use(named)
        try:
            deleted = core.delete_folder(
                connections, account_id, folder, messages=messages, note=trail.note
            )
        except FolderChangedError as error:
            raise Refusal(
                "mail", "folder_changed", error.message, extra={"messages": error.messages}
            ) from None
        _follow_in_notifications(ctx, user_id, account_id, deleted, deleted=True)
        return deleted

    with _logged_folder_call("mail_folder_deleted", user_id, origin.via, account_id) as logged:
        deleted = _folder_write(ctx, user_id, accounts, "mail_folder_deleted", account_id, work)
        logged["messages"] = deleted.messages
        return deleted


def mark_folder_read(
    ctx: AppContext,
    user_id: int,
    account_id: int,
    folder: str,
    *,
    uid_validity: int | None,
    uid_next: int | None,
    origin: Origin = WEB,
) -> int:
    """Marks read every unread message of ``folder`` below ``uid_next``, the UIDNEXT of the
    listing the user confirmed from (``uid_validity`` its UIDVALIDITY, ``stale_reference`` when
    it changed): mailroom-core's ``mark_folder_read``. Without both, for a Gmail label's folder
    (whose listing may have counted it from the index, without them) the folder's own STATUS
    gives them now; any other folder, or only one of them, is ``invalid_argument``. Returns how
    many it marked. Recorded once for the folder as ``mark_folder_read`` with that count,
    refusals too; the mail index follows (``mailroom.mail.index.writes``)."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    trail = AuditTrail(ctx.session_maker, user_id, origin, "mark_folder_read")

    def work(connections: MailConnections) -> int:
        validity, following = (
            (uid_validity, uid_next)
            if uid_validity is not None and uid_next is not None
            else _label_uids(connections, account_id, folder, trail, uid_validity, uid_next)
        )
        return _marked_read(
            ctx,
            user_id,
            lambda: core.mark_folder_read(
                connections,
                account_id,
                folder,
                uid_validity=validity,
                uid_next=following,
                note=trail.note,
            ),
            account_id,
            folder,
            uid_validity=validity,
            uid_next=following,
        )

    with _logged_folder_call("mail_folder_marked_read", user_id, origin.via, account_id) as logged:
        marked = _folder_write(ctx, user_id, accounts, "mail_folder_marked_read", account_id, work)
        logged["messages"] = marked
        return marked


def _label_uids(
    connections: MailConnections,
    account_id: int,
    folder: str,
    trail: AuditTrail,
    uid_validity: int | None,
    uid_next: int | None,
) -> tuple[int, int]:
    """The UIDVALIDITY and UIDNEXT of a Gmail label's folder, from its STATUS now:
    ``invalid_argument`` for any other folder or when one of them was given, ``mailbox_missing``
    for one not listed; a refusal is recorded on ``trail``."""
    try:
        if uid_validity is not None or uid_next is not None:
            raise MailError("invalid_argument", _UIDS_NEEDED)
        return _status_uids(connections, account_id, folder)
    except Exception as error:
        result = core.audit_result(error)
        trail.note(AuditItem(account=account_id, folder=folder, messages=0, result=result))
        raise


def _status_uids(connections: MailConnections, account_id: int, folder: str) -> tuple[int, int]:
    if not connections.capabilities(account_id).gmail:
        raise MailError("invalid_argument", _UIDS_NEEDED)
    listed = connections.folders(account_id, with_status=lambda f: f.path == folder)
    here = next((f for f in listed if f.path == folder), None)
    if here is None:
        raise MailError("mailbox_missing")
    if index_reads.gmail_label(here) is None or here.uid_validity is None or here.uid_next is None:
        raise MailError("invalid_argument", _UIDS_NEEDED)
    return here.uid_validity, here.uid_next


def _marked_read(
    ctx: AppContext,
    user_id: int,
    mark: Callable[[], int],
    account_id: int,
    folder: str,
    *,
    uid_validity: int,
    uid_next: int,
) -> int:
    """``mark``'s count, the index's rows of the folder marked read as it was; synced again
    when it failed, maybe partway."""
    done = False
    try:
        marked = mark()
        done = True
        return marked
    finally:
        index_writes.marked_read(
            ctx.session_maker,
            user_id,
            account_id,
            folder,
            uid_validity=uid_validity,
            uid_next=uid_next,
            done=done,
        )
