from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User


class InstanceSetting(Base):
    """One setting an admin has changed for the whole instance; the ones never changed have no row
    (``mailroom.config.instance_settings``)."""

    __tablename__ = "instance_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # None once that admin is removed; the setting stays.
    updated_by: Mapped[int | None] = mapped_column(ForeignKey(User.id, ondelete="SET NULL"))
