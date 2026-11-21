from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User


class SavedView(Base):
    """A list of the user's mail kept in the sidebar: some folders of any of their accounts, or a
    search in the search syntax, read from the mail index (``mailroom.mail.saved_views``). The
    built-in *All inboxes* is no row."""

    __tablename__ = "saved_view"
    __table_args__ = (
        UniqueConstraint("user_id", "name"),
        CheckConstraint("(sources IS NULL) <> (query IS NULL)", name="sources_or_query"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    sources: Mapped[list[list[Any]] | None] = mapped_column(JSONB(none_as_null=True))
    """``[[account_id, path], ...]``, each path as the server names it (``INBOX`` in capitals);
    None for a search."""
    query: Mapped[str | None] = mapped_column(Text)
    """The search syntax's line; None for folders."""
    position: Mapped[int] = mapped_column(Integer)
    """0, 1, 2… among the user's views: the sidebar's order."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
