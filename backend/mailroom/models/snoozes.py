from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User


class Snooze(Base):
    """A message snoozed until ``until`` (``mailroom.mail.service.snooze``): moved to its
    account's Snoozed folder, with the procrastinate job ``wake_snooze`` queued in the same
    transaction to move it back to ``folder``, unread. The row goes once it is back, or found gone.
    The account lives in mailroom-core's schema ``core``, keyed by its id there."""

    __tablename__ = "snooze"
    __table_args__ = (
        UniqueConstraint("user_id", "account_id", "message_id"),
        CheckConstraint("via IN ('web', 'mcp', 'api')", name="via"),
        Index("ix_snooze_user_id", "user_id", "until"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(BigInteger)
    via: Mapped[str] = mapped_column(Text)
    """Who snoozed it, as ``Origin.via``: the wake's audit records are theirs."""
    message_id: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    """Its Message-ID and RFC822.SIZE: what finds it again in Snoozed."""
    folder: Mapped[str] = mapped_column(Text)
    """Where it was, and goes back to."""
    snoozed: Mapped[str] = mapped_column(Text)
    """The Snoozed folder's path."""
    ref: Mapped[str | None] = mapped_column(Text)
    """Its ref in Snoozed, when the server said (COPYUID): tried first."""
    subject: Mapped[str] = mapped_column(Text, server_default="")
    sender: Mapped[str | None] = mapped_column(Text)
    """Its subject and From address, for the Snoozed list."""
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    code: Mapped[str | None] = mapped_column(Text)
    """Why the last wake failed (it is tried again later); None until one did."""
    job_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
