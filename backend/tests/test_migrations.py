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

    rule_set = RuleSet(user_id=user.id, account_id=1, folder=["INBOX"], name="R", trigger="manual")
    db_session.add(rule_set)
    db_session.flush()
    error = "login_failed: [AUTHENTICATIONFAILED] no"
    run = FilterRun(
        rule_set_id=rule_set.id,
        account_id=1,
        trigger="manual",
        dry_run=False,
        error=error,
        result={"filters": [], "error": error},
    )
    plain = FilterRun(
        rule_set_id=rule_set.id,
        account_id=1,
        trigger="manual",
        dry_run=True,
        result={"filters": []},
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


def _rows(engine: Engine, query: str, **params: object) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(query), params)]


def _insert(engine: Engine, query: str, **params: object) -> Any:
    with engine.begin() as connection:
        result = connection.execute(text(query), params)
        return result.scalar() if result.returns_rows else None


def test_account_ids_are_numbered_in_every_table_and_given_back(
    throwaway_databases: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``core_0004`` and ``202611171200`` on rows as the slug ids left them: every table, JSON
    field, draft send key and live job lock naming an account takes its number, and the
    downgrades give each its slug back."""
    import json

    from alembic import command
    from alembic.config import Config

    from mailroom.db_migrations import alembic_ini_path
    from mailroom_core.store import downgrade, upgrade

    with throwaway_databases.migrated(lambda _: None) as engine:
        monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
        config = Config(str(alembic_ini_path()))
        config.attributes["quiet"] = True
        upgrade(engine, CORE_MIGRATIONS, "core_0003")
        command.upgrade(config, "202611161200")

        add_user = "insert into app.user (username, password_hash) values (:u, 'x') returning id"
        alice = _insert(engine, add_user, u="alice")
        bob = _insert(engine, add_user, u="bob")
        docs = {
            alice: [
                {"id": "work", "login": "a@x.org", "name": "Office"},
                {"id": "home", "login": "a@y.org"},
            ],
            bob: [{"id": "home", "login": "b@x.org"}],
            # mailroom-core's own rows from before users (core_0002), which the app never names.
            "owner": [{"id": "home", "login": "o@x.org"}],
        }
        for user_id, accounts in docs.items():
            _insert(
                engine,
                "insert into core.settings (user_id, revision, doc, saved_at) "
                "values (:u, 1, cast(:d as jsonb), now())",
                u=str(user_id),
                d=json.dumps({"account": accounts}),
            )
        _insert(
            engine,
            "insert into core.account_secrets (user_id, account_id, purpose, blob, updated_at) "
            "values (:u, 'work', 'password', 'x', now())",
            u=str(alice),
        )
        _insert(
            engine,
            "insert into core.sends (user_id, at, key, state, account) "
            "values (:u, now(), 'work/c1', 'sent', 'work'), (:u, now(), 'k', 'sent', 'home')",
            u=str(alice),
        )
        for user_id, account, on in ((alice, "work", True), (alice, "home", False)):
            _insert(
                engine,
                "insert into app.account_filter_settings values (:u, :a, :on)",
                u=user_id,
                a=account,
                on=on,
            )
        add_rule_set = (
            "insert into app.rule_set (user_id, account_id, folder, name, trigger) "
            "values (:u, :a, '{INBOX}', 'R', 'manual') returning id"
        )
        alices = _insert(engine, add_rule_set, u=alice, a="work")
        bobs = _insert(engine, add_rule_set, u=bob, a="home")
        _insert(
            engine,
            "insert into app.filter_run (rule_set_id, trigger, dry_run, result) values "
            "(:r, 'manual', true, cast(:result as jsonb)), (:r, 'manual', true, null)",
            r=alices,
            result=json.dumps({"account": "work", "folder": ["INBOX"], "filters": []}),
        )
        add_record = (
            "insert into app.audit_record (user_id, actor, action, account_id, result, detail) "
            "values (:u, 'user', 'move_to_account', :a, 'ok', cast(:d as jsonb))"
        )
        _insert(
            engine,
            add_record,
            u=alice,
            a="work",
            d=json.dumps({"folder": "INBOX", "destination_account": "home"}),
        )
        _insert(engine, add_record, u=alice, a="home", d=json.dumps({"source_account": "work"}))
        # An account removed before: only the trail names it.
        _insert(engine, add_record, u=alice, a="gone", d=json.dumps({"folder": "INBOX"}))
        _insert(engine, add_record, u=bob, a="home", d="{}")
        with engine.begin() as connection:
            # procrastinate's triggers name its tables unqualified.
            connection.execute(text("set local search_path to procrastinate"))
            for lock, status in (
                (f"mailbox:{alice}:work", "todo"),
                (f"mailbox:{alice}:home", "succeeded"),
            ):
                connection.execute(
                    text(
                        "insert into procrastinate_jobs (queue_name, task_name, lock, status) "
                        "values ('default', 'run_rule_set', :lock, :status)"
                    ),
                    {"lock": lock, "status": status},
                )

        upgrade(engine, CORE_MIGRATIONS)
        command.upgrade(config, "head")

        ids = {
            (int(user) if user.isdecimal() else user, slug): account_id
            for user, account_id, slug in _rows(engine, "select * from core.former_slugs")
        }
        work, home, gone = ids[alice, "work"], ids[alice, "home"], ids[alice, "gone"]
        bobs_home, owners_home = ids[bob, "home"], ids["owner", "home"]
        assert len({work, home, gone, bobs_home, owners_home}) == 5
        assert _rows(engine, "select doc from core.settings where user_id = :u", u=str(alice)) == [
            (
                {
                    "account": [
                        {"id": work, "login": "a@x.org", "name": "Office"},
                        {"id": home, "login": "a@y.org", "name": "home"},
                    ]
                },
            )
        ]
        assert _rows(engine, "select account_id from core.account_secrets") == [(work,)]
        assert _rows(engine, "select key, account from core.sends order by id") == [
            (f"{work}/c1", work),
            ("k", home),
        ]
        assert _rows(
            engine, "select account_id, run_filters from app.account_filter_settings order by 2"
        ) == [(home, False), (work, True)]
        assert _rows(engine, "select id, account_id from app.rule_set order by id") == [
            (alices, work),
            (bobs, bobs_home),
        ]
        assert _rows(engine, "select result from app.filter_run order by id") == [
            ({"account": work, "folder": ["INBOX"], "filters": []},),
            (None,),
        ]
        assert _rows(
            engine, "select user_id, account_id, detail from app.audit_record order by id"
        ) == [
            (alice, work, {"folder": "INBOX", "destination_account": home}),
            (alice, home, {"source_account": work}),
            (alice, gone, {"folder": "INBOX"}),
            (bob, bobs_home, {}),
        ]
        assert _rows(engine, "select lock from procrastinate.procrastinate_jobs order by id") == [
            (f"mailbox:{alice}:{work}",),
            # A finished job's lock stays as it was.
            (f"mailbox:{alice}:home",),
        ]
        # Old refs and cursors are stale; the app queries the new columns as numbers.
        assert _rows(
            engine,
            "select count(*) from app.rule_set where account_id = :a",
            a=work,
        ) == [(1,)]

        assert _rows(engine, "select doc from core.settings where user_id = 'owner'") == [
            ({"account": [{"id": owners_home, "login": "o@x.org", "name": "home"}]},)
        ]

        command.downgrade(config, "202611161200")
        downgrade(engine, CORE_MIGRATIONS, "core_0003")
        assert _rows(engine, "select doc from core.settings where user_id = 'owner'") == [
            ({"account": [{"id": "home", "login": "o@x.org", "name": "home"}]},)
        ]
        assert _rows(engine, "select doc from core.settings where user_id = :u", u=str(alice)) == [
            (
                {
                    "account": [
                        {"id": "work", "login": "a@x.org", "name": "Office"},
                        {"id": "home", "login": "a@y.org", "name": "home"},
                    ]
                },
            )
        ]
        assert _rows(engine, "select account_id from core.account_secrets") == [("work",)]
        assert _rows(engine, "select key, account from core.sends order by id") == [
            ("work/c1", "work"),
            ("k", "home"),
        ]
        assert _rows(engine, "select account_id from app.rule_set order by id") == [
            ("work",),
            ("home",),
        ]
        assert _rows(engine, "select result from app.filter_run order by id") == [
            ({"account": "work", "folder": ["INBOX"], "filters": []},),
            (None,),
        ]
        assert _rows(engine, "select account_id, detail from app.audit_record order by id") == [
            ("work", {"folder": "INBOX", "destination_account": "home"}),
            ("home", {"source_account": "work"}),
            ("gone", {"folder": "INBOX"}),
            ("home", {}),
        ]
        assert _rows(engine, "select account_id from app.account_filter_settings order by 1") == [
            ("home",),
            ("work",),
        ]
        assert _rows(engine, "select lock from procrastinate.procrastinate_jobs order by id") == [
            (f"mailbox:{alice}:work",),
            (f"mailbox:{alice}:home",),
        ]

        upgrade(engine, CORE_MIGRATIONS)
        command.upgrade(config, "head")
        assert _rows(engine, "select count(*) from core.former_slugs") == [(5,)]


def test_starred_only_becomes_a_rule_set_s_marker_and_comes_back(
    throwaway_databases: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``202611181200``: true is the star, false none; down again, any marker is true, and a
    ``to_sort`` rule set is disabled (it would act on starred mail instead)."""
    from alembic import command
    from alembic.config import Config

    from mailroom.db_migrations import alembic_ini_path
    from mailroom_core.store import upgrade

    with throwaway_databases.migrated(lambda _: None) as engine:
        monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
        config = Config(str(alembic_ini_path()))
        config.attributes["quiet"] = True
        upgrade(engine, CORE_MIGRATIONS)
        command.upgrade(config, "202611171200")
        add_user = (
            "insert into app.user (username, password_hash) values ('alice', 'x') returning id"
        )
        alice = _insert(engine, add_user)
        add_rule_set = (
            "insert into app.rule_set "
            "(user_id, account_id, folder, name, trigger, starred_only, enabled) "
            "values (:u, 1, '{INBOX}', :name, 'manual', :starred, true)"
        )
        _insert(engine, add_rule_set, u=alice, name="starred", starred=True)
        _insert(engine, add_rule_set, u=alice, name="every", starred=False)

        command.upgrade(config, "202611181200")
        assert _rows(engine, "select name, marker from app.rule_set order by id") == [
            ("starred", "star"),
            ("every", "none"),
        ]
        add_to_sort = (
            "insert into app.rule_set "
            "(user_id, account_id, folder, name, trigger, marker, enabled) "
            "values (:u, 1, '{INBOX}', :name, 'manual', 'to_sort', :enabled)"
        )
        _insert(engine, add_to_sort, u=alice, name="to sort", enabled=True)
        _insert(engine, add_to_sort, u=alice, name="to sort, off", enabled=False)

        command.downgrade(config, "202611171200")
        query = "select name, starred_only, enabled from app.rule_set order by id"
        assert _rows(engine, query) == [
            ("starred", True, True),
            ("every", False, True),
            ("to sort", True, False),
            ("to sort, off", True, False),
        ]
