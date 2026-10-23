"""Add procrastinate's job queue, in its own schema.

Revision ID: 202610201200
Revises: 202610151200
Create Date: 2026-10-20 12:00:00

"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

    import psycopg

revision: str = "202610201200"
down_revision: str | Sequence[str] | None = "202610151200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# procrastinate's own schema.sql as of the version this revision was written for, frozen: a later
# release ships its changes as migration files, which a later revision applies.
SCHEMA_SQL = Path(__file__).parent / "sql" / "procrastinate_3.10.0.sql"


def upgrade() -> None:
    op.execute('CREATE SCHEMA "procrastinate"')
    # Through psycopg itself: the script holds many statements and `%` signs, which SQLAlchemy's
    # `text()` and parameter handling would both misread.
    connection = cast("psycopg.Connection[object]", op.get_bind().connection.driver_connection)
    connection.execute("SET LOCAL search_path TO procrastinate")
    connection.execute(SCHEMA_SQL.read_text())  # type: ignore[call-overload]
    connection.execute("SET LOCAL search_path TO DEFAULT")


def downgrade() -> None:
    op.execute('DROP SCHEMA "procrastinate" CASCADE')
