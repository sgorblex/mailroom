"""Each account's vacation auto-reply (``docs/requirements/mail-client.md``, *Vacation reply*):
its settings (``app.vacation``), the new mail it answers, and who it answered lately
(``app.vacation_replied``).

The index's sync is where the worker learns of new mail: its new rows in INBOX (on Gmail, All
Mail's rows labelled ``\\Inbox``), from IDLE's new mail or the dispatcher's turn, each queue a
``vacation_reply`` job in the transaction that stores them, while the account's vacation answers.
The job (``mailroom.worker.vacation``) decides and sends.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert

from mailroom.auth.users import time_zone_of
from mailroom.errors import Refusal
from mailroom.filters.transfers import ledger_uids
from mailroom.models import MailIndexFolder, Vacation, VacationReplied
from mailroom_core import mail as core
from mailroom_core import render

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from mailroom_core.config import AccountConfig
    from mailroom_core.mail import IndexedMessage

logger = logging.getLogger(__name__)

MAX_VACATION_CHARS: Final = 20_000
"""Of the reply's HTML as written."""
MAX_SUBJECT_CHARS: Final = 200
REPLY_INTERVAL: Final = timedelta(days=4)
"""A sender is answered once in this long at most (RFC 3834 suggests a few days)."""
INBOX_LABEL: Final = "\\Inbox"
ARRIVAL_SLACK: Final = timedelta(minutes=1)
"""Mail that arrived this long before the reply was switched on still counts: the arrival time is
the mail server's clock, in whole seconds."""


@dataclass(frozen=True, slots=True)
class VacationSettings:
    enabled: bool = False
    starts_on: date | None = None
    ends_on: date | None = None
    subject: str = ""
    html: str = ""
    text: str = ""
    """``html`` as the reply's text part has it."""
    identity: str | None = None
    """The identity it goes from; None: the one the message was addressed to."""


def _settings(row: Vacation | None) -> VacationSettings:
    if row is None:
        return VacationSettings()
    return VacationSettings(
        enabled=row.enabled,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        subject=row.subject,
        html=row.html,
        text=core.html_text(row.html).strip(),
        identity=row.identity,
    )


def vacation_of(db: Session, user_id: int, account_id: int) -> VacationSettings:
    """The account's vacation reply; the caller checked the account is the user's."""
    return _settings(db.get(Vacation, (user_id, account_id)))


def _invalid(message: str) -> Refusal:
    return Refusal("invalid", "invalid_vacation", message)


def set_vacation(
    db: Session, user_id: int, account: AccountConfig, change: VacationSettings
) -> VacationSettings:
    """Keeps the account's vacation reply, its HTML sanitised (images dropped), and commits.
    ``invalid_vacation`` for an identity that is not the account's, days out of order, or one
    switched on without a subject or a text. The caller checked the account is the user's."""
    identity = None if change.identity is None else change.identity.strip().lower()
    if identity is not None and identity not in {i.address.lower() for i in account.identities}:
        msg = "That address is not one of this account's identities."
        raise _invalid(msg)
    if change.starts_on and change.ends_on and change.ends_on < change.starts_on:
        msg = "The last day comes before the first."
        raise _invalid(msg)
    html = render.outgoing_html(change.html, data_images=False, signature_mark=False).html.strip()
    subject = render.clean_header(change.subject.strip(), MAX_SUBJECT_CHARS)
    if change.enabled and not (subject and core.html_text(html).strip()):
        msg = "A vacation reply needs a subject and a text."
        raise _invalid(msg)
    row = db.get(Vacation, (user_id, account.id))
    if row is None:
        row = Vacation(user_id=user_id, account_id=account.id, enabled=False)
        db.add(row)
    if change.enabled and not row.enabled:
        row.enabled_at = datetime.now(UTC)
    row.enabled = change.enabled
    row.starts_on, row.ends_on = change.starts_on, change.ends_on
    row.subject, row.html, row.identity = subject, html, identity
    db.commit()
    return _settings(row)


def answers_on(settings: VacationSettings | Vacation, day: date) -> bool:
    """Whether the vacation reply is on and ``day`` (the user's) is among its days."""
    return (
        settings.enabled
        and (settings.starts_on is None or settings.starts_on <= day)
        and (settings.ends_on is None or day <= settings.ends_on)
    )


def today_of(db: Session, user_id: int, now: datetime | None = None) -> date:
    """The user's day at ``now``, in their time zone."""
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(time_zone_of(db, user_id))).date()


