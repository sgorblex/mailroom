"""Add the senders whose remote content each user shows without asking.

Revision ID: 202611191300
Revises: 202611191200
Create Date: 2026-11-19 13:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611191300"
down_revision: str | Sequence[str] | None = "202611191200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "remote_content_sender",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_remote_content_sender_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "address", name=op.f("pk_remote_content_sender")),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("remote_content_sender", schema="app")
