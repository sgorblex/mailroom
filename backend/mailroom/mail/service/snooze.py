"""Snooze (``docs/requirements/mail-client.md``, *Snooze*): a message taken out of its folder into
the account's Snoozed folder (a label on Gmail, made when missing) until a time, when the worker's
job ``wake_snooze`` moves it back, unread (``mailroom.worker.snoozes``). ``snooze`` writes the
``app.snooze`` row and its job in one transaction before the move, so a snooze never outlives
its wake, and drops both when the move fails (when its outcome is unknown, it wakes soon
instead); ``wake_now`` brings one back at once (the job run now), ``list_snoozes`` reads them,
and a folder's rename carries them along (``rename_in_snoozes``). Other clients see the message
in Snoozed meanwhile."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mailroom.audit import WEB, AuditTrail, Origin
from mailroom.errors import Refusal, mail_refusal
from mailroom.filters.folders import segments, under
from mailroom.mail.service._shared import _accounts, _call, _message_ref
from mailroom.mail.service.organize import _from_label_views, _organize
from mailroom.models import Snooze
from mailroom_core import mail as core

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from mailroom.context import AppContext
    from mailroom_core.mail import MailConnections, SnoozeTarget

logger = logging.getLogger(__name__)

MAX_AHEAD: Final = timedelta(days=366)
"""How far ahead a message can be snoozed."""
ALREADY_SNOOZED: Final = "This message is snoozed already."
UNCONFIRMED: Final = frozenset({"timeout", "unreachable"})
"""A move refused with these may have been done by the server all the same."""
_NO_SUCH: Final = "No such snoozed message."
_ID: Final = re.compile(r"[0-9]{1,18}")


@dataclass(frozen=True, slots=True, kw_only=True)
class SnoozeView:
    """A snoozed message as a client lists it."""

    snooze_id: str
    account: int
    subject: str
    sender: str | None
    folder: str
    """Where it goes back to."""
    until: datetime
    code: str | None = None
    """Why its last wake failed; it is tried again later."""


def _view(row: Snooze) -> SnoozeView:
    return SnoozeView(
        snooze_id=str(row.id),
        account=row.account_id,
        subject=row.subject,
        sender=row.sender,
        folder=row.folder,
        until=row.until,
        code=row.code,
    )


def _log(event: str, user_id: int, origin: Origin, row: Snooze) -> None:
    logger.info(
        event,
        extra={
            "event": event,
            "user_id": user_id,
            "via": origin.via,
            "account_id": row.account_id,
            "snooze_id": row.id,
        },
    )


def snooze(
    ctx: AppContext,
    user_id: int,
    ref: str,
    until: datetime,
    *,
    source_folder: str | None = None,
    origin: Origin = WEB,
) -> SnoozeView:
    """Snooze the message ``ref`` until ``until`` (with its offset, to come, a year ahead at
    most): its Snoozed folder made when missing (audited ``folder_create``), its row and wake
    job written, then the move (audited ``snooze``). ``source_folder``: the folder the user acted
    from, where a Gmail label view's ref is acted on, as a move's. ``already_snoozed`` for a
    message of the account snoozed already; mailroom-core's refusals (``snooze_target``)."""
    if until.tzinfo is None:
        raise Refusal("invalid", "invalid_argument", "A time needs its offset.")
    now = datetime.now(UTC)
    if until <= now:
        raise Refusal("invalid", "invalid_argument", "A snooze must end in the future.")
    if until > now + MAX_AHEAD:
        raise Refusal("invalid", "invalid_argument", "A message can be snoozed a year at most.")
    accounts = _accounts(ctx, user_id)
    account_id = _message_ref(ctx, user_id, accounts, ref).account
    created = AuditTrail(ctx.session_maker, user_id, origin, "folder_create")

    def prepare(connections: MailConnections) -> SnoozeTarget:
        [acting] = _from_label_views(ctx, user_id, connections, [ref], source_folder, {})
        return core.snooze_target(connections, acting, created.note)

    target = _call(ctx, user_id, accounts, "mail_snooze_checked", origin.via, account_id, prepare)
    acting = core.format_ref(target.ref)
    row = _hold(ctx, user_id, target, until, origin)

    def move(connections: MailConnections, _: object, refs: Sequence[str]) -> core.OrganizeResult:
        result = core.move_messages(connections, refs, target.snoozed)
        [moved] = result.groups
        if moved.error is None and moved.new_refs and target.ref.folder.upper() != "INBOX":
            _leave_inbox(connections, user_id, moved.new_refs[0])
        return result

    try:
        result = _organize(ctx, user_id, "mail_snoozed", "snooze", origin, [acting], move)
    except Refusal as refusal:
        _undo_hold(ctx, row.id, refusal.code)
        raise
    [group] = result.groups
    if group.error is not None:
        _undo_hold(ctx, row.id, group.error.code)
        raise mail_refusal(group.error.code, group.error.message)
    with ctx.session_maker() as db:
        stored = db.get(Snooze, row.id)
        if stored is not None and group.new_refs:
            stored.ref = group.new_refs[0]
            db.commit()
        _log("mail_snooze_held", user_id, origin, row)
        return _view(stored if stored is not None else row)


