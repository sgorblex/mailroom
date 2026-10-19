"""Schema `core`: the settings with their history, sealed account secrets, the send log.

The tables of `src/store/db.ts` that mailroom-core owns. `settings` and `settings_history` are one
table: the current settings are the highest revision.

Revision ID: core_0001
Revises:
"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "core_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("revision", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("doc", postgresql.JSONB(), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("revision", name="pk_settings"),
        schema="core",
    )
    op.create_table(
        "account_secrets",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "purpose", name="pk_account_secrets"),
        schema="core",
    )
    op.create_table(
        "sends",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sends"),
        schema="core",
    )
    op.create_index("ix_sends_at", "sends", ["at"], schema="core")


def downgrade() -> None:
    op.drop_table("sends", schema="core")
    op.drop_table("account_secrets", schema="core")
    op.drop_table("settings", schema="core")
