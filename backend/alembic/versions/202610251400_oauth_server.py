"""Add the built-in OAuth authorization server's clients, requests, codes, grants and tokens.

Revision ID: 202610251400
Revises: 202610251200
Create Date: 2026-10-25 14:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202610251400"
down_revision: str | Sequence[str] | None = "202610251200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _index(table: str, column: str) -> None:
    op.create_index(op.f(f"ix_app_{table}_{column}"), table, [column], unique=False, schema="app")


def upgrade() -> None:
    op.create_table(
        "oauth_client",
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("client_name", sa.String(length=100), nullable=True),
        sa.Column("redirect_uris", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('dcr', 'cimd')", name=op.f("ck_oauth_client_kind")),
        sa.PrimaryKeyConstraint("client_id", name=op.f("pk_oauth_client")),
        schema="app",
    )
    op.create_table(
        "oauth_authorization_request",
        sa.Column("id_hash", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["app.oauth_client.client_id"],
            name=op.f("fk_oauth_authorization_request_client_id_oauth_client"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id_hash", name=op.f("pk_oauth_authorization_request")),
        schema="app",
    )
    _index("oauth_authorization_request", "client_id")
    _index("oauth_authorization_request", "expires_at")
    op.create_table(
        "oauth_grant",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        _created_at(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["app.oauth_client.client_id"],
            name=op.f("fk_oauth_grant_client_id_oauth_client"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_oauth_grant_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_grant")),
        schema="app",
    )
    _index("oauth_grant", "client_id")
    _index("oauth_grant", "user_id")
    op.create_table(
        "oauth_authorization_code",
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("grant_id", sa.BigInteger(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("redirect_uri_explicit", sa.Boolean(), nullable=False),
        sa.Column("code_challenge", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["app.oauth_client.client_id"],
            name=op.f("fk_oauth_authorization_code_client_id_oauth_client"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["app.oauth_grant.id"],
            name=op.f("fk_oauth_authorization_code_grant_id_oauth_grant"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_oauth_authorization_code_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("code_hash", name=op.f("pk_oauth_authorization_code")),
        schema="app",
    )
    _index("oauth_authorization_code", "client_id")
    _index("oauth_authorization_code", "expires_at")
    _index("oauth_authorization_code", "grant_id")
    _index("oauth_authorization_code", "user_id")
    op.create_table(
        "oauth_token",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("grant_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.CheckConstraint("kind IN ('access', 'refresh')", name=op.f("ck_oauth_token_kind")),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["app.oauth_grant.id"],
            name=op.f("fk_oauth_token_grant_id_oauth_grant"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_oauth_token")),
        schema="app",
    )
    _index("oauth_token", "expires_at")
    _index("oauth_token", "grant_id")


def downgrade() -> None:
    op.drop_table("oauth_token", schema="app")
    op.drop_table("oauth_authorization_code", schema="app")
    op.drop_table("oauth_grant", schema="app")
    op.drop_table("oauth_authorization_request", schema="app")
    op.drop_table("oauth_client", schema="app")
