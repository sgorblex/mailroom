"""Create the app schema.

Revision ID: 202610141900
Revises:
Create Date: 2026-10-14 19:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202610141900"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # alembic/env.py creates it already, because the version table lives in it; this revision
    # records the schema as the first thing the app owns.
    op.execute('CREATE SCHEMA IF NOT EXISTS "app"')


def downgrade() -> None:
    # The schema stays: it holds `app.alembic_version`, which Alembic still writes to after this
    # downgrade runs. An empty `app` schema is the base state.
    pass
