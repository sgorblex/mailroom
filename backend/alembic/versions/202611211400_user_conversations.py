"""Each user picks whether the web's message lists group messages by conversation:
``user.conversations``, true (the default) for grouped.

Revision ID: 202611211400
Revises: 202611211200
Create Date: 2026-11-21 14:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611211400"
down_revision: str | Sequence[str] | None = "202611211200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("conversations", sa.Boolean(), server_default=sa.true(), nullable=False),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("user", "conversations", schema="app")
