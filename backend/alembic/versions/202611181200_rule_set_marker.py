"""A rule set's "only marked" picks its mark: ``rule_set.marker`` (``none``, ``star`` or
``to_sort``) replaces ``starred_only``, true becoming ``star`` and false ``none``. The downgrade
gives ``starred_only`` back, ``to_sort`` becoming true and disabled: such a rule set then waits
for the star, and is not run on starred mail its owner never chose until they enable it again.

Revision ID: 202611181200
Revises: 202611171200
Create Date: 2026-11-18 12:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611181200"
down_revision: str | Sequence[str] | None = "202611171200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rule_set",
        sa.Column("marker", sa.Text(), server_default=sa.text("'none'"), nullable=False),
        schema="app",
    )
    op.execute("UPDATE app.rule_set SET marker = 'star' WHERE starred_only")
    op.create_check_constraint(
        op.f("ck_rule_set_marker"),
        "rule_set",
        "marker IN ('none', 'star', 'to_sort')",
        schema="app",
    )
    op.drop_column("rule_set", "starred_only", schema="app")


def downgrade() -> None:
    op.add_column(
        "rule_set",
        sa.Column("starred_only", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="app",
    )
    op.execute(
        "UPDATE app.rule_set SET starred_only = marker <> 'none', "
        "enabled = enabled AND marker <> 'to_sort'"
    )
    op.drop_constraint(op.f("ck_rule_set_marker"), "rule_set", schema="app", type_="check")
    op.drop_column("rule_set", "marker", schema="app")
