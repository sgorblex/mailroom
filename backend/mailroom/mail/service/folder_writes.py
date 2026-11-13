"""An account's folders: creating, renaming (the rule sets naming the folder follow it) and
deleting."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from mailroom.audit import WEB, AuditTrail, Origin
from mailroom.errors import Refusal
from mailroom.filters.folders import Rewritten, naming_rule_sets, rewrite_folder
from mailroom.mail.service._shared import _Accounts, _accounts, _run, _Stoppable, _usable
from mailroom_core import mail as core
from mailroom_core.mail import AuditItem, FolderChangedError
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


@contextmanager
def _logged_folder_call(
    event: str, user_id: int, via: Via, account_id: str
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
    account_id: str,
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


def _segments(path: str, delimiter: str | None) -> list[str]:
    return path.split(delimiter) if delimiter else [path]


def create_folder(
    ctx: AppContext,
    user_id: int,
    account_id: str,
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
    ctx: AppContext, user_id: int, account_id: str, change: FolderChange
) -> Rewritten | None:
    """Points the rule sets and filters naming the renamed folder at its new path; what it
    changed, or None, logged, when the database step failed (the rename stands)."""
    destination = change.destination if change.destination is not None else change.path
    try:
        with ctx.session_maker() as db:
            return rewrite_folder(
                db,
                user_id,
                account_id,
                _segments(change.path, change.delimiter),
                _segments(destination, change.delimiter),
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


def rename_folder(
    ctx: AppContext,
    user_id: int,
    account_id: str,
    folder: str,
    parent: str | None,
    name: str,
    *,
    origin: Origin = WEB,
) -> FolderRenamed:
    """Renames ``folder`` to ``name`` under ``parent`` (None: the top level), its subfolders with
    it (mailroom-core's ``rename_folder``), then points the account's rule sets and filter
    actions that name it, or a folder under it, at the new path: enabled ones too. Both run on
    the pool's thread, so the rule sets follow a rename the server did after the call's deadline.
    A failure of that database step does not fail the call: ``rule_sets_updated`` is False.
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
        extra: dict[str, object] = (
            {"rule_sets_error": True}
            if rewritten is None
            else {"rule_sets": rewritten.rule_sets, "filters": rewritten.filters}
        )
        for item in noted:
            trail.note(item, extra)
        return FolderRenamed(
            change=change,
            rule_sets=() if rewritten is None else tuple(rewritten.rule_sets),
            filters=() if rewritten is None else tuple(rewritten.filters),
            rule_sets_updated=rewritten is not None,
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
    account_id: str,
    folder: str,
    *,
    messages: int,
    origin: Origin = WEB,
) -> FolderChange:
    """Deletes ``folder`` and its mail for good, never to Trash: mailroom-core's
    ``delete_folder``, with ``messages`` the count the user confirmed (``folder_changed``,
    carrying the count it holds now, when it differs). Refused first, before the folder is
    touched, while a rule set or a filter's action names it or a folder under it
    (``folder_in_use``, listing them). Recorded as ``folder_delete``, refusals too."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    trail = AuditTrail(ctx.session_maker, user_id, origin, "folder_delete")

    def noted(result: AuditResult) -> None:
        trail.note(AuditItem(account=account_id, folder=folder, messages=messages, result=result))

    def work(connections: MailConnections) -> FolderChange:
        try:
            segments = _segments(folder, connections.delimiter(account_id))
            with ctx.session_maker() as db:
                named = naming_rule_sets(db, user_id, account_id, segments)
        except Exception as error:
            noted(core.audit_result(error))
            raise
        if named:
            noted(cast("MailErrorCode", "folder_in_use"))  # the app's own code
            raise _in_use(named)
        try:
            return core.delete_folder(
                connections, account_id, folder, messages=messages, note=trail.note
            )
        except FolderChangedError as error:
            raise Refusal(
                "mail", "folder_changed", error.message, extra={"messages": error.messages}
            ) from None

    with _logged_folder_call("mail_folder_deleted", user_id, origin.via, account_id) as logged:
        deleted = _folder_write(ctx, user_id, accounts, "mail_folder_deleted", account_id, work)
        logged["messages"] = deleted.messages
        return deleted
