"""Organizing batches of refs: moves, archiving, trashing and flag changes."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Final, cast

from mailroom.audit import WEB, AuditTrail, Origin
from mailroom.errors import Refusal, mail_refusal
from mailroom.mail.service._shared import _accounts, _ref_accounts, _run, _Stoppable
from mailroom_core import mail as core
from mailroom_core.mail import MailError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mailroom.context import AppContext
    from mailroom_core.mail import MailConnections, OrganizeResult

logger = logging.getLogger(__name__)

MAX_REFS: Final = core.MAX_REFS
"""The most refs one organize call takes."""


def _organize(
    ctx: AppContext,
    user_id: int,
    event: str,
    action: str,
    origin: Origin,
    refs: Sequence[str],
    work: Callable[[MailConnections], OrganizeResult],
) -> OrganizeResult:
    """``work`` on the batch ``refs`` once each ref is checked (``_ref_accounts``), logged with
    its accounts, counts and failure codes only: never a folder, a ref or a message's data, which
    go to the audit trail as ``action`` (the result's items), recorded by the thread that did the
    work. A call cut at the deadline writes no group after the cut (``_Stoppable``); the ones done
    before it stay done, and are recorded once that thread finishes."""
    via = origin.via
    trail = AuditTrail(ctx.session_maker, user_id, origin, action)
    accounts = _accounts(ctx, user_id)
    used = _ref_accounts(ctx, user_id, accounts, refs)
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
                trail, work(cast("MailConnections", _Stoppable(connections, stop)))
            ),
            on_deadline=stop.set,
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
        lambda connections: core.move_messages(connections, refs, folder),
    )


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
        lambda connections: core.archive_messages(connections, refs),
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
        lambda connections: core.trash_messages(connections, refs),
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
        lambda connections: core.set_flags(connections, refs, seen=seen, flagged=flagged),
    )


def _recorded(trail: AuditTrail, result: OrganizeResult) -> OrganizeResult:
    """``result``, its items in ``trail``."""
    trail.notes(result.items)
    return result
