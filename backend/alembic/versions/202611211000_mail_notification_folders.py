"""The folders whose new mail each user's browser notifies on, per account:
``app.mail_notification_folders`` (no row: INBOX alone).

Revision ID: 202611211000
Revises: 202611201400
Create Date: 2026-11-21 10:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611211000"
down_revision: str | Sequence[str] | None = "202611201400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_notification_folders",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("folders", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_mail_notification_folders_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "account_id", name=op.f("pk_mail_notification_folders")),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("mail_notification_folders", schema="app")
