"""Each user picks what "mark all as read" on a folder with subfolders does by default:
``user.mark_read_subfolders``, true (the default) for the folder and its subfolders, false for the
folder alone.

Revision ID: 202611191200
Revises: 202611181200
Create Date: 2026-11-19 12:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611191200"
down_revision: str | Sequence[str] | None = "202611181200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("mark_read_subfolders", sa.Boolean(), server_default=sa.true(), nullable=False),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("user", "mark_read_subfolders", schema="app")
