"""Held sends (``docs/requirements/mail-client.md``, *Send state machine*): one mechanism for
undo send and send later. ``schedule_send`` checks a draft as Send would and holds it: a
``held`` row of ``app.scheduled_send`` and the worker's job ``send_draft`` from ``send_at``,
written in one transaction (``mailroom.worker.sends`` sends it, through ``send_held``);
``hold_send`` holds the user's Send for an Undo. ``cancel_scheduled`` deletes both while the job
has not taken the row, leaving the draft in Drafts; ``read_scheduled`` and ``list_scheduled`` read
them. ``schedule_later`` is send later's: ``schedule_send`` at a time still to come."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, cast

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from mailroom.accounts.store import load_accounts
from mailroom.audit import WEB, AuditTrail, Origin
from mailroom.errors import Refusal
from mailroom.mail.service._shared import (
    _Accounts,
    _accounts,
    _call,
    _message_ref,
    _special_path,
)
from mailroom.mail.service.compose import (
    DRAFT_GONE,
    SEND_HOLDS_THE_DRAFT,
    _deliverable,
    _send_key,
    _writable,
)
from mailroom.mail.service.send import deliver_admitted, draft_unsettled, settled_folders
from mailroom.models import ApiToken, ScheduledSend
from mailroom.oauth.models import OAuthClient
from mailroom_core import mail as core
from mailroom_core import send as gate

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager

    from sqlalchemy.orm import Session

    from mailroom.context import AppContext
    from mailroom.reach import Reach
    from mailroom_core.config import AccountConfig, TimeZoneName
    from mailroom_core.mail import (
        AuditResult,
        Authoriser,
        MailConnections,
        MessageRef,
        Note,
    )
    from mailroom_core.store import AccountStore

logger = logging.getLogger(__name__)

SENT: Final = "ok"
"""``send_held``'s outcome once the gate delivered the held send's key, then or before."""
MAX_AHEAD: Final = timedelta(days=366)
"""How far ahead a send can be held."""
SEND_STARTED: Final = "This draft is being sent, or was sent already."
ALREADY_SCHEDULED: Final = "This draft is scheduled already: cancel that first."
NOT_AHEAD: Final = "A send time must be in the future."
_NO_SUCH: Final = "No such scheduled send."
_ID: Final = re.compile(r"[0-9]{1,18}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ScheduledView:
    """A held send as a client lists it."""

    scheduled_id: str
    account: int
    ref: str
    """The draft's, as it was scheduled."""
    subject: str
    recipients: tuple[str, ...]
    """The draft's subject and addresses (To, Cc, Bcc) as it was scheduled."""
    send_at: datetime
    state: str
    """``held``; ``sending`` once the worker took it, when it can no longer be cancelled;
    ``failed`` when it ended before the gate had a send for it (``code``), or in its delivery."""
    code: str | None = None
    by: Authoriser = "user"
    """Who scheduled it: the user on the web, or an agent."""
    app: str | None = None
    """An agent's connected app: its name, its client id when it gave none. Or its API token's
    name, while it has one."""


def _view(db: Session, row: ScheduledSend) -> ScheduledView:
    name = None
    if row.client_id is not None:
        name = db.scalar(
            select(OAuthClient.client_name).where(OAuthClient.client_id == row.client_id)
        )
    elif row.token_id is not None:
        name = db.scalar(
            select(ApiToken.name).where(
                ApiToken.id == row.token_id, ApiToken.user_id == row.user_id
            )
        )
    return ScheduledView(
        scheduled_id=str(row.id),
        account=row.account_id,
        ref=row.ref,
        subject=row.subject,
        recipients=tuple(row.recipients),
        send_at=row.send_at,
        state=row.state,
        code=row.code,
        by="user" if row.via == "web" else "agent",
        app=name or row.client_id,
    )


def _log(event: str, user_id: int, origin: Origin, row: ScheduledSend) -> None:
    logger.info(
        event,
        extra={
            "event": event,
            "user_id": user_id,
            "via": origin.via,
            "account_id": row.account_id,
            "scheduled_id": row.id,
        },
    )


