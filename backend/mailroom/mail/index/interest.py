"""The folders browsers are viewing (``app.mail_view_interest``), which the worker watches
through IDLE so their index stays fresh while they are open
(``docs/requirements/mail-client.md``, *Index*)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from sqlalchemy import delete

from mailroom.models import MailViewInterest

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

INTEREST_CHANNEL: Final = "mailroom_interest"
"""The Postgres channel a viewing interest written is announced on, so the worker's IDLE
supervisor reconciles at once; the payload carries nothing."""


def prune_interest(db: Session, now: datetime) -> None:
    """Delete the interests expired at ``now`` (the caller commits)."""
    db.execute(delete(MailViewInterest).where(MailViewInterest.expires_at <= now))
