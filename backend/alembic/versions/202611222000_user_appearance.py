"""Each user's look of the web, the same on every device: ``user.appearance``, a JSON object of
only the choices they made (``mailroom.auth.appearance`` fills the rest).

Revision ID: 202611222000
Revises: 202611221700
Create Date: 2026-11-22 20:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611222000"
down_revision: str | Sequence[str] | None = "202611221700"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("appearance", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("user", "appearance", schema="app")
