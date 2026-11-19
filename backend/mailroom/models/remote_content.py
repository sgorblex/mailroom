from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User


class RemoteContentSender(Base):
    """A sender whose remote content a user shows without asking, by From address (lowercase).
    Added only from a message whose DMARC passed at the account's own authentication server."""

    __tablename__ = "remote_content_sender"

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), primary_key=True)
    address: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
