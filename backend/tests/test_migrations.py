from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from mailroom.db_migrations import applied_revisions, expected_heads
from mailroom.models.base import ALEMBIC_VERSION_TABLE, APP_SCHEMA

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def test_database_is_at_head(migrated_engine: Engine) -> None:
    assert applied_revisions(migrated_engine) == expected_heads()


def test_version_table_lives_in_app_schema(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        schemas = set(
            connection.execute(
                text("SELECT table_schema FROM information_schema.tables WHERE table_name = :name"),
                {"name": ALEMBIC_VERSION_TABLE},
            ).scalars(),
        )
    assert schemas == {APP_SCHEMA}