def inbox_indexed(db: Session, user_id: int, account_id: int) -> bool:
    """Whether the account's new mail in INBOX reaches ``queue_vacation_replies``: INBOX's index
    row on and synced (a folder still in its first sync, or off, stores no mail as new). On
    Gmail, whose only row is All Mail's (INBOX is a label of it), that row's."""
    rows = db.scalars(
        select(MailIndexFolder).where(
            MailIndexFolder.user_id == user_id, MailIndexFolder.account_id == account_id
        )
    ).all()
    inbox = [row for row in rows if row.path.upper() == "INBOX"]
    return any(row.enabled and row.state == "ready" for row in inbox or rows)


def in_inbox(message: IndexedMessage) -> bool:
    return message.folder.upper() == "INBOX" or INBOX_LABEL in message.gmail_labels


def queue_vacation_replies(
    db: Session, user_id: int, account_id: int, messages: Sequence[IndexedMessage]
) -> int:
    """Queue ``vacation_reply`` for each of the new ``messages`` the account's vacation answers
    (in INBOX, arrived after it was switched on, on one of its days, and not in the ledger the
    filter runs leave alone: a snoozed message woken comes back, it does not arrive), in ``db``'s
    transaction (the caller commits); how many."""
    if not messages:
        return 0
    row = db.get(Vacation, (user_id, account_id))
    if row is None or not row.enabled:
        return 0
    from mailroom.worker.tasks import defer_vacation_reply

    zone = ZoneInfo(time_zone_of(db, user_id))
    returned: dict[tuple[str, int], set[int]] = {}
    queued = 0
    for message in messages:
        arrived = message.received or datetime.now(UTC)
        if not in_inbox(message) or (
            row.enabled_at is not None and arrived < row.enabled_at - ARRIVAL_SLACK
        ):
            continue
        ref = message.message_ref
        folder = (ref.folder, ref.uid_validity)
        if folder not in returned:
            returned[folder] = ledger_uids(db, user_id, account_id, *folder)
        if ref.uid in returned[folder]:  # a snoozed message woken, or a filter's copy
            continue
        if not answers_on(row, arrived.astimezone(zone).date()):
            continue
        queued += defer_vacation_reply(
            db, user_id=user_id, account_id=account_id, ref=core.format_ref(message.message_ref)
        )
    return queued


def claim_reply(db: Session, user_id: int, account_id: int, sender: str, now: datetime) -> bool:
    """Write down a reply to ``sender`` at ``now`` and commit, unless the account answered them
    less than ``REPLY_INTERVAL`` ago; whether it was written. The account's replies older than
    that, which decide nothing any more, go."""
    db.execute(
        delete(VacationReplied).where(
            VacationReplied.user_id == user_id,
            VacationReplied.account_id == account_id,
            VacationReplied.sender != sender,
            VacationReplied.at <= now - REPLY_INTERVAL,
        )
    )
    statement = insert(VacationReplied).values(
        user_id=user_id, account_id=account_id, sender=sender, at=now
    )
    claimed = db.execute(
        statement.on_conflict_do_update(
            index_elements=[
                VacationReplied.user_id,
                VacationReplied.account_id,
                VacationReplied.sender,
            ],
            set_={"at": statement.excluded.at},
            where=VacationReplied.at <= now - REPLY_INTERVAL,
        ).returning(VacationReplied.sender)
    ).first()
    db.commit()
    return claimed is not None


def release_reply(db: Session, user_id: int, account_id: int, sender: str, at: datetime) -> None:
    """Undo ``claim_reply``'s claim made ``at``, for a reply that never reached SMTP, and
    commit: the sender's next message is answered."""
    db.execute(
        delete(VacationReplied).where(
            and_(
                VacationReplied.user_id == user_id,
                VacationReplied.account_id == account_id,
                VacationReplied.sender == sender,
                VacationReplied.at == at,
            )
        )
    )
    db.commit()


def forget_vacation(db: Session, user_id: int, account_id: int | None = None) -> None:
    """Delete the vacation replies and their senders, of one account or all the user's, in
    ``db``'s transaction."""
    for model in (Vacation, VacationReplied):
        statement = delete(model).where(model.user_id == user_id)
        if account_id is not None:
            statement = statement.where(model.account_id == account_id)
        db.execute(statement)


def own_addresses(accounts: Sequence[AccountConfig]) -> frozenset[str]:
    """Every identity of the user's accounts, lowercase."""
    return frozenset(i.address.lower() for account in accounts for i in account.identities)
