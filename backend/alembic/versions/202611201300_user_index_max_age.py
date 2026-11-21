"""Each user may limit the mail index to recent mail: ``user.index_max_age_days``, null for no
limit of their own (``MAILROOM_INDEX_MAX_AGE_DAYS`` caps it for everyone).

Revision ID: 202611201300
Revises: 202611201200
Create Date: 2026-11-20 13:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611201300"
down_revision: str | Sequence[str] | None = "202611201200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user", sa.Column("index_max_age_days", sa.Integer(), nullable=True), schema="app"
    )


def downgrade() -> None:
    op.drop_column("user", "index_max_age_days", schema="app")
