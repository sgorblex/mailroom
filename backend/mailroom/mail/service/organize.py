"""Organizing batches of refs: moves, within an account or to another, archiving, trashing and
flag changes."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Final, cast

from mailroom.audit import WEB, AuditTrail, Origin
from mailroom.errors import Refusal, mail_refusal
from mailroom.mail.service._shared import (
    _Accounts,
    _accounts,
    _ref_accounts,
    _run,
    _Stoppable,
    _usable,
)
from mailroom_core import mail as core
from mailroom_core.mail import MailError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mailroom.context import AppContext
    from mailroom_core.mail import MailConnections, OrganizeResult, TransferRemoval

logger = logging.getLogger(__name__)

MAX_REFS: Final = core.MAX_REFS
"""The most refs one organize call takes."""
MOVE_TO_ACCOUNT_SECONDS: Final = 55.0
"""A move to another account's deadline, longer than the pool's (it reads, writes and removes
each message whole) but within the web container's 60 s proxy read timeout. One cut off is safe
to repeat: what reached the target is not copied again."""


def _organize(
    ctx: AppContext,
    user_id: int,
    event: str,
    action: str,
    origin: Origin,
    refs: Sequence[str],
    work: Callable[[MailConnections, _Accounts], OrganizeResult],
    *,
    target: str | None = None,
    deadline: float | None = None,
) -> OrganizeResult:
    """``work`` on the batch ``refs`` once each ref is checked (``_ref_accounts``), and the
    ``target`` account it writes to besides theirs, logged with its accounts, counts and failure
    codes only: never a folder, a ref or a message's data, which go to the audit trail as
    ``action`` (the result's items, each under its own account), recorded by the thread that did
    the work. A call cut at the deadline (``deadline`` seconds, else the pool's) writes no group
    after the cut (``_Stoppable``); the ones done before it stay done, and are recorded once that
    thread finishes."""
    via = origin.via
    trail = AuditTrail(ctx.session_maker, user_id, origin, action)
    accounts = _accounts(ctx, user_id)
    used = _ref_accounts(ctx, user_id, accounts, refs)
    if target is not None:
        _usable(ctx, user_id, accounts, target)
        used = [*used, target] if target not in used else used
    started = time.monotonic()
    result: OrganizeResult | None = None
    outcome: str = "ok"
    stop = threading.Event()
    try:
        result = _run(
            ctx,
            user_id,
            accounts,
            event,
            used,
            lambda connections: _recorded(
                trail, work(cast("MailConnections", _Stoppable(connections, stop)), accounts)
            ),
            on_deadline=stop.set,
            deadline=deadline,
        )
        outcome = result.outcome
        return result
    except Refusal as refusal:
        outcome = refusal.code
        raise
    finally:
        groups = () if result is None else result.groups
        logger.info(
            event,
            extra={
                "event": event,
                "user_id": user_id,
                "via": via,
                "account_ids": used,
                "ref_count": len(refs),
                "changed": sum(group.count for group in groups),
                "groups": len(groups),
                "failed_groups": sum(not group.ok for group in groups),
                "codes": sorted({group.error.code for group in groups if group.error is not None}),
                "outcome": outcome,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )


def move(
    ctx: AppContext, user_id: int, refs: Sequence[str], folder: str, *, origin: Origin = WEB
) -> OrganizeResult:
    """Moves the messages ``refs`` name (1 to ``MAX_REFS``, any folders and accounts) to
    ``folder`` of each one's account, never Drafts or Sent: mailroom-core's ``move_messages``,
    one result per source folder."""
    return _organize(
        ctx,
        user_id,
        "mail_moved",
        "move",
        origin,
        refs,
        lambda connections, _: core.move_messages(connections, refs, folder),
    )


def move_to_account(
    ctx: AppContext,
    user_id: int,
    refs: Sequence[str],
    account: str,
    folder: str,
    removal: TransferRemoval,
    *,
    origin: Origin = WEB,
) -> OrganizeResult:
    """Moves the messages ``refs`` name (1 to ``MAX_REFS``, in any of the user's accounts but
    ``account``) to ``folder`` of ``account``, another of the user's, never Drafts or Sent:
    mailroom-core's ``move_to_account``, each message at most the user's ``move_max_mb``. The
    originals go to Trash, or with ``removal="delete"`` are deleted for good once their copies
    are confirmed. Each message moved is recorded in both accounts."""
    try:
        return _organize(
            ctx,
            user_id,
            "mail_moved_to_account",
            "move_to_account",
            origin,
            refs,
            lambda connections, accounts: core.move_to_account(
                connections, refs, account, folder, removal=removal, max_bytes=accounts.move_cap
            ),
            target=account,
            deadline=MOVE_TO_ACCOUNT_SECONDS,
        )
    except Refusal as refusal:
        # A refused delete for good is on record too (the deletion safeguards); a timeout's
        # groups record themselves once their thread finishes.
        if removal == "delete" and refusal.code != "timeout":
            _record_refused(ctx, user_id, origin, refs, account, folder, refusal.code)
        raise


def _record_refused(
    ctx: AppContext,
    user_id: int,
    origin: Origin,
    refs: Sequence[str],
    account: str,
    folder: str,
    code: str,
) -> None:
    """A record per ref of the user's own accounts for a move to ``account`` refused whole."""
    owned = _accounts(ctx, user_id)
    items: list[core.AuditItem] = []
    for text in refs:
        try:
            ref = core.parse_ref(text)
        except MailError:
            continue
        if owned.has(ref.account):
            items.append(
                core.AuditItem(
                    account=ref.account,
                    folder=ref.folder,
                    uid=ref.uid,
                    destination=folder,
                    destination_account=account,
                    result=cast("core.AuditResult", code),
                )
            )
    AuditTrail(ctx.session_maker, user_id, origin, "move_to_account").notes(items)


def archive(
    ctx: AppContext, user_id: int, refs: Sequence[str], *, origin: Origin = WEB
) -> OrganizeResult:
    """Moves them to each account's folder with role ``archive``: ``unsupported`` for an
    account's group when it has none."""
    return _organize(
        ctx,
        user_id,
        "mail_archived",
        "archive",
        origin,
        refs,
        lambda connections, _: core.archive_messages(connections, refs),
    )


def trash(
    ctx: AppContext, user_id: int, refs: Sequence[str], *, origin: Origin = WEB
) -> OrganizeResult:
    """Moves them to each account's Trash; never a permanent delete, so a group already in Trash
    is refused."""
    return _organize(
        ctx,
        user_id,
        "mail_trashed",
        "trash",
        origin,
        refs,
        lambda connections, _: core.trash_messages(connections, refs),
    )


def flag(
    ctx: AppContext,
    user_id: int,
    refs: Sequence[str],
    *,
    seen: bool | None = None,
    flagged: bool | None = None,
    origin: Origin = WEB,
) -> OrganizeResult:
    """Sets (True) or clears (False) ``\\Seen`` and ``\\Flagged``, at least one of them (else
    refused before anything connects), and reads them back; the refs stay valid."""
    if seen is None and flagged is None:
        neither = MailError("invalid_argument", "Give `seen`, `flagged` or both.")
        raise mail_refusal(neither.code, neither.message)
    return _organize(
        ctx,
        user_id,
        "mail_flagged",
        "flag",
        origin,
        refs,
        lambda connections, _: core.set_flags(connections, refs, seen=seen, flagged=flagged),
    )


def _recorded(trail: AuditTrail, result: OrganizeResult) -> OrganizeResult:
    """``result``, its items in ``trail``."""
    trail.notes(result.items)
    return result
