"""Key schema `core` by user: `user_id` on the settings, the sealed secrets and the send log.

Rows already stored belong to the standalone user, `owner`. Downgrading refuses while another
user has rows, as their revisions and account ids would collide.

Revision ID: core_0002
Revises: core_0001
"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "core_0002"
down_revision: str | None = "core_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STANDALONE_USER = "owner"
_TABLES = ("settings", "account_secrets", "sends")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("user_id", sa.Text(), nullable=False, server_default=_STANDALONE_USER),
            schema="core",
        )
        op.alter_column(table, "user_id", server_default=None, schema="core")
    op.drop_constraint("pk_settings", "settings", type_="primary", schema="core")
    op.create_primary_key("pk_settings", "settings", ["user_id", "revision"], schema="core")
    op.drop_constraint("pk_account_secrets", "account_secrets", type_="primary", schema="core")
    op.create_primary_key(
        "pk_account_secrets",
        "account_secrets",
        ["user_id", "account_id", "purpose"],
        schema="core",
    )
    op.drop_index("ix_sends_at", "sends", schema="core")
    op.create_index("ix_sends_user_id_at", "sends", ["user_id", "at"], schema="core")


def downgrade() -> None:
    others = op.get_bind().execute(
        sa.text(
            " union all ".join(
                f"(select 1 from core.{table} where user_id <> :user limit 1)" for table in _TABLES
            )
        ),
        {"user": _STANDALONE_USER},
    )
    if others.first() is not None:
        raise RuntimeError(f"schema core holds rows of users other than {_STANDALONE_USER!r}")
    op.drop_index("ix_sends_user_id_at", "sends", schema="core")
    op.create_index("ix_sends_at", "sends", ["at"], schema="core")
    op.drop_constraint("pk_account_secrets", "account_secrets", type_="primary", schema="core")
    op.create_primary_key(
        "pk_account_secrets", "account_secrets", ["account_id", "purpose"], schema="core"
    )
    op.drop_constraint("pk_settings", "settings", type_="primary", schema="core")
    op.create_primary_key("pk_settings", "settings", ["revision"], schema="core")
    for table in _TABLES:
        op.drop_column(table, "user_id", schema="core")
