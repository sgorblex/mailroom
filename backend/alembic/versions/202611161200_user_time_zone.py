"""Each user may keep a time zone (``user.time_zone``, an IANA name); until set, UTC is used.

Revision ID: 202611161200
Revises: 202611141200
Create Date: 2026-11-16 12:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611161200"
down_revision: str | Sequence[str] | None = "202611141200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user", sa.Column("time_zone", sa.String(64), nullable=True), schema="app")


def downgrade() -> None:
    op.drop_column("user", "time_zone", schema="app")
