"""Turn the send log into a record of each send: `key`, `state`, `account` and `result`.

Rows already stored only count towards the hourly limit, so they keep nulls. The partial unique
index lets one key win once per user while keyless sends stay unlimited in number.

Revision ID: core_0003
Revises: core_0002
"""

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "core_0003"
down_revision: str | None = "core_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sends", sa.Column("key", sa.Text(), nullable=True), schema="core")
    op.add_column("sends", sa.Column("state", sa.Text(), nullable=True), schema="core")
    op.add_column("sends", sa.Column("account", sa.Text(), nullable=True), schema="core")
    op.add_column("sends", sa.Column("result", JSONB(), nullable=True), schema="core")
    op.create_index(
        "uq_sends_user_id_key",
        "sends",
        ["user_id", "key"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("key is not null"),
    )


def downgrade() -> None:
    op.drop_index("uq_sends_user_id_key", "sends", schema="core")
    op.drop_column("sends", "result", schema="core")
    op.drop_column("sends", "account", schema="core")
    op.drop_column("sends", "state", schema="core")
    op.drop_column("sends", "key", schema="core")
