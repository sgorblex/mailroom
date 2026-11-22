"""A held send keeps what the Scheduled list shows of its draft (``subject`` and
``recipients``), and an agent's the connected app and grant that scheduled it (``client_id``,
``grant_id``), which its send is audited as and checked against.

Revision ID: 202611221500
Revises: 202611221400
Create Date: 2026-11-22 15:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611221500"
down_revision: str | Sequence[str] | None = "202611221400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scheduled_send",
        sa.Column("subject", sa.Text(), server_default=sa.text("''"), nullable=False),
        schema="app",
    )
    op.add_column(
        "scheduled_send",
        sa.Column(
            "recipients",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        schema="app",
    )
    op.add_column("scheduled_send", sa.Column("client_id", sa.Text(), nullable=True), schema="app")
    op.add_column("scheduled_send", sa.Column("grant_id", sa.Text(), nullable=True), schema="app")


def downgrade() -> None:
    op.drop_column("scheduled_send", "grant_id", schema="app")
    op.drop_column("scheduled_send", "client_id", schema="app")
    op.drop_column("scheduled_send", "recipients", schema="app")
    op.drop_column("scheduled_send", "subject", schema="app")
