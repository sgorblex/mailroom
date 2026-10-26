from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User

Intent = Literal["login", "link"]
"""What an SSO flow is for: signing in, or linking a sign-in to the signed-in user."""


class ExternalIdentity(Base):
    """A linked sign-in: an OIDC provider's ``(issuer, subject)`` that signs in as one user."""

    __tablename__ = "external_identity"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), index=True)
    issuer: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SsoLoginState(Base):
    """An SSO sign-in or link under way, from its start to the provider's callback (single use).
    Only the SHA-256 of ``state`` and of the browser binding cookie are kept."""

    __tablename__ = "sso_login_state"
    __table_args__ = (
        CheckConstraint(
            f"intent IN ({', '.join(repr(intent) for intent in get_args(Intent))})", name="intent"
        ),
    )

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(Text)
    code_verifier: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(Text)
    link_user_id: Mapped[int | None] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"))
    return_to: Mapped[str] = mapped_column(Text)
    browser_binding_hash: Mapped[str] = mapped_column(String(64))
    client_key: Mapped[str] = mapped_column(Text, index=True)
    """Who started it (``mailroom.auth.throttle.client_key``), for the per-client cap."""
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
