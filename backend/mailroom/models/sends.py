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
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User


class ScheduledSend(Base):
    """A draft held for the worker to send at ``send_at`` (``mailroom.mail.service.scheduled``),
    with its procrastinate job ``send_draft``, queued in the same transaction. The row goes once
    the gate has a send for its key; one that never got that far stays ``failed`` with its code.
    The account lives in mailroom-core's schema ``core``, keyed by its id there."""

    __tablename__ = "scheduled_send"
    __table_args__ = (
        UniqueConstraint("user_id", "send_key"),
        CheckConstraint("state IN ('held', 'sending', 'failed')", name="state"),
        CheckConstraint("via IN ('web', 'mcp')", name="via"),
        Index("ix_scheduled_send_user_id", "user_id", "send_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(BigInteger)
    ref: Mapped[str] = mapped_column(Text)
    """The draft's ref when it was scheduled: a later version of it is another message."""
    send_key: Mapped[str] = mapped_column(Text)
    """The draft's send key (``compose._send_key``), which the gate claims."""
    via: Mapped[str] = mapped_column(Text)
    """Who scheduled it, as ``Origin.via``: the gate's ``by`` is its ``actor``."""
    client_id: Mapped[str | None] = mapped_column(Text)
    grant_id: Mapped[str | None] = mapped_column(Text)
    """An agent's connected app and grant, as ``Origin`` has them: its send is audited as that
    app, and fails once the grant is revoked or no longer approves ``mail_send``."""
    subject: Mapped[str] = mapped_column(Text, server_default="")
    recipients: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    """The draft's subject and addresses (To, Cc, Bcc) when it was scheduled, for the Scheduled
    list."""
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(Text, default="held")
    """``held`` until its job starts, ``sending`` from then, ``failed`` when it ended with
    ``code`` before or in its delivery."""
    code: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
