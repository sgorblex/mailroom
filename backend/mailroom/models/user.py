from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base


class User(Base):
    """A person who signs in. Each user's mail accounts and filters are theirs alone."""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Stored as normalised by ``mailroom.auth.users.normalized_username``: lowercase.
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    """A signed-in browser. The cookie carries the raw token; only its SHA-256 is stored."""

    __tablename__ = "user_session"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
