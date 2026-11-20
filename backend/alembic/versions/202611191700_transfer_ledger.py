"""Filters that move to another account: ``app.transfer_ledger``, the copies such a move made
(the loop guard), and ``rule_set.disabled_reason``, why the app turned a rule set off.

Revision ID: 202611191700
Revises: 202611191600
Create Date: 2026-11-19 17:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611191700"
down_revision: str | Sequence[str] | None = "202611191600"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rule_set", sa.Column("disabled_reason", sa.Text(), nullable=True), schema="app")
    op.create_table(
        "transfer_ledger",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("folder", sa.Text(), nullable=False),
        sa.Column("uid_validity", sa.BigInteger(), nullable=False),
        sa.Column("uid", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_transfer_ledger_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "account_id",
            "folder",
            "uid_validity",
            "uid",
            name=op.f("pk_transfer_ledger"),
        ),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_transfer_ledger_created_at"),
        "transfer_ledger",
        ["created_at"],
        unique=False,
        schema="app",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_app_transfer_ledger_created_at"), table_name="transfer_ledger", schema="app"
    )
    op.drop_table("transfer_ledger", schema="app")
    op.drop_column("rule_set", "disabled_reason", schema="app")
