"""The authorization server's state, all in the database (schema ``app``): it survives restarts
and is shared by every API worker. Secrets (request ids, codes, tokens) are stored as their
SHA-256 only."""

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
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import APP_SCHEMA, Base

#: The one table outside this package the server refers to: the users grants belong to.
USER_ID_COLUMN = f"{APP_SCHEMA}.user.id"


class OAuthClient(Base):
    """A client: registered dynamically (``dcr``), or a client ID metadata document (``cimd``)
    whose URL is the ``client_id``, cached here between fetches."""

    __tablename__ = "oauth_client"
    __table_args__ = (CheckConstraint("kind IN ('dcr', 'cimd')", name="kind"),)

    client_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    client_name: Mapped[str | None] = mapped_column(String(100))
    redirect_uris: Mapped[list[str]] = mapped_column(ARRAY(Text))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    """The client information the SDK's handlers work with, as JSON."""
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When a ``cimd`` client's document was last fetched."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthAuthorizationRequest(Base):
    """An authorization request waiting on the consent screen (``/connect?request=<id>``)."""

    __tablename__ = "oauth_authorization_request"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey(OAuthClient.client_id, ondelete="CASCADE"), index=True
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """The SDK's ``AuthorizationParams`` as JSON, plus ``claimed_by``: the user who opened it."""
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OAuthGrant(Base):
    """One approval of a client by a user: a *connected app*. Its tokens go with it."""

    __tablename__ = "oauth_grant"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(USER_ID_COLUMN, ondelete="CASCADE"), index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey(OAuthClient.client_id, ondelete="CASCADE"), index=True
    )
    resource: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthAuthorizationCode(Base):
    """An issued authorization code: single use, bound to what the token request must repeat."""

    __tablename__ = "oauth_authorization_code"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey(OAuthClient.client_id, ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(USER_ID_COLUMN, ondelete="CASCADE"), index=True
    )
    grant_id: Mapped[int] = mapped_column(ForeignKey(OAuthGrant.id, ondelete="CASCADE"), index=True)
    redirect_uri: Mapped[str] = mapped_column(Text)
    redirect_uri_explicit: Mapped[bool] = mapped_column(Boolean)
    code_challenge: Mapped[str] = mapped_column(Text)
    resource: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set when the code is exchanged; the row stays until it expires, so a replay is recognised."""


class OAuthToken(Base):
    """An access or refresh token of a grant. A spent refresh token stays (``used_at`` set) until
    it expires, so presenting it again is recognised as reuse."""

    __tablename__ = "oauth_token"
    __table_args__ = (CheckConstraint("kind IN ('access', 'refresh')", name="kind"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    grant_id: Mapped[int] = mapped_column(ForeignKey(OAuthGrant.id, ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