class _AlreadyScheduled(Refusal):
    """``already_scheduled``, with the draft's held send."""

    def __init__(self, existing: ScheduledView | None) -> None:
        super().__init__("conflict", "already_scheduled", ALREADY_SCHEDULED)
        self.existing = existing


def schedule_send(
    ctx: AppContext,
    user_id: int,
    ref: str,
    send_at: datetime | timedelta,
    *,
    origin: Origin = WEB,
) -> ScheduledView:
    """Hold the draft ``ref`` for the worker to send at ``send_at`` (a moment past sends at
    once; a ``timedelta``, that long after the check), as ``origin.actor``: checked now as
    ``send_draft`` checks it, an agent's against the send mode too, and again by the gate when it
    goes. ``already_scheduled`` while the draft has a held send, ``send_started`` once a send of it
    began."""
    if isinstance(send_at, datetime):
        if send_at.tzinfo is None:
            raise Refusal("invalid", "invalid_argument", "A send time needs its offset.")
        if send_at > datetime.now(UTC) + MAX_AHEAD:
            raise Refusal("invalid", "invalid_argument", "A send can be held for a year at most.")
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    account = accounts.config(message.account)
    _writable(account)

    by: Authoriser = origin.actor

    def checked(connections: MailConnections) -> tuple[str, str, tuple[str, ...]]:
        ready = gate.prepare_draft(connections, account, message, accounts.write_limits)
        stored = ready.message
        to, cc, bcc = _deliverable(stored.to), _deliverable(stored.cc), _deliverable(stored.bcc)
        recipients = (*to, *cc, *bcc)
        core.check_recipient_count(recipients, accounts.write_limits.max_recipients)
        if not gate.sends_now(account, replace(stored, to=to, cc=cc, bcc=bcc), by):
            raise gate.kept_as_draft(account)
        return _send_key(ready.draft), stored.subject, tuple(m.address for m in recipients)

    key, subject, addresses = _call(
        ctx, user_id, accounts, "mail_send_checked", origin.via, account.id, checked
    )
    earlier = ctx.store(user_id).send_state_by_key(key)
    if earlier is not None and earlier.state in SEND_HOLDS_THE_DRAFT:
        raise Refusal("conflict", "send_started", SEND_STARTED)
    from mailroom.worker.tasks import defer_scheduled_send

    if isinstance(send_at, timedelta):
        send_at = datetime.now(UTC) + send_at
    with ctx.session_maker() as db:
        # One that failed is done with: scheduling the draft again replaces it.
        db.execute(
            delete(ScheduledSend).where(
                ScheduledSend.user_id == user_id,
                ScheduledSend.send_key == key,
                ScheduledSend.state == "failed",
            )
        )
        row = ScheduledSend(
            user_id=user_id,
            account_id=account.id,
            ref=core.format_ref(message),
            send_key=key,
            subject=subject,
            recipients=list(addresses),
            via=origin.via,
            client_id=origin.client_id,
            grant_id=origin.grant_id,
            token_id=origin.token_id,
            send_at=send_at,
            state="held",
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            existing = db.scalar(
                select(ScheduledSend).where(
                    ScheduledSend.user_id == user_id, ScheduledSend.send_key == key
                )
            )
            raise _AlreadyScheduled(None if existing is None else _view(db, existing)) from None
        row.job_id = defer_scheduled_send(db, scheduled_id=row.id, send_at=send_at)
        db.commit()
        _log("mail_send_scheduled", user_id, origin, row)
        return _view(db, row)


def schedule_later(
    ctx: AppContext, user_id: int, ref: str, send_at: datetime, *, origin: Origin = WEB
) -> ScheduledView:
    """Send later: ``schedule_send`` at ``send_at``, which must still be to come."""
    if send_at.tzinfo is not None and send_at <= datetime.now(UTC):
        raise Refusal("invalid", "invalid_argument", NOT_AHEAD)
    return schedule_send(ctx, user_id, ref, send_at, origin=origin)


def hold_send(
    ctx: AppContext, user_id: int, ref: str, delay: timedelta, *, origin: Origin = WEB
) -> ScheduledView | None:
    """A Send of the draft ``ref`` held ``delay`` for an Undo (``schedule_send``). Asked
    again while it is held or being sent, the same held send; ``already_scheduled`` while the
    draft is held for later than that. None when a send of the draft began already or the draft
    is gone, which ``send_draft`` answers."""
    try:
        return schedule_send(ctx, user_id, ref, delay, origin=origin)
    except _AlreadyScheduled as refusal:
        held = refusal.existing
        if held is not None and held.send_at <= datetime.now(UTC) + delay:
            return held
        raise
    except Refusal as refusal:
        if refusal.code in DRAFT_GONE or refusal.code == "send_started":
            return None
        raise


def _own(
    ctx: AppContext, db: Session, user_id: int, scheduled_id: str, *, lock: bool = True
) -> ScheduledSend:
    row = None
    if _ID.fullmatch(scheduled_id):
        query = select(ScheduledSend).where(
            ScheduledSend.id == int(scheduled_id),
            ScheduledSend.user_id == user_id,
            ctx.reach.covers(ScheduledSend.account_id),
        )
        row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise _no_such()
    return row


def _no_such() -> Refusal:
    return Refusal("not_found", "not_found", _NO_SUCH, coded=True)


def cancel_scheduled(
    ctx: AppContext, user_id: int, scheduled_id: str, *, origin: Origin = WEB
) -> ScheduledView:
    """Delete the user's held send ``scheduled_id`` and its job, the draft left in Drafts; what
    it was. ``send_started`` once the worker has taken it; another user's is ``not_found``. An
    agent cancels only what its API token scheduled, every other being ``not_found`` to it, and
    leaves an audit record."""
    from mailroom.worker.tasks import cancel_job

    with ctx.session_maker() as db:
        row = _own(ctx, db, user_id, scheduled_id)
        if origin.agent and (origin.token_id is None or row.token_id != origin.token_id):
            raise _no_such()
        account_id, ref = row.account_id, row.ref
        view = None if row.state == "sending" else _view(db, row)
        if view is not None:
            if row.job_id is not None:
                cancel_job(db, row.job_id)
            db.delete(row)
            db.commit()
            _log("mail_send_unscheduled", user_id, origin, row)
    if origin.agent:
        result = "ok" if view is not None else "send_started"
        _audit_cancel(ctx, user_id, origin, account_id, ref, cast("AuditResult", result))
    if view is None:
        raise Refusal("conflict", "send_started", SEND_STARTED)
    return view


def _audit_cancel(
    ctx: AppContext, user_id: int, origin: Origin, account_id: int, ref: str, result: AuditResult
) -> None:
    draft = core.parse_ref(ref)
    AuditTrail(ctx.session_maker, user_id, origin, "send_cancel").note(
        core.AuditItem(account=account_id, folder=draft.folder, uid=draft.uid, result=result)
    )


def read_scheduled(ctx: AppContext, user_id: int, scheduled_id: str) -> ScheduledView:
    """The user's held send ``scheduled_id``; ``not_found`` once it went, or was cancelled."""
    with ctx.session_maker() as db:
        return _view(db, _own(ctx, db, user_id, scheduled_id, lock=False))


def scheduled_for(ctx: AppContext, user_id: int, ref: str) -> ScheduledView | None:
    """The user's held send of the draft ``ref`` that is still to go (``held``, or ``sending``
    once the worker took it), if any: a draft opened in the composer says so, and the user's
    first change cancels it."""
    with ctx.session_maker() as db:
        row = db.scalar(
            select(ScheduledSend).where(
                ScheduledSend.user_id == user_id,
                ctx.reach.covers(ScheduledSend.account_id),
                ScheduledSend.ref == core.format_ref(core.parse_ref(ref)),
                ScheduledSend.state.in_(("held", "sending")),
            )
        )
        return None if row is None else _view(db, row)


def list_scheduled(ctx: AppContext, user_id: int) -> list[ScheduledView]:
    """The user's held sends, soonest first."""
    with ctx.session_maker() as db:
        rows = db.scalars(
            select(ScheduledSend)
            .where(ScheduledSend.user_id == user_id, ctx.reach.covers(ScheduledSend.account_id))
            .order_by(ScheduledSend.send_at, ScheduledSend.id)
        )
        return [_view(db, row) for row in rows]


def forget_scheduled(db: Session, user_id: int, account_id: int | None = None) -> None:
    """Delete the user's held sends, of ``account_id`` only when given, with their jobs, inside
    ``db``'s transaction: with the account or the user."""
    from mailroom.worker.tasks import cancel_job

    query = select(ScheduledSend).where(ScheduledSend.user_id == user_id)
    if account_id is not None:
        query = query.where(ScheduledSend.account_id == account_id)
    for row in db.scalars(query.with_for_update()):
        if row.job_id is not None:
            cancel_job(db, row.job_id)
        db.delete(row)
    db.flush()


def send_held(
    store: AccountStore,
    smtp: gate.SmtpSender,
    lease: Callable[[int], AbstractContextManager[MailConnections]],
    note: Note,
    *,
    account_id: int,
    ref: str,
    key: str,
    by: Authoriser,
    time_zone: TimeZoneName,
    resync: Callable[[Sequence[str]], None],
    reach: Reach,
) -> str:
    """The worker's send of a held draft at ``ref``, held with ``key``, as ``by``, within
    ``reach`` (its app's as it is now, whose ceiling the gate keeps to), on the connections
    ``lease`` gives for the accounts' revision: ``SENT``, or the code it ended with. Once a send of
    ``key`` holds the draft, that send decides and the draft is not read. Once this one is
    delivered, ``resync`` is given the account's folders it changed (``settled_folders``), for the
    mail index; it must not raise."""
    earlier = store.send_state_by_key(key)
    if earlier is not None and earlier.state in SEND_HOLDS_THE_DRAFT:
        return SENT if earlier.state == "sent" else "delivery_unknown"
    accounts = _Accounts(reach.within(load_accounts(store)))
    account = accounts.config(account_id)
    with lease(accounts.revision) as connections:
        deps = gate.GateDeps(
            connections=connections,
            smtp=smtp,
            send_log=store,
            limits=accounts.write_limits,
            time_zone=time_zone,
        )
        return _through_gate(deps, account, core.parse_ref(ref), key, note, by, resync)


def _through_gate(
    deps: gate.GateDeps,
    account: AccountConfig,
    ref: MessageRef,
    key: str,
    note: Note,
    by: Authoriser,
    resync: Callable[[Sequence[str]], None],
) -> str:
    try:
        ready = gate.prepare_draft(deps.connections, account, ref, deps.limits)
        _deliverable((*ready.message.to, *ready.message.cc, *ready.message.bcc))
    except (core.MailError, Refusal) as error:
        return error.code
    if _send_key(ready.draft) != key:
        return "stale_reference"
    try:
        admitted = gate.admit(
            deps, account, ready.message, note, by=by, prepared=ready.prepared, key=key
        )
    except core.MailError as error:
        return error.code
    if isinstance(admitted, gate.Earlier):
        return SENT if admitted.send.state == "sent" else "delivery_unknown"
    if isinstance(admitted, gate.Drafted):  # a stored draft is refused, not drafted again
        return "invalid_argument"

    def settle(filed: bool) -> tuple[gate.SendNote, ...]:
        try:
            return gate.settle_draft(deps.connections, ready.draft, note, filed=filed)
        except Exception as error:
            logger.warning(
                "mail_send_not_settled",
                extra={"event": "mail_send_not_settled", "error_type": type(error).__name__},
            )
            return draft_unsettled(ready.draft, filed)

    delivered = deliver_admitted(
        deps, admitted, note, file=lambda sent: gate.file_sent(deps, sent, note), settle=settle
    )
    if delivered.state != "sent":
        return delivered.code
    draft = ready.draft
    sent_in = _special_path(deps.connections, account.id, "sent")
    resync(settled_folders(account.id, sent_in, (draft.ref, draft.answers, draft.forwards)))
    return SENT
