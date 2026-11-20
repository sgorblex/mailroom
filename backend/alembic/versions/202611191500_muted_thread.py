"""Muted threads: ``app.muted_thread``, and ``rule_set.kind`` (``user`` or ``mute``), with one
``mute`` rule set per account at most.

Revision ID: 202611191500
Revises: 202611191400
Create Date: 2026-11-19 15:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611191500"
down_revision: str | Sequence[str] | None = "202611191400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rule_set",
        sa.Column("kind", sa.Text(), server_default=sa.text("'user'"), nullable=False),
        schema="app",
    )
    op.create_check_constraint(
        op.f("ck_rule_set_kind"), "rule_set", "kind IN ('user', 'mute')", schema="app"
    )
    op.create_index(
        "uq_rule_set_mute",
        "rule_set",
        ["user_id", "account_id"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("kind = 'mute'"),
    )
    op.create_table(
        "muted_thread",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("message_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gm_thrid", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_muted_thread_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_muted_thread")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_muted_thread_user_id"), "muted_thread", ["user_id"], unique=False, schema="app"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_app_muted_thread_user_id"), table_name="muted_thread", schema="app")
    op.drop_table("muted_thread", schema="app")
    # The muting rule sets go with the threads they muted.
    op.execute("DELETE FROM app.rule_set WHERE kind = 'mute'")
    op.drop_index("uq_rule_set_mute", table_name="rule_set", schema="app")
    op.drop_constraint(op.f("ck_rule_set_kind"), "rule_set", schema="app", type_="check")
    op.drop_column("rule_set", "kind", schema="app")
