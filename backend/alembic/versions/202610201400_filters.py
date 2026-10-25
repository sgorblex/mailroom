"""Add rule sets, filters and their runs.

Revision ID: 202610201400
Revises: 202610201300
Create Date: 2026-10-20 14:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202610201400"
down_revision: str | Sequence[str] | None = "202610201300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rule_set",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("folder", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=True),
        sa.Column("starred_only", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(trigger = 'scheduled') = (interval_minutes IS NOT NULL)",
            name=op.f("ck_rule_set_interval"),
        ),
        sa.CheckConstraint(
            "trigger IN ('new_mail', 'scheduled', 'manual')", name=op.f("ck_rule_set_trigger")
        ),
        sa.CheckConstraint("interval_minutes > 0", name=op.f("ck_rule_set_interval_positive")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app.user.id"], name=op.f("fk_rule_set_user_id_user"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_set")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_rule_set_user_id"), "rule_set", ["user_id"], unique=False, schema="app"
    )
    op.create_table(
        "filter",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("rule_set_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"],
            ["app.rule_set.id"],
            name=op.f("fk_filter_rule_set_id_rule_set"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filter")),
        sa.UniqueConstraint(
            "rule_set_id",
            "position",
            deferrable=True,
            initially="DEFERRED",
            name=op.f("uq_filter_rule_set_id_position"),
        ),
        schema="app",
    )
    op.create_table(
        "filter_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("rule_set_id", sa.BigInteger(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "trigger IN ('new_mail', 'scheduled', 'manual')", name=op.f("ck_filter_run_trigger")
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"],
            ["app.rule_set.id"],
            name=op.f("fk_filter_run_rule_set_id_rule_set"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filter_run")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_filter_run_rule_set_id"),
        "filter_run",
        ["rule_set_id"],
        unique=False,
        schema="app",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_app_filter_run_rule_set_id"), table_name="filter_run", schema="app")
    op.drop_table("filter_run", schema="app")
    op.drop_table("filter", schema="app")
    op.drop_index(op.f("ix_app_rule_set_user_id"), table_name="rule_set", schema="app")
    op.drop_table("rule_set", schema="app")
