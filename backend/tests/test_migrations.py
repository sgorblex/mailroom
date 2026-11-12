from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    from sqlalchemy.orm import Session

    from mailroom.models import User


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


def _migration(revision: str) -> Any:
    from alembic.config import Config

    from mailroom.db_migrations import alembic_ini_path

    script = ScriptDirectory.from_config(Config(str(alembic_ini_path())))
    found = script.get_revision(revision)
    assert found is not None
    return found.module


AUTH = "The mail server rejected the login for this account."
UNREACHABLE = "The mail server for this account could not be reached."
UNVERIFIED = f"{UNREACHABLE} Its TLS certificate did not verify."
TIMEOUT = "The mail server did not answer in time."
MISSING = "That folder does not exist on this account."
SERVER = "The mail server reported an error."


@pytest.mark.parametrize(
    ("stored", "upgraded", "downgraded"),
    [
        (
            "login_failed: [AUTHENTICATIONFAILED] no",
            f"auth_failed: {AUTH}",
            f"login_failed: {AUTH}",
        ),
        (
            "tls_failed: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
            f"unreachable: {UNVERIFIED}",
            f"tls_failed: {UNVERIFIED}",
        ),
        ("tls_failed: [SSL] handshake failure", f"unreachable: {UNREACHABLE}", None),
        ("timeout: timed out", f"timeout: {TIMEOUT}", None),
        ("unreachable: [Errno 111] Connection refused", f"unreachable: {UNREACHABLE}", None),
        ("imap_error: [AUTHENTICATIONFAILED] no", f"auth_failed: {AUTH}", f"login_failed: {AUTH}"),
        (
            "imap_error: select failed: [NONEXISTENT] x",
            f"mailbox_missing: {MISSING}",
            f"imap_error: {MISSING}",
        ),
        ("imap_error: socket error: EOF", f"unreachable: {UNREACHABLE}", None),
        ("imap_error: command: NOOP => EOF", f"unreachable: {UNREACHABLE}", None),
        ("imap_error: BAD odd", f"server_error: {SERVER}", f"imap_error: {SERVER}"),
        (
            "password_missing: No stored password opens",
            "password_missing: No stored password opens",
            None,
        ),
        ("dry_run_timeout: too long", "dry_run_timeout: too long", None),
    ],
)
def test_recorded_mailbox_failures_take_core_s_codes(
    migrated_engine: Engine, stored: str, upgraded: str, downgraded: str | None
) -> None:
    """``downgraded`` None: the upgraded text stays as it is."""
    migration = _migration("202611121200")
    value = "CAST(:x AS text)"
    with migrated_engine.connect() as connection:
        up = connection.execute(text(f"SELECT {migration.upgraded(value)}"), {"x": stored})
        assert up.scalar_one() == upgraded
        down = connection.execute(text(f"SELECT {migration.downgraded(value)}"), {"x": upgraded})
        assert down.scalar_one() == (upgraded if downgraded is None else downgraded)


def test_a_recorded_run_and_its_report_take_core_s_codes(db_session: Session, user: User) -> None:
    from alembic.operations import Operations

    from mailroom.models import FilterRun, RuleSet

    rule_set = RuleSet(
        user_id=user.id, account_id="work", folder=["INBOX"], name="R", trigger="manual"
    )
    db_session.add(rule_set)
    db_session.flush()
    error = "login_failed: [AUTHENTICATIONFAILED] no"
    run = FilterRun(
        rule_set_id=rule_set.id,
        trigger="manual",
        dry_run=False,
        error=error,
        result={"filters": [], "error": error},
    )
    plain = FilterRun(
        rule_set_id=rule_set.id, trigger="manual", dry_run=True, result={"filters": []}
    )
    db_session.add_all([run, plain])
    db_session.flush()

    migration = _migration("202611121200")
    with Operations.context(MigrationContext.configure(db_session.connection())):
        migration.upgrade()
    db_session.expire_all()
    assert (run.error, run.result) == (
        f"auth_failed: {AUTH}",
        {"filters": [], "error": f"auth_failed: {AUTH}"},
    )
    assert (plain.error, plain.result) == (None, {"filters": []})

    with Operations.context(MigrationContext.configure(db_session.connection())):
        migration.downgrade()
    db_session.expire_all()
    assert (run.error, run.result["error"]) == (f"login_failed: {AUTH}", f"login_failed: {AUTH}")
