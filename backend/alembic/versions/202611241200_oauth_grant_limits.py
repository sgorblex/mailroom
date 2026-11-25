"""Each grant may be limited to some of its user's accounts, and to a send ceiling.

Revision ID: 202611241200
Revises: 202611241100
Create Date: 2026-11-24 12:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611241200"
down_revision: str | Sequence[str] | None = "202611241100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Null, as every grant has it now: no limit.
    op.add_column(
        "oauth_grant",
        sa.Column("account_ids", postgresql.ARRAY(sa.BigInteger()), nullable=True),
        schema="app",
    )
    op.add_column("oauth_grant", sa.Column("send_ceiling", sa.Text(), nullable=True), schema="app")
    op.create_check_constraint(
        op.f("ck_oauth_grant_send_ceiling"),
        "oauth_grant",
        "send_ceiling IN ('off', 'drafts', 'approval', 'direct')",
        schema="app",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_oauth_grant_send_ceiling"), "oauth_grant", schema="app", type_="check"
    )
    op.drop_column("oauth_grant", "send_ceiling", schema="app")
    op.drop_column("oauth_grant", "account_ids", schema="app")
