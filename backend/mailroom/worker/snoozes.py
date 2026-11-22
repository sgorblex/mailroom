"""A snoozed message brought back: the ``wake_snooze`` job, on the user's connections from the
worker's pool (``docs/requirements/mail-client.md``, *Snooze*).

At its time the job finds the message in Snoozed by its Message-ID and size, its ref there first
(``mailroom_core.mail.wake_snoozed``), marks it unread and moves it back to the folder it came
from (INBOX when that folder is gone; on Gmail the move adds INBOX's label back), audited
``wake`` as whoever snoozed it, and the row goes. A message no longer in Snoozed, moved or deleted
by another client, is recorded (``wake``, ``not_found``) and the row dropped; one the server
could not be asked about keeps its row with the code, and the job is queued again ``RETRY`` later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, cast

from sqlalchemy import delete, select

from mailroom.accounts.store import AccountNotFound, load_accounts, user_store
from mailroom.audit import AuditTrail, Entry, Origin, write_records
from mailroom.filters.transfers import ledger_copies
from mailroom.mail.connections import PASSWORD_MISSING, PasswordMissing, unusable
from mailroom.mail.index import writes as index_writes
from mailroom.models import Snooze
from mailroom_core import mail as core
from mailroom_core.mail import MailError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.accounts.service import Via
    from mailroom.worker.context import Mailboxes

logger = logging.getLogger(__name__)

OK: Final = "ok"
GONE: Final = "gone"
"""Woken already, or its account removed."""
MISSING: Final = "missing"
"""No longer in Snoozed: recorded and dropped."""
RETRY: Final = timedelta(minutes=15)
"""How long a wake that failed waits before it is tried again."""
GRACE: Final = timedelta(minutes=2)
"""A snooze younger than this whose message is not in Snoozed yet is tried again, not dropped:
its move may still be on its way."""


@dataclass(frozen=True, slots=True)
class _Due:
    id: int
    user_id: int
    account_id: int
    via: Via
    message_id: str
    size: int
    folder: str
    snoozed: str
    ref: str | None
    created_at: datetime


def wake_snooze(sessions: sessionmaker[Session], mailboxes: Mailboxes, snooze_id: int) -> str:
    """Bring the snoozed message ``snooze_id`` back and settle its row; the outcome: ``ok``,
    ``gone``, ``missing`` or the code it is retried with."""
    with sessions() as db:
        row = db.get(Snooze, snooze_id)
        due = None if row is None else _snapshot(row)
    outcome = GONE if due is None else _wake(sessions, mailboxes, due)
    logger.info(
        "mail_snooze_woken",
        extra={
            "event": "mail_snooze_woken",
            "snooze_id": snooze_id,
            "user_id": None if due is None else due.user_id,
            "account_id": None if due is None else due.account_id,
            "outcome": outcome,
        },
    )
    return outcome


def _snapshot(row: Snooze) -> _Due:
    return _Due(
        row.id,
        row.user_id,
        row.account_id,
        cast("Via", row.via),
        row.message_id,
        row.size,
        row.folder,
        row.snoozed,
        row.ref,
        row.created_at,
    )


def _wake(sessions: sessionmaker[Session], mailboxes: Mailboxes, due: _Due) -> str:
    store = user_store(mailboxes.engine, due.user_id)
    try:
        refused = unusable(store, mailboxes.subkey, due.account_id)
    except AccountNotFound:
        _settle(sessions, due.id)
        return GONE
    if refused is not None:
        return _retry(sessions, due, refused[0])
    accounts = load_accounts(store)
    try:
        with mailboxes.lease(due.user_id, accounts.revision) as connections:
            woken = core.wake_snoozed(
                connections,
                due.account_id,
                due.snoozed,
                due.message_id,
                due.folder,
                size=due.size or None,
                ref=due.ref,
            )
    except MailError as error:
        return _retry(sessions, due, error.code)
    except PasswordMissing:
        return _retry(sessions, due, PASSWORD_MISSING)
    origin = Origin(due.via)
    if woken is None:
        if datetime.now(UTC) - due.created_at < GRACE:
            return _retry(sessions, due, "not_found")
        write_records(
            sessions,
            due.user_id,
            [
                Entry(
                    actor=origin.actor,
                    action="wake",
                    account_id=due.account_id,
                    result="not_found",
                    detail={"snooze_id": due.id, "folder": due.snoozed, "destination": due.folder},
                )
            ],
        )
        _settle(sessions, due.id)
        return MISSING
    trail = AuditTrail(sessions, due.user_id, origin, "wake")
    for done in (woken.unread, woken.moved):
        trail.notes(done.items)
        index_writes.organized(sessions, due.user_id, done.items, done.groups, refs=[woken.ref])
    [group] = woken.moved.groups
    if group.error is not None:
        return _retry(sessions, due, group.error.code)
    _not_new(sessions, due, woken.refs)
    _settle(sessions, due.id)
    return OK


def _not_new(sessions: sessionmaker[Session], due: _Due, refs: Sequence[str]) -> None:
    """The woken message's refs into the ledger the filter runs leave alone (``ledger_skip``),
    as soon as the move answered: back in its folder it is no new mail, so neither the
    ``new_mail`` rule sets its arrival queues nor a muted thread's mark it read again, or move it.
    Its notification is the live updates', from the index. A failure is logged: it is back."""
    if not refs:
        return
    with sessions() as db:
        try:
            ledger_copies(db, due.user_id, refs)
        except MailError:
            logger.warning(
                "mail_snooze_woken_unguarded",
                extra={
                    "event": "mail_snooze_woken_unguarded",
                    "user_id": due.user_id,
                    "account_id": due.account_id,
                },
            )


def _settle(sessions: sessionmaker[Session], snooze_id: int) -> None:
    with sessions() as db:
        db.execute(delete(Snooze).where(Snooze.id == snooze_id))
        db.commit()


def _retry(sessions: sessionmaker[Session], due: _Due, code: str) -> str:
    """Keep the row with ``code`` and queue its job again ``RETRY`` from now."""
    from mailroom.worker.tasks import cancel_job, defer_snooze_wake

    with sessions() as db:
        row = db.scalar(select(Snooze).where(Snooze.id == due.id).with_for_update())
        if row is not None:
            if row.job_id is not None:  # one still waiting (a wake run outside its job)
                cancel_job(db, row.job_id)
            row.code = code
            row.job_id = defer_snooze_wake(db, snooze_id=row.id, until=datetime.now(UTC) + RETRY)
            db.commit()
    return code
