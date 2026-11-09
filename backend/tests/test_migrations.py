from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from mailroom import db_migrations
from mailroom.db_migrations import applied_revisions, expected_heads, wait_for_head
from mailroom.dev.check_migration_roundtrip import scratch_database_name
from mailroom.models.base import ALEMBIC_VERSION_TABLE, APP_SCHEMA
from mailroom_core.store import CORE_MIGRATIONS, CORE_SCHEMA

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


def test_the_models_match_the_migrations(migrated_engine: Engine) -> None:
    from alembic.autogenerate import compare_metadata

    import mailroom.models  # noqa: F401 -- every model on Base.metadata
    from mailroom.models.base import Base

    def only_app(name: str | None, type_: str, _parent: object) -> bool:
        return name == APP_SCHEMA if type_ == "schema" else True

    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": only_app,
                "compare_type": True,
                "version_table": ALEMBIC_VERSION_TABLE,
                "version_table_schema": APP_SCHEMA,
            },
        )
        assert compare_metadata(context, Base.metadata) == []


@pytest.mark.parametrize(
    ("url", "scratch"),
    [
        ("postgresql://u:p@127.0.0.1:5434/mailroom_test", "mailroom_test_roundtrip_check"),
        (
            "postgresql://u:p@127.0.0.1:5434/mailroom_fix_web_sso_test",
            "mailroom_fix_web_sso_test_roundtrip_check",
        ),
        (f"postgresql://u:p@h/{'x' * 70}", "x" * 47 + "_roundtrip_check"),
    ],
)
def test_each_test_database_gets_its_own_roundtrip_scratch(url: str, scratch: str) -> None:
    assert scratch_database_name(url) == scratch
    assert len(scratch_database_name(url)) <= 63
