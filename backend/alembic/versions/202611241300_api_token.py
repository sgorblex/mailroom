"""Personal API tokens, and the token an agent's records and held sends came through.

Revision ID: 202611241300
Revises: 202611241200
Create Date: 2026-11-24 13:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611241300"
down_revision: str | Sequence[str] | None = "202611241200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIA_TABLES = ("scheduled_send", "snooze")


def _check_via(values: str) -> None:
    for table in _VIA_TABLES:
        name = op.f(f"ck_{table}_via")
        op.drop_constraint(name, table, schema="app", type_="check")
        op.create_check_constraint(name, table, f"via IN ({values})", schema="app")


def upgrade() -> None:
    op.create_table(
        "api_token",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("prefix", sa.String(length=8), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("tool_groups", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("account_ids", postgresql.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("send_ceiling", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "send_ceiling IN ('off', 'drafts', 'approval', 'direct')",
            name=op.f("ck_api_token_send_ceiling"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_api_token_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_token")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_api_token_token_hash")),
        schema="app",
    )
    op.create_index(op.f("ix_app_api_token_user_id"), "api_token", ["user_id"], schema="app")
    for table in ("audit_record", "scheduled_send"):
        op.add_column(table, sa.Column("token_id", sa.BigInteger(), nullable=True), schema="app")
    _check_via("'web', 'mcp', 'api'")


def downgrade() -> None:
    # Without its token a held send would go out with no limits: it is dropped, its job finds
    # nothing, and the draft stays.
    op.execute("DELETE FROM app.scheduled_send WHERE token_id IS NOT NULL OR via = 'api'")
    op.execute("UPDATE app.snooze SET via = 'mcp' WHERE via = 'api'")
    _check_via("'web', 'mcp'")
    for table in ("audit_record", "scheduled_send"):
        op.drop_column(table, "token_id", schema="app")
    op.drop_table("api_token", schema="app")
