"""The folders browsers are viewing, which the worker watches through IDLE:
``app.mail_view_interest``.

Revision ID: 202611201400
Revises: 202611201300
Create Date: 2026-11-20 14:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611201400"
down_revision: str | Sequence[str] | None = "202611201300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_view_interest",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("folder", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_mail_view_interest_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "account_id", "folder", name=op.f("pk_mail_view_interest")
        ),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("mail_view_interest", schema="app")