def _leave_inbox(connections: MailConnections, user_id: int, ref: str) -> None:
    """On Gmail, INBOX's label off a message snoozed from a label's view; one that stayed is
    logged, the message snoozed all the same (it comes back to INBOX anyway)."""
    try:
        core.leave_inbox(connections, ref)
    except core.MailError as error:
        logger.warning(
            "mail_snooze_inbox_kept",
            extra={"event": "mail_snooze_inbox_kept", "user_id": user_id, "code": error.code},
        )


def _hold(
    ctx: AppContext, user_id: int, target: SnoozeTarget, until: datetime, origin: Origin
) -> Snooze:
    from mailroom.worker.tasks import defer_snooze_wake

    with ctx.session_maker() as db:
        row = Snooze(
            user_id=user_id,
            account_id=target.ref.account,
            via=origin.via,
            message_id=target.message_id,
            size=target.size,
            folder=target.ref.folder,
            snoozed=target.snoozed,
            subject=target.subject,
            sender=target.sender,
            until=until,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            raise Refusal("conflict", "already_snoozed", ALREADY_SNOOZED) from None
        row.job_id = defer_snooze_wake(db, snooze_id=row.id, until=until)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def _undo_hold(ctx: AppContext, snooze_id: int, code: str) -> None:
    """After a move that failed with ``code``: the row and its job dropped when the move did not
    happen; when its outcome is unknown (``UNCONFIRMED``: it may have gone through after all), the
    wake queued ``GRACE`` from now instead, which brings the message back from Snoozed or records
    it missing."""
    from mailroom.worker.snoozes import GRACE
    from mailroom.worker.tasks import cancel_job, defer_snooze_wake

    with ctx.session_maker() as db:
        row = db.get(Snooze, snooze_id, with_for_update=True)
        if row is None:
            return
        if row.job_id is not None:
            cancel_job(db, row.job_id)
        if code in UNCONFIRMED:
            row.until = datetime.now(UTC) + GRACE
            row.code = code
            row.job_id = defer_snooze_wake(db, snooze_id=row.id, until=row.until)
        else:
            db.delete(row)
        db.commit()


def _own(ctx: AppContext, db: Session, user_id: int, snooze_id: str) -> Snooze:
    row = None
    if _ID.fullmatch(snooze_id):
        row = db.scalar(
            select(Snooze)
            .where(
                Snooze.id == int(snooze_id),
                Snooze.user_id == user_id,
                ctx.reach.covers(Snooze.account_id),
            )
            .with_for_update()
        )
    if row is None:
        raise Refusal("not_found", "not_found", _NO_SUCH, coded=True)
    return row


def wake_now(ctx: AppContext, user_id: int, snooze_id: str, *, origin: Origin = WEB) -> SnoozeView:
    """Bring the user's snoozed message back now: its wake job queued again to run at once (the
    waiting one deleted); what it is. Another user's is ``not_found``."""
    from mailroom.worker.tasks import cancel_job, defer_snooze_wake

    with ctx.session_maker() as db:
        row = _own(ctx, db, user_id, snooze_id)
        if row.job_id is not None:
            cancel_job(db, row.job_id)
        row.until = datetime.now(UTC)
        row.job_id = defer_snooze_wake(db, snooze_id=row.id, until=None)
        view = _view(row)
        db.commit()
        _log("mail_snooze_woken_now", user_id, origin, row)
        return view


def list_snoozes(ctx: AppContext, user_id: int) -> list[SnoozeView]:
    """The user's snoozed messages, soonest back first."""
    with ctx.session_maker() as db:
        rows = db.scalars(
            select(Snooze)
            .where(Snooze.user_id == user_id, ctx.reach.covers(Snooze.account_id))
            .order_by(Snooze.until, Snooze.id)
        )
        return [_view(row) for row in rows]


def forget_snoozes(db: Session, user_id: int, account_id: int | None = None) -> None:
    """Delete the user's snoozes, of ``account_id`` only when given, with their jobs, inside
    ``db``'s transaction: with the account or the user. Their messages stay in Snoozed."""
    from mailroom.worker.tasks import cancel_job

    query = select(Snooze).where(Snooze.user_id == user_id)
    if account_id is not None:
        query = query.where(Snooze.account_id == account_id)
    for row in db.scalars(query.with_for_update()):
        if row.job_id is not None:
            cancel_job(db, row.job_id)
        db.delete(row)
    db.flush()


def rename_in_snoozes(
    db: Session, user_id: int, account_id: int, old: str, new: str, delimiter: str | None
) -> None:
    """The account's snoozes follow the folder ``old``, and every folder under it, renamed
    ``new``: where they wait (their ref there dropped, as its folder changed) and where they go
    back to; committed."""
    joiner = delimiter or ""
    before = segments(old, delimiter)

    def moved(path: str) -> str | None:
        split = segments(path, delimiter)
        if not under(split, before):
            return None
        return joiner.join([*segments(new, delimiter), *split[len(before) :]])

    rows = db.scalars(
        select(Snooze)
        .where(Snooze.user_id == user_id, Snooze.account_id == account_id)
        .with_for_update()
    )
    for row in rows:
        snoozed = moved(row.snoozed)
        if snoozed is not None:
            row.snoozed = snoozed
            row.ref = None
        row.folder = moved(row.folder) or row.folder
    db.commit()
