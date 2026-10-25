"""Add the per-account Run filters switch.

Revision ID: 202610201300
Revises: 202610201200
Create Date: 2026-10-20 13:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202610201300"
down_revision: str | Sequence[str] | None = "202610201200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_filter_settings",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("run_filters", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_account_filter_settings_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "account_id", name=op.f("pk_account_filter_settings")),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("account_filter_settings", schema="app")
