"""Drafts held for the worker to send later: ``app.scheduled_send``.

Revision ID: 202611221300
Revises: 202611221200
Create Date: 2026-11-22 13:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611221300"
down_revision: str | Sequence[str] | None = "202611221200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_send",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("send_key", sa.Text(), nullable=False),
        sa.Column("via", sa.Text(), nullable=False),
        sa.Column("send_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('held', 'sending', 'failed')", name=op.f("ck_scheduled_send_state")
        ),
        sa.CheckConstraint("via IN ('web', 'mcp')", name=op.f("ck_scheduled_send_via")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_scheduled_send_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_send")),
        sa.UniqueConstraint("user_id", "send_key", name=op.f("uq_scheduled_send_user_id")),
        schema="app",
    )
    op.create_index(
        op.f("ix_scheduled_send_user_id"),
        "scheduled_send",
        ["user_id", "send_at"],
        unique=False,
        schema="app",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_scheduled_send_user_id"), table_name="scheduled_send", schema="app")
    op.drop_table("scheduled_send", schema="app")
