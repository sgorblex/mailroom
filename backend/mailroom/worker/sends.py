"""A scheduled draft, sent: the ``send_draft`` job, on the user's connections from the worker's
pool, through the same gate as the web's Send (``docs/requirements/mail-client.md``, *Send state
machine*).

The job first moves its row from ``held`` to ``sending`` and commits, so a cancel from then on is
refused, and one that came first has left nothing to do. Then ``send_held``: unless a send of the
key began already (the draft sent at once meanwhile, from another tab or by an agent), it reads the
draft at the row's ref, which must still be the version scheduled (the same send key), and takes
it through ``admit`` with that key, ``deliver``, ``file_sent`` and ``settle_draft``, as the
scheduler's ``by``, audited as its connected app. An agent's held send first needs its grant:
revoked, or no longer approving ``mail_send`` or the account, it fails (``grant_revoked``,
``not_approved``; its API token instead: ``token_revoked`` once revoked or expired) before the
draft is read; its send ceiling is the gate's to keep to. A delivered
send has the folders it changed synced again into the mail index (``index_writes.resync``). The key
is claimed before SMTP, so the job run again after a worker stopped between the claim and the
delivery finds that send and delivers nothing. Once the gate has delivered the key, or had
delivered it before, the row goes: the send's own row says the rest. Anything else, an unexpected
error too (``internal``), leaves it ``failed`` with its code, the draft where it was. A job that
ends before the gate audited anything (the grant, the draft gone or changed, the account unusable,
its password missing, an unexpected error) writes a refused record of its own, its code and the
draft's folder and UID: nothing of the message, which it may not have read.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Final, cast

from sqlalchemy import delete, update

from mailroom.accounts.store import AccountNotFound, user_store
from mailroom.audit import AuditTrail, Origin
from mailroom.auth.api_tokens import token_reach
from mailroom.auth.users import time_zone_of
from mailroom.mail.connections import PASSWORD_MISSING, PasswordMissing, unusable
from mailroom.mail.index import writes as index_writes
from mailroom.mail.service.scheduled import SENT, send_held
from mailroom.mail.service.send import smtp_sender
from mailroom.models import ScheduledSend
from mailroom.oauth.grants import GrantRefusal, grant_reach
from mailroom.reach import WHOLE
from mailroom_core.mail import AuditItem, MailError, parse_ref

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.accounts.service import Via
    from mailroom.reach import Reach
    from mailroom.worker.context import Mailboxes
    from mailroom_core.config import TimeZoneName
    from mailroom_core.mail import AuditResult, Authoriser, Note

logger = logging.getLogger(__name__)

GONE: Final = "gone"
MAIL_SEND_GROUP: Final = "mail_send"
INTERNAL: Final = "internal"


@dataclass(frozen=True, slots=True)
class _Held:
    id: int
    user_id: int
    account_id: int
    ref: str
    send_key: str
    via: Via
    client_id: str | None
    grant_id: str | None
    token_id: int | None

    @property
    def by(self) -> Authoriser:
        return "user" if self.via == "web" else "agent"


def send_scheduled(sessions: sessionmaker[Session], mailboxes: Mailboxes, scheduled_id: int) -> str:
    """Send the scheduled draft ``scheduled_id`` and settle its row; the outcome: ``ok``,
    ``gone`` (cancelled, or its account removed) or the code the row was left ``failed`` with."""
    taken = _take(sessions, scheduled_id)
    outcome = GONE
    if taken is not None:
        held, zone = taken
        audit = _Audit(sessions, held)
        try:
            outcome = _send(sessions, mailboxes, held, zone, audit.note, _reach(sessions, held))
        except GrantRefusal as refusal:
            outcome = refusal.code
        except Exception as error:
            # Its key keeps the draft from going out twice if the user schedules it again.
            logger.warning(
                "mail_scheduled_send_broke",
                extra={"event": "mail_scheduled_send_broke", "error_type": type(error).__name__},
            )
            outcome = INTERNAL
        audit.stopped(outcome)
        with sessions() as db:
            row = ScheduledSend.id == held.id
            if outcome == SENT:
                db.execute(delete(ScheduledSend).where(row))
            else:
                db.execute(update(ScheduledSend).where(row).values(state="failed", code=outcome))
            db.commit()
    logger.info(
        "mail_scheduled_send",
        extra={
            "event": "mail_scheduled_send",
            "scheduled_id": scheduled_id,
            "user_id": None if taken is None else taken[0].user_id,
            "account_id": None if taken is None else taken[0].account_id,
            "outcome": outcome,
        },
    )
    return outcome


def _take(sessions: sessionmaker[Session], scheduled_id: int) -> tuple[_Held, TimeZoneName] | None:
    """The row, moved to ``sending`` and committed, with its user's time zone; None when it is
    gone or failed already."""
    with sessions() as db:
        taken = db.execute(
            update(ScheduledSend)
            .where(ScheduledSend.id == scheduled_id, ScheduledSend.state.in_(("held", "sending")))
            .values(state="sending")
            .returning(
                ScheduledSend.id,
                ScheduledSend.user_id,
                ScheduledSend.account_id,
                ScheduledSend.ref,
                ScheduledSend.send_key,
                ScheduledSend.via,
                ScheduledSend.client_id,
                ScheduledSend.grant_id,
                ScheduledSend.token_id,
            )
        ).one_or_none()
        db.commit()
        if taken is None:
            return None
        held = _Held(
            taken.id,
            taken.user_id,
            taken.account_id,
            taken.ref,
            taken.send_key,
            cast("Via", taken.via),
            taken.client_id,
            taken.grant_id,
            taken.token_id,
        )
        return held, time_zone_of(db, held.user_id)


class _Audit:
    """The job's audit trail, as the scheduler: whether the gate noted anything, and the refused
    record of a held send that ended before it did."""

    def __init__(self, sessions: sessionmaker[Session], held: _Held) -> None:
        origin = Origin(held.via, held.client_id, held.grant_id, held.token_id)
        self._trail = AuditTrail(sessions, held.user_id, origin, "send")
        self._held = held
        self.noted = False

    def note(self, item: AuditItem) -> None:
        self.noted = True
        self._trail.note(item)

    def stopped(self, outcome: str) -> None:
        """Record ``outcome`` unless the gate audited the send, or another send of the key
        decided (``ok``, or a ``delivery_unknown`` nothing noted), or it is ``gone``."""
        if self.noted or outcome in (SENT, GONE, "delivery_unknown"):
            return
        draft = parse_ref(self._held.ref)
        self._trail.note(
            AuditItem(
                account=self._held.account_id,
                folder=draft.folder,
                uid=draft.uid,
                by=self._held.by,
                delivery="refused",
                result=cast("AuditResult", outcome),
            )
        )


def _reach(sessions: sessionmaker[Session], held: _Held) -> Reach:
    """How far the held send's app reaches now; the whole for the user's, and for an app without
    a grant of Mailroom's own (an external issuer's token). Raises ``GrantRefusal`` when it may no
    longer go: its grant or API token revoked (a token expired, too), or ``mail_send`` or the
    account taken back from it."""
    if held.token_id is not None:
        with sessions() as db:
            return token_reach(db, held.user_id, held.token_id, MAIL_SEND_GROUP, held.account_id)
    if held.grant_id is None:
        return WHOLE
    with sessions() as db:
        return grant_reach(db, held.user_id, held.grant_id, MAIL_SEND_GROUP, held.account_id)


def _send(
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    held: _Held,
    zone: TimeZoneName,
    note: Note,
    reach: Reach,
) -> str:
    store = user_store(mailboxes.engine, held.user_id)
    try:
        refused = unusable(store, mailboxes.subkey, held.account_id)
    except AccountNotFound:
        return GONE
    if refused is not None:
        return refused[0]
    try:
        return send_held(
            store,
            smtp_sender(store, mailboxes.subkey, mailboxes.ssl_context),
            partial(mailboxes.lease, held.user_id),
            note,
            account_id=held.account_id,
            ref=held.ref,
            key=held.send_key,
            by=held.by,
            time_zone=zone,
            resync=partial(index_writes.resync, sessions, held.user_id, held.account_id),
            reach=reach,
        )
    except MailError as error:
        return error.code
    except PasswordMissing:
        return PASSWORD_MISSING
