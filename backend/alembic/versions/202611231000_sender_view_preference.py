"""Add the senders whose mail each user reads as text rather than HTML.

Revision ID: 202611231000
Revises: 202611222000
Create Date: 2026-11-23 10:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611231000"
down_revision: str | Sequence[str] | None = "202611222000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sender_view_preference",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("view", sa.Text(), server_default="text", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("view = 'text'", name=op.f("ck_sender_view_preference_view")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_sender_view_preference_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "address", name=op.f("pk_sender_view_preference")),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("sender_view_preference", schema="app")
