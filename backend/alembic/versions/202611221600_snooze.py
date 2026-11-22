"""Snoozed messages: ``app.snooze``.

Revision ID: 202611221600
Revises: 202611221500
Create Date: 2026-11-22 16:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611221600"
down_revision: str | Sequence[str] | None = "202611221500"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "snooze",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("via", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("folder", sa.Text(), nullable=False),
        sa.Column("snoozed", sa.Text(), nullable=False),
        sa.Column("ref", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("sender", sa.Text(), nullable=True),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("via IN ('web', 'mcp')", name=op.f("ck_snooze_via")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_snooze_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_snooze")),
        sa.UniqueConstraint("user_id", "account_id", "message_id", name=op.f("uq_snooze_user_id")),
        schema="app",
    )
    op.create_index(
        op.f("ix_snooze_user_id"), "snooze", ["user_id", "until"], unique=False, schema="app"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_snooze_user_id"), table_name="snooze", schema="app")
    op.drop_table("snooze", schema="app")
