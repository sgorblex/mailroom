from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User


class AuditRecord(Base):
    """One message a write acted on, or tried to: what mailroom-core's ``AuditItem`` says of it,
    with who acted and through what (``mailroom.audit``). Never a subject or a body.

    No foreign key but the user's: a record outlives the account, the connected app and the rule
    set it names, since what happened stays true; it goes with the user."""

    __tablename__ = "audit_record"
    __table_args__ = (Index("ix_audit_record_user_id_id", "user_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor: Mapped[str] = mapped_column(Text)
    """``user`` (the web), ``agent`` (MCP) or ``filter`` (a rule set's run)."""
    client_id: Mapped[str | None] = mapped_column(Text)
    grant_id: Mapped[str | None] = mapped_column(Text)
    rule_set_id: Mapped[int | None] = mapped_column(BigInteger)
    run_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(Text)
    account_id: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """The item's fields (``AuditItem.record``) but its account and result."""
