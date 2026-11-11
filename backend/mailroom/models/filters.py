from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User

TRIGGERS: Final = ("new_mail", "scheduled", "manual")
"""What starts a rule set's runs; a run records the one that started it (``manual``: Run now or
a dry run)."""

_TRIGGER_CHECK: Final = "trigger IN ('new_mail', 'scheduled', 'manual')"


class RuleSet(Base):
    """Ordered filters for one folder of one of the user's accounts, and what triggers them. The
    account lives in mailroom-core's schema ``core``, keyed by its id there."""

    __tablename__ = "rule_set"
    __table_args__ = (
        CheckConstraint(_TRIGGER_CHECK, name="trigger"),
        # An interval exactly when scheduled, and a positive one.
        CheckConstraint(
            "(trigger = 'scheduled') = (interval_minutes IS NOT NULL)", name="interval"
        ),
        CheckConstraint("interval_minutes > 0", name="interval_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), index=True)
    account_id: Mapped[str] = mapped_column(Text)
    folder: Mapped[list[str]] = mapped_column(ARRAY(Text))
    """Path segments, as the server's delimiter splits them: ``["Archive", "2026"]``."""
    name: Mapped[str] = mapped_column(String(200))
    trigger: Mapped[str] = mapped_column(Text)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    starred_only: Mapped[bool] = mapped_column(Boolean, server_default=false())
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Filter(Base):
    """One filter of a rule set, as its spec (``mailroom_core.filters.spec``)."""

    __tablename__ = "filter"
    __table_args__ = (
        # Deferred, so a reorder can swap positions within one transaction.
        UniqueConstraint(
            "rule_set_id",
            "position",
            name="uq_filter_rule_set_id_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    rule_set_id: Mapped[int] = mapped_column(ForeignKey(RuleSet.id, ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    """0, 1, 2… in the rule set: the order the filters run in."""
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=true())
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FilterRun(Base):
    """One run of a rule set, real or dry. Queued with no ``started_at``; the worker starts and
    finishes it."""

    __tablename__ = "filter_run"
    __table_args__ = (CheckConstraint(_TRIGGER_CHECK, name="trigger"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    rule_set_id: Mapped[int] = mapped_column(ForeignKey(RuleSet.id, ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(Text)
    dry_run: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    """The filter engine's folder report, each filter's entry with its ``filter_id``
    (``mailroom.filters.runs.labelled_report``)."""
    error: Mapped[str | None] = mapped_column(Text)
