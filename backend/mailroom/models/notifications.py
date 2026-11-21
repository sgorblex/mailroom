from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User


class MailNotificationFolders(Base):
    """The folders of one of the user's accounts whose new mail the web client shows as desktop
    notifications. The account lives in mailroom-core's schema ``core``, keyed by its id there; no
    row means INBOX alone."""

    __tablename__ = "mail_notification_folders"

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    folders: Mapped[list[str]] = mapped_column(ARRAY(Text))
    """As the server names them (``INBOX`` in capitals)."""
