from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User


class SenderViewPreference(Base):
    """A sender whose mail a user reads as text, not HTML, by From address (lowercase). It only
    changes how a message is shown, so any From counts: no sender authentication is asked."""

    __tablename__ = "sender_view_preference"
    __table_args__ = (CheckConstraint("view = 'text'", name="view"),)

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), primary_key=True)
    address: Mapped[str] = mapped_column(Text, primary_key=True)
    view: Mapped[str] = mapped_column(Text, server_default="text")
    """The view the sender's mail opens in; ``text`` only, HTML being the default."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
