"""Schema `core` (`mailroom_core.store`): the settings, the sealed secrets, the send log.

`src/store/db.ts` is tested through its callers; the settings case of `src/settings/stored.test.ts`
is ported in `mcp/tests/test_store.py`, with the whole document. These cover what core keeps.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from mailroom_core.store import (
    CORE_MIGRATIONS,
    SETTINGS_HISTORY,
    AccountStore,
    RevisionConflict,
    SecretChanges,
    SendCount,
    SettingsBusy,
    downgrade,
    forget_user,
    migrate,
    upgrade,
    write_settings,
)
from mailroom_core.store.schema import metadata
from mailroom_core.testing import schema_differences

T0 = datetime(2026, 10, 18, 12, tzinfo=UTC)
ALICE = "alice"
BOB = "bob"


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
    store = AccountStore(core_engine, ALICE)
    assert store.settings() is None
    assert store.save_settings({"account": []}, 0) == 1
    assert store.save_settings({"account": [{"id": 1}]}, 1) == 2
    with pytest.raises(RevisionConflict, match="at revision 2, not 1"):
        store.save_settings({"account": []}, 1)
    settings = store.settings()
    assert settings is not None
    assert (settings.revision, settings.doc) == (2, {"account": [{"id": 1}]})


def test_let_only_one_of_two_saves_from_the_same_revision_through(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
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


def test_let_two_users_save_from_the_same_revision_at_once(core_engine) -> None:
    def save(user_id: str) -> int:
        return AccountStore(core_engine, user_id).save_settings({"who": user_id}, 0)

    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(save, (ALICE, BOB))) == [1, 1]


def test_refuse_a_save_while_another_holds_the_users_settings_too_long(core_engine) -> None:
    """Only the same user's save waits: another user's goes through at once."""
    store = AccountStore(core_engine, ALICE, lock_timeout=timedelta(milliseconds=100))
    with core_engine.begin() as other:
        write_settings(other, ALICE, {}, 0, SecretChanges(), T0)
        with pytest.raises(SettingsBusy, match=r"^another save of the settings is still running$"):
            store.save_settings({}, 0)
        bob = AccountStore(core_engine, BOB, lock_timeout=timedelta(milliseconds=100))
        assert bob.save_settings({}, 0) == 1
    with pytest.raises(RevisionConflict, match="at revision 1, not 0"):
        store.save_settings({}, 0)


def test_keep_the_last_revisions_only(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    for revision in range(SETTINGS_HISTORY + 5):
        store.save_settings({"n": revision}, revision)
    with core_engine.connect() as connection:
        kept = connection.execute(text("select min(revision), count(*) from core.settings")).one()
    assert tuple(kept) == (6, SETTINGS_HISTORY)
    assert AccountStore(core_engine, BOB).settings() is None
    assert store.settings().doc == {"n": SETTINGS_HISTORY + 4}


def test_count_sends_in_the_window_and_forget_them_after_a_day(core_engine) -> None:
    store = AccountStore(core_engine, ALICE)
    assert store.sends_since(T0) == SendCount(count=0, oldest=None)
    store.record_send(T0)
    store.record_send(T0 + timedelta(minutes=10))
    store.record_send(T0 + timedelta(minutes=20))
    assert store.sends_since(T0) == SendCount(count=2, oldest=T0 + timedelta(minutes=10))
    store.record_send(T0 + timedelta(days=1, minutes=15))
    assert store.sends_since(T0 - timedelta(days=1)) == SendCount(
        count=2, oldest=T0 + timedelta(minutes=20)
    )
    assert AccountStore(core_engine, BOB).sends_since(T0 - timedelta(days=1)) == SendCount(0, None)


def test_keep_each_users_settings_secrets_and_sends_apart(core_engine) -> None:
    alice = AccountStore(core_engine, ALICE)
    bob = AccountStore(core_engine, BOB)
    assert alice.save_settings({"who": "alice"}, 0, SecretChanges(set={1: b"a"})) == 1
    assert bob.save_settings({"who": "bob"}, 0, SecretChanges(set={1: b"b"})) == 1
    assert alice.save_settings({"who": "alice"}, 1) == 2
    bob.record_send(T0)
    assert (alice.settings().revision, alice.settings().doc) == (2, {"who": "alice"})
    assert (bob.settings().revision, bob.settings().doc) == (1, {"who": "bob"})
    assert alice.secrets() == {1: b"a"}
    assert bob.secrets() == {1: b"b"}
    alice.save_settings({}, 2, SecretChanges(remove=[1]))
    assert alice.secrets() == {}
    assert bob.secrets() == {1: b"b"}
    alice.record_send(T0 + timedelta(days=2))
    assert bob.sends_since(T0 - timedelta(seconds=1)) == SendCount(count=1, oldest=T0)
    assert alice.sends_since(T0) == SendCount(count=1, oldest=T0 + timedelta(days=2))


def test_forget_one_users_settings_secrets_and_sends_only(core_engine) -> None:
    alice = AccountStore(core_engine, ALICE)
    bob = AccountStore(core_engine, BOB)
    for store in (alice, bob):
        store.save_settings({"who": store.user_id}, 0, SecretChanges(set={1: b"x"}))
        store.save_settings({"who": store.user_id}, 1)
        store.record_send(T0)
    alice.forget()
    assert alice.settings() is None
    assert alice.secrets() == {}
    assert alice.sends_since(T0 - timedelta(seconds=1)) == SendCount(count=0, oldest=None)
    with core_engine.connect() as connection:
        rows = connection.execute(
            text("select count(*) from core.settings where user_id = :u"), {"u": ALICE}
        ).scalar()
    assert rows == 0
    assert (bob.settings().revision, bob.settings().doc) == (2, {"who": BOB})
    assert bob.secrets() == {1: b"x"}
    assert bob.sends_since(T0 - timedelta(seconds=1)) == SendCount(count=1, oldest=T0)
    assert alice.save_settings({}, 0) == 1


def test_forget_a_user_inside_the_callers_transaction(core_engine) -> None:
    alice = AccountStore(core_engine, ALICE)
    alice.save_settings({}, 0)

    def delete_the_user() -> None:
        with core_engine.begin() as connection:
            forget_user(connection, ALICE)
            raise RuntimeError("the host's own delete failed")

    with pytest.raises(RuntimeError, match="the host's own delete failed"):
        delete_the_user()
    assert alice.settings().revision == 1


def test_wait_for_a_save_of_the_users_settings_before_forgetting_them(core_engine) -> None:
    """Forgetting takes the save's lock: it is refused while a save holds it, and another user's
    save does not hold it up.
    """
    alice = AccountStore(core_engine, ALICE, lock_timeout=timedelta(milliseconds=100))
    bob = AccountStore(core_engine, BOB, lock_timeout=timedelta(milliseconds=100))
    bob.save_settings({}, 0)
    with core_engine.begin() as other:
        write_settings(other, ALICE, {}, 0, SecretChanges(), T0)
        with pytest.raises(SettingsBusy, match=r"^another save of the settings is still running$"):
            alice.forget()
        bob.forget()
    assert alice.settings().revision == 1
    with core_engine.begin() as other:
        forget_user(other, ALICE)
        with pytest.raises(SettingsBusy):
            alice.save_settings({}, 1)
    assert alice.settings() is None


def test_key_the_rows_stored_before_users_to_the_standalone_user(throwaway_databases) -> None:
    with throwaway_databases.migrated(lambda _: None) as engine:
        upgrade(engine, CORE_MIGRATIONS, "core_0001")
        with engine.begin() as connection:
            connection.execute(text("""insert into core.settings values (1, '{"n": 1}', now())"""))
            connection.execute(
                text("insert into core.account_secrets values ('acc', 'password', 'x', now())")
            )
            connection.execute(text("insert into core.sends (at) values (:at)"), {"at": T0})
        migrate(engine)
        assert schema_differences(engine, metadata) == []
        # v0002 gave the rows stored before users to "owner".
        owner = AccountStore(engine, "owner")
        assert owner.settings().doc == {"n": 1}
        assert owner.secrets() == {1: b"x"}
        assert owner.sends_since(T0 - timedelta(seconds=1)).count == 1

        AccountStore(engine, ALICE).save_settings({}, 0)
        with pytest.raises(RuntimeError, match="rows of users other than 'owner'"):
            downgrade(engine, CORE_MIGRATIONS, "core_0001")
        with engine.begin() as connection:
            connection.execute(text("delete from core.settings where user_id = :u"), {"u": ALICE})
        downgrade(engine, CORE_MIGRATIONS, "core_0001")
        with engine.connect() as connection:
            assert connection.execute(text("select revision from core.settings")).scalar() == 1


def test_hand_out_account_ids_never_given_before(core_engine) -> None:
    alice = AccountStore(core_engine, ALICE)
    bob = AccountStore(core_engine, BOB)
    first = alice.new_account_id()
    assert [bob.new_account_id(), alice.new_account_id()] == [first + 1, first + 2]
    alice.forget()
    assert alice.new_account_id() == first + 3


def _account(account_id: object, **fields: object) -> dict[str, object]:
    return {"id": account_id, "login": f"{account_id}@example.org", **fields}


def _rows(engine, query: str) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(query))]


def test_number_the_slug_ids_and_give_them_back(throwaway_databases) -> None:
    """core_0004 on rows as core_0003 left them: two users, history, secrets and sends."""
    with throwaway_databases.migrated(lambda _: None) as engine:
        upgrade(engine, CORE_MIGRATIONS, "core_0003")
        # "work" is unnamed, but another account there is already named so: it stays unnamed.
        alice_v1 = {
            "account": [_account("work"), _account("old", name=" WORK ")],
            "limits": {"send_per_hour": 5},
        }
        alice_v2 = {
            "account": [_account("work", name="Office"), _account("home"), {"odd": True}],
            "limits": {"send_per_hour": 5},
        }
        bob_v1 = {"account": [_account("home"), _account("7")]}
        with engine.begin() as connection:
            for user, revision, doc in (
                (ALICE, 1, alice_v1),
                (ALICE, 2, alice_v2),
                (BOB, 1, bob_v1),
                ("carol", 1, {"n": 1}),
            ):
                connection.execute(
                    text(
                        "insert into core.settings (user_id, revision, doc, saved_at) "
                        "values (:u, :r, cast(:d as jsonb), now())"
                    ),
                    {"u": user, "r": revision, "d": json.dumps(doc)},
                )
            for user, account in ((ALICE, "work"), (ALICE, "home"), (BOB, "7"), (BOB, "gone")):
                connection.execute(
                    text(
                        "insert into core.account_secrets "
                        "(user_id, account_id, purpose, blob, updated_at) "
                        "values (:u, :a, 'password', :b, now())"
                    ),
                    {"u": user, "a": account, "b": f"{user}/{account}".encode()},
                )
            for user, account in ((ALICE, "home"), (ALICE, "old"), (BOB, None)):
                connection.execute(
                    text("insert into core.sends (user_id, at, account) values (:u, :at, :a)"),
                    {"u": user, "at": T0, "a": account},
                )

        migrate(engine)
        assert schema_differences(engine, metadata) == []
        alice = AccountStore(engine, ALICE)
        bob = AccountStore(engine, BOB)
        # Alice's current accounts first, then what only her history, secrets and sends name.
        assert alice.former_slugs() == {1: "work", 2: "home", 3: "old"}
        assert bob.former_slugs() == {4: "home", 5: "7", 6: "gone"}
        assert AccountStore(engine, "carol").former_slugs() == {}
        assert alice.settings().doc == {
            "account": [
                {**_account("work", name="Office"), "id": 1},
                {**_account("home"), "id": 2, "name": "home"},
                {"odd": True},
            ],
            "limits": {"send_per_hour": 5},
        }
        assert _rows(
            engine, "select doc from core.settings where revision = 1 order by user_id"
        ) == [
            (
                {
                    "account": [
                        {**_account("work"), "id": 1},
                        {**_account("old", name=" WORK "), "id": 3},
                    ],
                    "limits": {"send_per_hour": 5},
                },
            ),
            (
                {
                    "account": [
                        {**_account("home"), "id": 4, "name": "home"},
                        {**_account("7"), "id": 5, "name": "7"},
                    ]
                },
            ),
            ({"n": 1},),
        ]
        assert alice.secrets() == {1: b"alice/work", 2: b"alice/home"}
        assert bob.secrets() == {5: b"bob/7", 6: b"bob/gone"}
        assert _rows(engine, "select user_id, account from core.sends order by id") == [
            (ALICE, 2),
            (ALICE, 3),
            (BOB, None),
        ]
        assert alice.new_account_id() == 7

        # Added since: its name becomes its slug, or account-<id> when that cannot be one.
        current = alice.settings()
        doc = {
            **current.doc,
            "account": [
                *current.doc["account"],
                {**_account("new"), "id": 8, "name": "travel"},
                {**_account("new2"), "id": 9, "name": "Not a slug"},
                {**_account("new3"), "id": 10, "name": "home"},
            ],
        }
        alice.save_settings(doc, current.revision, SecretChanges(set={8: b"alice/travel"}))

        downgrade(engine, CORE_MIGRATIONS, "core_0003")
        assert _rows(
            engine, "select user_id, revision, doc from core.settings order by user_id, revision"
        ) == [
            (ALICE, 1, alice_v1),
            (
                ALICE,
                2,
                {
                    **alice_v2,
                    "account": [
                        _account("work", name="Office"),
                        _account("home", name="home"),
                        {"odd": True},
                    ],
                },
            ),
            (
                ALICE,
                3,
                {
                    **alice_v2,
                    "account": [
                        _account("work", name="Office"),
                        _account("home", name="home"),
                        {"odd": True},
                        {**_account("new"), "id": "travel", "name": "travel"},
                        {**_account("new2"), "id": "account-9", "name": "Not a slug"},
                        {**_account("new3"), "id": "account-10", "name": "home"},
                    ],
                },
            ),
            (BOB, 1, {"account": [_account("home", name="home"), _account("7", name="7")]}),
            ("carol", 1, {"n": 1}),
        ]
        assert _rows(
            engine,
            "select user_id, account_id from core.account_secrets order by user_id, account_id",
        ) == [(ALICE, "home"), (ALICE, "travel"), (ALICE, "work"), (BOB, "7"), (BOB, "gone")]
        assert _rows(engine, "select user_id, account from core.sends order by id") == [
            (ALICE, "home"),
            (ALICE, "old"),
            (BOB, None),
        ]
        migrate(engine)
        assert schema_differences(engine, metadata) == []
