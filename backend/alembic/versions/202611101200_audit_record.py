"""Add the audit trail of mail writes.

Revision ID: 202611101200
Revises: 202610251400
Create Date: 2026-11-10 12:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611101200"
down_revision: str | Sequence[str] | None = "202610251400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_record",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=True),
        sa.Column("grant_id", sa.Text(), nullable=True),
        sa.Column("rule_set_id", sa.BigInteger(), nullable=True),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_audit_record_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_record")),
        schema="app",
    )
    op.create_index(
        "ix_audit_record_user_id_id", "audit_record", ["user_id", "id"], unique=False, schema="app"
    )
    op.create_index(
        op.f("ix_app_audit_record_at"), "audit_record", ["at"], unique=False, schema="app"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_app_audit_record_at"), table_name="audit_record", schema="app")
    op.drop_index("ix_audit_record_user_id_id", table_name="audit_record", schema="app")
    op.drop_table("audit_record", schema="app")
