"""Give each user's accounts distinct names: in the newest revision of every user's settings, an
account whose name an earlier account already has loses it (it then shows its login). Names are
compared trimmed, NFKC-normalised and case-folded, as the host compares them.

The downgrade does nothing: the names taken away are not kept, and unique names are valid at
every earlier revision.

Revision ID: core_0005
Revises: core_0004
"""

import unicodedata
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "core_0005"
down_revision: str | None = "core_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_settings = sa.table(
    "settings",
    sa.column("user_id", sa.Text),
    sa.column("revision", sa.Integer),
    sa.column("doc", JSONB),
    schema="core",
)


def upgrade() -> None:
    connection = op.get_bind()
    newest = connection.execute(
        sa.text(
            "select distinct on (user_id) user_id, revision, doc from core.settings "
            "order by user_id, revision desc"
        )
    )
    for row in list(newest):
        doc = row.doc
        if not isinstance(doc, dict) or not isinstance(accounts := doc.get("account"), list):
            continue
        taken: set[str] = set()
        renamed: list[object] = []
        for account in accounts:
            if not isinstance(account, dict) or not isinstance(name := account.get("name"), str):
                renamed.append(account)
            elif (key := _name_key(name)) in taken:
                renamed.append({**account, "name": None})
            else:
                taken.add(key)
                renamed.append(account)
        if renamed != accounts:
            connection.execute(
                sa.update(_settings)
                .where(_settings.c.user_id == row.user_id, _settings.c.revision == row.revision)
                .values(doc={**doc, "account": renamed})
            )


def downgrade() -> None:
    """Nothing to undo."""


def _name_key(name: str) -> str:
    """What two account names are compared by, as the host compares them."""
    return unicodedata.normalize("NFKC", name).strip().casefold()
