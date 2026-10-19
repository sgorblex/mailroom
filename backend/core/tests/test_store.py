"""Schema `core` (`mailroom_core.store`): the settings, the sealed secrets, the send log.

`src/store/db.ts` is tested through its callers; the settings case of `src/settings/stored.test.ts`
is ported in `mcp/tests/test_store.py`, with the whole document. These cover what core keeps.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from mailroom_core.store import (
    CORE_MIGRATIONS,
    SETTINGS_HISTORY,
    AccountStore,
    RevisionConflict,
    SendCount,
    SettingsBusy,
    downgrade,
    migrate,
)
from mailroom_core.store.schema import metadata
from mailroom_core.testing import schema_differences

T0 = datetime(2026, 10, 18, 12, tzinfo=UTC)


def test_migrations_create_the_declared_tables_and_undo_them(core_engine) -> None:
    assert schema_differences(core_engine, metadata) == []
    downgrade(core_engine, CORE_MIGRATIONS)
    with core_engine.connect() as connection:
        tables = connection.execute(
            text("select table_name from information_schema.tables where table_schema = 'core'")
        ).scalars()
        assert list(tables) == ["alembic_version"]
    migrate(core_engine)
    migrate(core_engine)
    assert schema_differences(core_engine, metadata) == []


def test_refuse_a_save_based_on_an_outdated_revision(core_engine) -> None:
    store = AccountStore(core_engine)
    assert store.settings() is None
    assert store.save_settings({"account": []}, 0) == 1
    assert store.save_settings({"account": [{"id": "acc"}]}, 1) == 2
    with pytest.raises(RevisionConflict, match="at revision 2, not 1"):
        store.save_settings({"account": []}, 1)
    settings = store.settings()
    assert settings is not None
    assert (settings.revision, settings.doc) == (2, {"account": [{"id": "acc"}]})


def test_let_only_one_of_two_saves_from_the_same_revision_through(core_engine) -> None:
    store = AccountStore(core_engine)
    store.save_settings({}, 0)

    def save(n: int) -> int | RevisionConflict:
        try:
            return store.save_settings({"n": n}, 1)
        except RevisionConflict as conflict:
            return conflict

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(save, (1, 2)))
    assert sorted(type(outcome).__name__ for outcome in outcomes) == ["RevisionConflict", "int"]
    assert store.settings().revision == 2


def test_refuse_a_save_while_another_holds_the_settings_too_long(core_engine) -> None:
    store = AccountStore(core_engine, lock_timeout=timedelta(milliseconds=100))
    with core_engine.begin() as other:
        other.execute(text("lock table core.settings in share row exclusive mode"))
        with pytest.raises(SettingsBusy, match=r"^another save of the settings is still running$"):
            store.save_settings({}, 0)
    assert store.save_settings({}, 0) == 1


def test_keep_the_last_revisions_only(core_engine) -> None:
    store = AccountStore(core_engine)
    for revision in range(SETTINGS_HISTORY + 5):
        store.save_settings({"n": revision}, revision)
    with core_engine.connect() as connection:
        kept = connection.execute(text("select min(revision), count(*) from core.settings")).one()
    assert tuple(kept) == (6, SETTINGS_HISTORY)
    assert store.settings().doc == {"n": SETTINGS_HISTORY + 4}


def test_count_sends_in_the_window_and_forget_them_after_a_day(core_engine) -> None:
    store = AccountStore(core_engine)
    assert store.sends_since(T0) == SendCount(count=0, oldest=None)
    store.record_send(T0)
    store.record_send(T0 + timedelta(minutes=10))
    store.record_send(T0 + timedelta(minutes=20))
    assert store.sends_since(T0) == SendCount(count=2, oldest=T0 + timedelta(minutes=10))
    store.record_send(T0 + timedelta(days=1, minutes=15))
    assert store.sends_since(T0 - timedelta(days=1)) == SendCount(
        count=2, oldest=T0 + timedelta(minutes=20)
    )
