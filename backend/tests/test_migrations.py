from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from mailroom_core.store import CORE_MIGRATIONS, CORE_SCHEMA
from sqlalchemy import text

from mailroom import db_migrations
from mailroom.db_migrations import applied_revisions, expected_heads, wait_for_head
from mailroom.models.base import ALEMBIC_VERSION_TABLE, APP_SCHEMA

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def test_database_is_at_head(migrated_engine: Engine) -> None:
    assert applied_revisions(migrated_engine) == expected_heads()


def test_schema_core_is_at_mailroom_cores_head(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"version_table_schema": CORE_SCHEMA})
        applied = set(context.get_current_heads())
    assert applied == set(ScriptDirectory(str(CORE_MIGRATIONS)).get_heads())


def test_each_history_keeps_its_version_table_in_its_schema(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        schemas = set(
            connection.execute(
                text("SELECT table_schema FROM information_schema.tables WHERE table_name = :name"),
                {"name": ALEMBIC_VERSION_TABLE},
            ).scalars(),
        )
    assert schemas == {APP_SCHEMA, CORE_SCHEMA}


def test_wait_for_head_returns_at_head(migrated_engine: Engine) -> None:
    wait_for_head(migrated_engine)


def test_wait_for_head_gives_up_behind_head(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db_migrations, "expected_heads", lambda: {"ahead"})
    monkeypatch.setenv("MAILROOM_STARTUP_MIGRATION_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("MAILROOM_STARTUP_MIGRATION_RETRY_SECONDS", "0")
    with pytest.raises(RuntimeError, match="not at head"):
        wait_for_head(migrated_engine)
