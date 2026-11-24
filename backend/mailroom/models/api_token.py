from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User


class ApiToken(Base):
    """A user's personal API token (``mailroom.auth.api_tokens``). Only its SHA-256 is stored."""

    __tablename__ = "api_token"
    __table_args__ = (
        CheckConstraint(
            "send_ceiling IN ('off', 'drafts', 'approval', 'direct')", name="send_ceiling"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(8))
    """The part of the token shown in the list, to tell which one a script holds."""
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    tool_groups: Mapped[list[str]] = mapped_column(ARRAY(Text))
    """What it may do: the tool groups, as a connected app's grant approves them."""
    account_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    """The user's accounts it is narrowed to; null for every one, those added later too."""
    send_ceiling: Mapped[str | None] = mapped_column(Text)
    """The most it may do when it sends, whatever an account's send mode; null for no ceiling."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Null: never."""
