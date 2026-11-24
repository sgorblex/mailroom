"""All inboxes with INBOX's subfolders: the user's choice (``user.inbox_subfolders``, off by
default), and which index folders are INBOX's subfolders (``mail_index_folder.inbox_subfolder``,
set on the account's next index turn from its folder listing).

Revision ID: 202611241400
Revises: 202611241300
Create Date: 2026-11-24 14:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611241400"
down_revision: str | Sequence[str] | None = "202611241300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (("user", "inbox_subfolders"), ("mail_index_folder", "inbox_subfolder"))


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.add_column(
            table,
            sa.Column(column, sa.Boolean(), server_default=sa.false(), nullable=False),
            schema="app",
        )


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.drop_column(table, column, schema="app")
