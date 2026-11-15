"""Each grant records the MCP tool groups its user approved (``oauth_grant.tool_groups``).

Grants approved before this are recorded as approving the groups that were ``stable`` then: a
``preview`` group (``mail_transfer``) waits for the user's approval under Connected apps. The list
is written here, not imported, so the migration keeps meaning what it meant when it ran.

Revision ID: 202611141200
Revises: 202611131200
Create Date: 2026-11-14 12:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611141200"
down_revision: str | Sequence[str] | None = "202611131200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STABLE_GROUPS = (
    "filters_read",
    "filters_write",
    "filters_run",
    "mail_read",
    "mail_organize",
    "mail_folders",
    "mail_draft",
    "mail_send",
    "audit_read",
)


def upgrade() -> None:
    op.add_column(
        "oauth_grant",
        sa.Column("tool_groups", postgresql.ARRAY(sa.Text()), nullable=True),
        schema="app",
    )
    op.execute(
        sa.text("UPDATE app.oauth_grant SET tool_groups = :groups").bindparams(
            sa.bindparam("groups", list(STABLE_GROUPS), type_=postgresql.ARRAY(sa.Text()))
        )
    )
    op.alter_column("oauth_grant", "tool_groups", nullable=False, schema="app")


def downgrade() -> None:
    op.drop_column("oauth_grant", "tool_groups", schema="app")
