"""The folders browsers are viewing (``app.mail_view_interest``), which the worker watches
through IDLE so their index stays fresh while they are open
(``docs/requirements/mail-client.md``, *Index*). The API holds one for each event stream that
names a folder (``mailroom.mail.events``)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import delete, literal, select, text
from sqlalchemy.dialects.postgresql import insert

from mailroom.models import MailIndexFolder, MailViewInterest

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

INTEREST_CHANNEL: Final = "mailroom_interest"
"""The Postgres channel a viewing interest written is announced on, so the worker's IDLE
supervisor reconciles at once; the payload carries nothing."""
INTEREST_LASTS: Final = timedelta(seconds=90)
"""How long an interest lasts past its stream's last heartbeat."""


def prune_interest(db: Session, now: datetime) -> None:
    """Delete the interests expired at ``now`` (the caller commits)."""
    db.execute(delete(MailViewInterest).where(MailViewInterest.expires_at <= now))


def hold_interest(
    db: Session, user_id: int, account_id: int, folder: str, now: datetime, *, announce: bool
) -> None:
    """Hold the user's interest in ``folder`` until ``INTEREST_LASTS`` past ``now``, when it is
    one of the user's folders on the index (any other has nothing to keep fresh); with
    ``announce`` (a stream's first hold, not its heartbeats'), announce it on
    ``INTEREST_CHANNEL``. The caller commits."""
    indexed = select(
        literal(user_id), literal(account_id), MailIndexFolder.path, literal(now + INTEREST_LASTS)
    ).where(
        MailIndexFolder.user_id == user_id,
        MailIndexFolder.account_id == account_id,
        MailIndexFolder.path == folder,
        MailIndexFolder.enabled,
    )
    statement = insert(MailViewInterest).from_select(
        ["user_id", "account_id", "folder", "expires_at"], indexed
    )
    written = db.execute(
        statement.on_conflict_do_update(
            index_elements=["user_id", "account_id", "folder"],
            set_={"expires_at": statement.excluded.expires_at},
        ).returning(MailViewInterest.folder)
    ).all()
    if written and announce:
        _announce(db)


def drop_interest(db: Session, user_id: int, account_id: int, folder: str) -> None:
    """Delete the user's interest in ``folder`` and announce it when there was one. The caller
    commits."""
    dropped = db.execute(
        delete(MailViewInterest)
        .where(
            MailViewInterest.user_id == user_id,
            MailViewInterest.account_id == account_id,
            MailViewInterest.folder == folder,
        )
        .returning(MailViewInterest.folder)
    ).all()
    if dropped:
        _announce(db)


def _announce(db: Session) -> None:
    db.execute(text("SELECT pg_notify(:channel, '')"), {"channel": INTEREST_CHANNEL})
