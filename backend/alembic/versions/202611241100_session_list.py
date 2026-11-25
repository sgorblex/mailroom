"""Keep when each session was last seen, from which browser and from which address.

Revision ID: 202611241100
Revises: 202611241000
Create Date: 2026-11-24 11:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611241100"
down_revision: str | Sequence[str] | None = "202611241000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_session",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="app",
    )
    # The sessions there are: all that is known of them is when they began.
    op.execute("UPDATE app.user_session SET last_seen_at = created_at")
    op.add_column(
        "user_session", sa.Column("device", sa.String(length=64), nullable=True), schema="app"
    )
    op.add_column(
        "user_session", sa.Column("ip", sa.String(length=45), nullable=True), schema="app"
    )


def downgrade() -> None:
    op.drop_column("user_session", "ip", schema="app")
    op.drop_column("user_session", "device", schema="app")
    op.drop_column("user_session", "last_seen_at", schema="app")
