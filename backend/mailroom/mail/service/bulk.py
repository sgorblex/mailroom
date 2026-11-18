"""Acting on every message a search matches (*select all matching*): counting them, then
flagging, moving or trashing them a batch at a time, over mailroom-core's ``count_matching``
and ``act_on_matching``."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Final, cast

from mailroom.accounts.service import require_subkey
from mailroom.audit import WEB, AuditTrail, Origin
from mailroom.errors import Refusal, mail_refusal
from mailroom.mail.service._shared import (
    _Accounts,
    _accounts,
    _call,
    _missing_password,
    _run,
    _Stoppable,
    _usable,
)
from mailroom.mail.service.read import (
    ALL_ACCOUNTS,
    AllAccounts,
    _password_missing_failure,
    check_search,
    password_checked,
)
from mailroom_core import mail as core
from mailroom_core.mail import BulkFlag, BulkMove, BulkResult, MatchCountError, Matching

if TYPE_CHECKING:
    from mailroom.accounts.service import Via
    from mailroom.context import AppContext
    from mailroom_core.mail import (
        AuditItem,
        BulkAction,
        MailConnections,
        PageFailure,
        SearchQuery,
    )

logger = logging.getLogger(__name__)

BULK_BATCH: Final = core.BULK_BATCH
BULK_MAX: Final = core.BULK_MAX


def _scope(
    ctx: AppContext, user_id: int, accounts: _Accounts, account: int | AllAccounts
) -> tuple[list[int], list[PageFailure]]:
    """The accounts a call searches, and over all of them (``ALL_ACCOUNTS``) those it cannot,
    without a password that opens (``password_missing``), as a search reports them."""
    if account != ALL_ACCOUNTS:
        _usable(ctx, user_id, accounts, account)
        return [account], []
    wanted = accounts.ids()
    if wanted:
        require_subkey(ctx.subkey)
    usable: list[int] = []
    skipped: list[PageFailure] = []
    for account_id in wanted:
        if _missing_password(ctx, user_id, account_id):
            skipped.append(_password_missing_failure(account_id, None))
        else:
            usable.append(account_id)
    return usable, skipped


def count_matching(
    ctx: AppContext,
    user_id: int,
    account: int | AllAccounts,
    folder: str,
    query: SearchQuery,
    *,
    subfolders: bool = False,
    action: BulkAction | None = None,
    via: Via = "web",
) -> Matching:
    """How many messages ``query`` matches in ``folder`` of one account or all, as ``search``
    reads them; with ``action``, those it would change, the count ``act_on_matching`` takes.
    Over all accounts one that cannot be searched is a failure and the others still count."""
    check_search(folder, query, subfolders=subfolders)
    accounts = _accounts(ctx, user_id)
    usable, skipped = _scope(ctx, user_id, accounts, account)
    if not usable:
        return Matching(0, tuple(skipped))
    matching = _call(
        ctx,
        user_id,
        accounts,
        "mail_bulk_counted",
        via,
        account,
        lambda connections: core.count_matching(
            connections, usable, folder, query, subfolders=subfolders, action=action
        ),
        on=usable,
    )
    return Matching(matching.total, password_checked(ctx, user_id, [*skipped, *matching.failures]))


def act_on_matching(
    ctx: AppContext,
    user_id: int,
    account: int | AllAccounts,
    folder: str,
    query: SearchQuery,
    action: BulkAction,
    *,
    expected: int,
    subfolders: bool = False,
    origin: Origin = WEB,
) -> BulkResult:
    """``action`` on up to ``BULK_BATCH`` of what ``count_matching`` with it finds, refused with
    ``count_changed`` (carrying ``matching``, the count now) unless it is ``expected``, and with
    ``too_many`` past ``BULK_MAX``: mailroom-core's ``act_on_matching``. The caller repeats it
    with the ``remaining`` count until none remain. Recorded per message as ``bulk_flag``,
    ``bulk_move`` or ``bulk_trash``, refusals and failed accounts too; logged with counts and
    codes only."""
    check_search(folder, query, subfolders=subfolders)
    event = "mail_bulk_acted"
    name = (
        "bulk_flag"
        if isinstance(action, BulkFlag)
        else "bulk_move"
        if isinstance(action, BulkMove)
        else "bulk_trash"
    )
    trail = AuditTrail(ctx.session_maker, user_id, origin, name)
    accounts = _accounts(ctx, user_id)
    usable, skipped = _scope(ctx, user_id, accounts, account)
    trail.notes(_failed(failure) for failure in skipped)
    started = time.monotonic()
    result: BulkResult | None = None
    outcome = "ok"
    stop = threading.Event()

    def work(connections: MailConnections) -> BulkResult:
        items: list[AuditItem] = []
        try:
            return core.act_on_matching(
                cast("MailConnections", _Stoppable(connections, stop)),
                usable,
                folder,
                query,
                action,
                expected=expected,
                subfolders=subfolders,
                note=items.append,
            )
        except MatchCountError as error:
            raise Refusal(
                "mail", error.code, error.message, extra={"matching": error.matching}
            ) from None
        finally:
            trail.notes(items)

    try:
        if usable:
            result = _run(ctx, user_id, accounts, event, usable, work, on_deadline=stop.set)
        elif expected != 0:
            refused = MatchCountError("count_changed", 0)
            raise Refusal("mail", refused.code, refused.message, extra={"matching": 0})
        else:
            result = BulkResult(0, 0, ())
        result = BulkResult(
            result.done,
            result.remaining,
            result.groups,
            password_checked(ctx, user_id, [*skipped, *result.failures]),
        )
        if result.groups and not any(group.ok for group in result.groups):
            error = next(group.error for group in result.groups if group.error is not None)
            raise mail_refusal(error.code, error.message)
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
                "via": origin.via,
                "account_ids": usable,
                "expected": expected,
                "done": 0 if result is None else result.done,
                "remaining": None if result is None else result.remaining,
                "groups": len(groups),
                "failed_groups": sum(not group.ok for group in groups),
                "codes": sorted({group.error.code for group in groups if group.error is not None}),
                "outcome": outcome,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )


def _failed(failure: PageFailure) -> AuditItem:
    return core.AuditItem(account=failure.account, folder=failure.folder, result=failure.code)
