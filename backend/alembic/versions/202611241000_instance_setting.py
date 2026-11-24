"""Add the settings admins change for the whole instance.

Revision ID: 202611241000
Revises: 202611231000
Create Date: 2026-11-24 10:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611241000"
down_revision: str | Sequence[str] | None = "202611231000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instance_setting",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["app.user.id"],
            name=op.f("fk_instance_setting_updated_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_instance_setting")),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("instance_setting", schema="app")
