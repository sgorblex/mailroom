"""An index row keeps its Date header's UTC offset (``app.mail_index_message.date_offset``, in
minutes), so a read from the index gives the date in the offset a live read gives. Rows stored
before stay null, read in UTC, until their folder is indexed again (switched off and on, or
rebuilt).

Revision ID: 202611211600
Revises: 202611211500
Create Date: 2026-11-21 16:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611211600"
down_revision: str | Sequence[str] | None = "202611211500"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mail_index_message",
        sa.Column("date_offset", sa.SmallInteger(), nullable=True),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("mail_index_message", "date_offset", schema="app")
