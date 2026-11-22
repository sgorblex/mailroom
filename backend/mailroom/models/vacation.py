from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User


class Vacation(Base):
    """One account's vacation auto-reply (``mailroom.mail.vacation``): on or off, the days it
    covers in the user's time zone, and the reply. The account lives in mailroom-core's schema
    ``core``, keyed by its id there; no row means off."""

    __tablename__ = "vacation"

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When it was last switched on: mail that arrived before is never answered."""
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    """The first and last day answered, in the user's time zone; None: open."""
    subject: Mapped[str] = mapped_column(Text)
    html: Mapped[str] = mapped_column(Text)
    """Sanitised as an outgoing message's HTML is, without images."""
    identity: Mapped[str | None] = mapped_column(Text)
    """The identity it goes from, lowercase; None: the one the message was addressed to."""
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VacationReplied(Base):
    """Who an account's vacation reply answered last, and when: claimed before each reply, so a
    sender gets one per ``REPLY_INTERVAL`` at most."""

    __tablename__ = "vacation_replied"

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sender: Mapped[str] = mapped_column(Text, primary_key=True)
    """Lowercase."""
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
