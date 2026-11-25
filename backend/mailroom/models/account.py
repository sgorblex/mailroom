from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, ForeignKey, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User


class AccountFilterSettings(Base):
    """Whether the worker runs a user's filters on one of their accounts. The account itself lives
    in mailroom-core's schema ``core``, keyed by its id there; no row means off."""

    __tablename__ = "account_filter_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_filters: Mapped[bool] = mapped_column(Boolean, server_default=false())


class AccountHandled(Base):
    """What one of a user's accounts keeps the Handled mark as (``mailroom.accounts.handled``);
    no row means the star."""

    __tablename__ = "account_handled"

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mark: Mapped[str] = mapped_column(Text)
    """``keyword:<name>`` or ``label:<name>`` (``mailroom_core.mail.parse_handled``)."""
