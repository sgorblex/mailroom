"""Vacation auto-replies: ``app.vacation`` and ``app.vacation_replied``.

Revision ID: 202611221700
Revises: 202611221600
Create Date: 2026-11-22 17:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611221700"
down_revision: str | Sequence[str] | None = "202611221600"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vacation",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("identity", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_vacation_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "account_id", name=op.f("pk_vacation")),
        schema="app",
    )
    op.create_table(
        "vacation_replied",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_vacation_replied_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "account_id", "sender", name=op.f("pk_vacation_replied")
        ),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("vacation_replied", schema="app")
    op.drop_table("vacation", schema="app")
