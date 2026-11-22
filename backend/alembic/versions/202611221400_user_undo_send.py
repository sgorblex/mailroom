"""Each user picks how long Send waits for an Undo: ``user.undo_send_seconds``, 0 (off) to 30,
10 by default.

Revision ID: 202611221400
Revises: 202611221300
Create Date: 2026-11-22 14:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611221400"
down_revision: str | Sequence[str] | None = "202611221300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("undo_send_seconds", sa.Integer(), server_default=sa.text("10"), nullable=False),
        schema="app",
    )
    op.create_check_constraint(
        op.f("ck_user_undo_send_seconds"),
        "user",
        "undo_send_seconds BETWEEN 0 AND 30",
        schema="app",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_user_undo_send_seconds"), "user", schema="app", type_="check")
    op.drop_column("user", "undo_send_seconds", schema="app")
