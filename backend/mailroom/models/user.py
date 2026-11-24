from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base


class User(Base):
    """A person who signs in. Each user's mail accounts and filters are theirs alone."""

    __tablename__ = "user"
    __table_args__ = (
        CheckConstraint("undo_send_seconds BETWEEN 0 AND 30", name="undo_send_seconds"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Stored as normalised by ``mailroom.auth.users.normalized_username``: lowercase.
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # An IANA name, canonical case; None until set, and UTC is used.
    time_zone: Mapped[str | None] = mapped_column(String(64))
    # What "mark all as read" on a folder with subfolders does first: True, them too.
    mark_read_subfolders: Mapped[bool] = mapped_column(Boolean, server_default=true())
    # The mail index's age limit: messages that arrived in the last this many days are indexed;
    # None: no limit of the user's own (``MAILROOM_INDEX_MAX_AGE_DAYS`` still caps it).
    index_max_age_days: Mapped[int | None] = mapped_column(Integer)
    # Whether the web's message lists group messages by conversation.
    conversations: Mapped[bool] = mapped_column(Boolean, server_default=true())
    # How long the web's Send holds a message for an Undo, in seconds; 0 sends at once.
    undo_send_seconds: Mapped[int] = mapped_column(Integer, server_default=text("10"))
    # How the web looks, on every device: only the choices made (``mailroom.auth.appearance``).
    appearance: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    """A signed-in browser. The cookie carries the raw token; only its SHA-256 is stored."""

    __tablename__ = "user_session"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # When it last reached the API, to within ``mailroom.auth.sessions.SEEN_EVERY``.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Browser and system, as ``mailroom.auth.sign_in_client`` names them; None: not recognised.
    device: Mapped[str | None] = mapped_column(String(64))
    # As much of it as the instance's ``session_ip`` let be recorded at sign-in.
    ip: Mapped[str | None] = mapped_column(String(45))
