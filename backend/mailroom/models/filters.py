from __future__ import annotations

from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
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

_MARKER_CHECK: Final = "marker IN ('none', 'star', 'to_sort')"


class RuleSet(Base):
    """Ordered filters for one folder of one of the user's accounts, or for INBOX of each of their
    accounts, and what triggers them. The account lives in mailroom-core's schema ``core``, keyed
    by its id there."""

    __tablename__ = "rule_set"
    __table_args__ = (
        CheckConstraint(_TRIGGER_CHECK, name="trigger"),
        CheckConstraint(_MARKER_CHECK, name="marker"),
        # An interval exactly when scheduled, and a positive one.
        CheckConstraint(
            "(trigger = 'scheduled') = (interval_minutes IS NOT NULL)", name="interval"
        ),
        CheckConstraint("interval_minutes > 0", name="interval_positive"),
        CheckConstraint("kind IN ('user', 'mute')", name="kind"),
        CheckConstraint("account_id IS NOT NULL OR kind = 'user'", name="all_accounts"),
        # One muting rule set per account.
        Index(
            "uq_rule_set_mute",
            "user_id",
            "account_id",
            unique=True,
            postgresql_where=text("kind = 'mute'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), index=True)
    account_id: Mapped[int | None] = mapped_column(BigInteger)
    """None: every account of the user's whose Run filters is on, each run on one of them
    (``FilterRun.account_id``), on INBOX only."""
    folder: Mapped[list[str]] = mapped_column(ARRAY(Text))
    """Path segments, as the server's delimiter splits them: ``["Archive", "2026"]``."""
    name: Mapped[str] = mapped_column(String(200))
    trigger: Mapped[str] = mapped_column(Text)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    marker: Mapped[str] = mapped_column(Text, server_default="none")
    """The mark the rule set limits itself to, cleared once a message is handled: ``none`` (every
    message), ``star`` (``\\Flagged``) or ``to_sort`` (the ``$MailroomSort`` keyword)."""
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    kind: Mapped[str] = mapped_column(Text, server_default="user")
    """``user``, or ``mute``: the account's hidden one muting threads (``mailroom.filters.mute``),
    left out of every listing and edit."""
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    """Why the app turned it off, ``<code>: <message>``; cleared when the user turns it on."""
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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    """The user's last edit of its name, spec or switch: a reorder or a folder rename leaves it."""


class FilterMatchDay(Base):
    """How many messages a filter acted on in its real runs on one day (UTC), kept 30 days."""

    __tablename__ = "filter_match_day"

    filter_id: Mapped[int] = mapped_column(
        ForeignKey(Filter.id, ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class FilterRun(Base):
    """One run of a rule set, real or dry. Queued with no ``started_at``; the worker starts and
    finishes it."""

    __tablename__ = "filter_run"
    __table_args__ = (CheckConstraint(_TRIGGER_CHECK, name="trigger"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    rule_set_id: Mapped[int] = mapped_column(ForeignKey(RuleSet.id, ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(BigInteger)
    """The account the run acted on: its rule set's, or one of an all-accounts rule set's."""
    trigger: Mapped[str] = mapped_column(Text)
    dry_run: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    """The filter engine's folder report, each filter's entry with its ``filter_id``
    (``mailroom.filters.runs.labelled_report``)."""
    error: Mapped[str | None] = mapped_column(Text)


class MutedThread(Base):
    """A conversation the user muted on one account: its new messages in INBOX arrive marked read,
    by the account's ``mute`` rule set. Named by Message-IDs (seeded from the message muted, each
    message matched since added, ``mailroom.filters.mute.MAX_IDS`` at most) and on Gmail by its
    thread id."""

    __tablename__ = "muted_thread"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(BigInteger)
    message_ids: Mapped[list[str]] = mapped_column(JSONB)
    """Angle brackets included, oldest first."""
    gm_thrid: Mapped[int | None] = mapped_column(BigInteger)
    """Gmail's ``X-GM-THRID``."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TransferLedger(Base):
    """A message a filter's move to another account copied there, by its place in that account:
    the target's runs leave it alone, so no filter moves it on or back (the loop guard). Pruned
    when its folder's UIDVALIDITY changes and after ``mailroom.filters.transfers.LEDGER_DAYS``."""

    __tablename__ = "transfer_ledger"

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    folder: Mapped[str] = mapped_column(Text, primary_key=True)
    """As the server names it (``INBOX`` in capitals)."""
    uid_validity: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uid: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
