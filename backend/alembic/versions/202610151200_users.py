"""Add users and their sessions.

Revision ID: 202610151200
Revises: 202610141900
Create Date: 2026-10-15 12:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202610151200"
down_revision: str | Sequence[str] | None = "202610141900"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint("username", name=op.f("uq_user_username")),
        schema="app",
    )
    op.create_table(
        "user_session",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_user_session_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_session")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_session_token_hash")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_user_session_expires_at"),
        "user_session",
        ["expires_at"],
        unique=False,
        schema="app",
    )
    op.create_index(
        op.f("ix_app_user_session_user_id"),
        "user_session",
        ["user_id"],
        unique=False,
        schema="app",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_app_user_session_user_id"), table_name="user_session", schema="app")
    op.drop_index(op.f("ix_app_user_session_expires_at"), table_name="user_session", schema="app")
    op.drop_table("user_session", schema="app")
    op.drop_table("user", schema="app")
