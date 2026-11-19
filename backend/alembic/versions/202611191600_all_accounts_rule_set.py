"""Rule sets for all accounts: ``rule_set.account_id`` null for one (a user rule set only), and
``filter_run.account_id``, the account each run acted on.

Revision ID: 202611191600
Revises: 202611191500
Create Date: 2026-11-19 16:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611191600"
down_revision: str | Sequence[str] | None = "202611191500"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("rule_set", "account_id", nullable=True, schema="app")
    op.create_check_constraint(
        op.f("ck_rule_set_all_accounts"),
        "rule_set",
        "account_id IS NOT NULL OR kind = 'user'",
        schema="app",
    )
    op.add_column(
        "filter_run", sa.Column("account_id", sa.BigInteger(), nullable=True), schema="app"
    )
    op.execute(
        "UPDATE app.filter_run SET account_id = rule_set.account_id"
        " FROM app.rule_set WHERE rule_set.id = filter_run.rule_set_id"
    )
    op.alter_column("filter_run", "account_id", nullable=False, schema="app")


def downgrade() -> None:
    op.drop_column("filter_run", "account_id", schema="app")
    # Rule sets for all accounts go, with their filters and runs.
    op.execute("DELETE FROM app.rule_set WHERE account_id IS NULL")
    op.drop_constraint(op.f("ck_rule_set_all_accounts"), "rule_set", schema="app", type_="check")
    op.alter_column("rule_set", "account_id", nullable=False, schema="app")
