"""Add each filter's daily match counts.

Revision ID: 202611191400
Revises: 202611191300
Create Date: 2026-11-19 14:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611191400"
down_revision: str | Sequence[str] | None = "202611191300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "filter_match_day",
        sa.Column("filter_id", sa.BigInteger(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["filter_id"],
            ["app.filter.id"],
            name=op.f("fk_filter_match_day_filter_id_filter"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("filter_id", "day", name=op.f("pk_filter_match_day")),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("filter_match_day", schema="app")
