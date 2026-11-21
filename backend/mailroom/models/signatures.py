from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User


class IdentitySignature(Base):
    """The signature of one identity of one of the user's accounts (``mailroom.mail.signatures``).
    The account lives in mailroom-core's schema ``core``, keyed by its id there; no row means no
    signature."""

    __tablename__ = "identity_signature"

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    address: Mapped[str] = mapped_column(Text, primary_key=True)
    """The identity's address, lowercase."""
    html: Mapped[str] = mapped_column(Text)
    """Sanitised as an outgoing message's HTML is, without images."""
    text: Mapped[str] = mapped_column(Text)
    """``html`` as a message's text part has it."""
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
