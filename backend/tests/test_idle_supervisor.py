"""The IDLE supervisor's reconciliation, with stand-in watches. A real watch on real mail:
``test_filter_runner_mail``."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

import pytest

from mailroom.accounts.store import load_accounts, user_store
from mailroom.worker.idle import (
    MAX_RETRY_SECONDS,
    FolderWatch,
    IdleSupervisor,
    WatchedFolder,
    account_fingerprint,
    folder_path,
    watch_changes,
)
from tests.test_api_accounts import new_account
from tests.test_filter_runner import add_account, add_rule_set, runs, set_run_filters

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.testclient import TestClient

    from mailroom.models import User
    from mailroom.worker.context import Mailboxes
    from mailroom_core.config import AccountConfig

INBOX = WatchedFolder(1, "work", ("INBOX",))
ARCHIVE = WatchedFolder(1, "work", ("Archive",))
HOME = WatchedFolder(1, "home", ("INBOX",))


def test_the_plan_starts_the_new_and_stops_the_unwanted() -> None:
    changes = watch_changes({INBOX: ("a", True), ARCHIVE: ("b", True)}, {INBOX: "a", HOME: "c"})
    assert (changes.stop, changes.start) == ({ARCHIVE}, {HOME})


def test_the_plan_restarts_the_changed_and_the_dead() -> None:
    changes = watch_changes(
        {INBOX: ("a", True), ARCHIVE: ("b", False), HOME: ("c", True)},
        {INBOX: "a2", ARCHIVE: "b", HOME: "c"},
    )
    assert (changes.stop, changes.start) == ({INBOX, ARCHIVE}, {INBOX, ARCHIVE})


def test_the_plan_leaves_what_matches() -> None:
    changes = watch_changes({INBOX: ("a", True)}, {INBOX: "a"})
    assert (changes.stop, changes.start) == (frozenset(), frozenset())


def test_a_folder_is_named_with_the_server_delimiter() -> None:
    assert folder_path(["INBOX"], None) == "INBOX"
    assert folder_path(["Archive", "2026"], "/") == "Archive/2026"
    assert folder_path(["Archive", "2026"], ".") == "Archive.2026"


def stored_account(engine: Engine, user: User, account_id: str = "work") -> AccountConfig:
    return load_accounts(user_store(engine, user.id)).account(account_id)


def test_the_fingerprint_follows_the_server_the_login_and_the_password(
    user_client: TestClient, user: User, migrated_engine: Engine
) -> None:
    add_account(user_client)
    account = stored_account(migrated_engine, user)
    imap = account.imap
    same = account_fingerprint(account, "secret")
    assert account_fingerprint(account, "secret") == same
    assert account_fingerprint(account, "other") != same
    changes = [
        {"login": "other@example.org"},
        {"imap": imap.model_copy(update={"host": "imap.example.org"})},
        {"imap": imap.model_copy(update={"port": 143})},
        {"imap": imap.model_copy(update={"tls": "starttls"})},
    ]
    for change in changes:
        assert account_fingerprint(account.model_copy(update=change), "secret") != same
    # The SMTP server or the send mode are not the watch's business.
    unrelated = account.model_copy(update={"send_mode": "approval"})
    assert account_fingerprint(unrelated, "secret") == same


class FakeWatch:
    def __init__(self, revision: int, on_new_mail: Callable[[], None]) -> None:
        self.revision = revision
        self.on_new_mail = on_new_mail
        self.running = True
        self.joined = False
        self.reached = False
        self.reason: str | None = None

    @property
    def alive(self) -> bool:
        return self.running

    @property
    def connected(self) -> bool:
        return self.reached

    @property
    def failure(self) -> str | None:
        return self.reason

    def die(self, reason: str) -> None:
        self.running = False
        self.reason = reason

    def stop(self) -> None:
        self.running = False

    def join(self, timeout: float | None = None) -> None:
        self.joined = True


class Watches:
    def __init__(self) -> None:
        self.started: list[tuple[WatchedFolder, FakeWatch]] = []

    def __call__(
        self, folder: WatchedFolder, revision: int, on_new_mail: Callable[[], None]
    ) -> FakeWatch:
        watch = FakeWatch(revision, on_new_mail)
        self.started.append((folder, watch))
        return watch

    def latest(self, folder: WatchedFolder) -> FakeWatch:
        return [watch for started, watch in self.started if started == folder][-1]


@pytest.fixture
def watches() -> Watches:
    return Watches()


@pytest.fixture
def supervisor(
    sessions: sessionmaker[Session], mailboxes: Mailboxes, watches: Watches
) -> IdleSupervisor:
    return IdleSupervisor(sessions, mailboxes, start_watch=watches)


def test_the_supervisor_follows_the_rule_sets(
    user_client: TestClient, user: User, supervisor: IdleSupervisor, watches: Watches
) -> None:
    inbox = WatchedFolder(user.id, "work", ("INBOX",))
    archive = WatchedFolder(user.id, "work", ("Archive",))
    add_account(user_client)
    first = add_rule_set(user_client, trigger="new_mail")
    add_rule_set(user_client, trigger="new_mail")
    add_rule_set(user_client, trigger="scheduled", interval_minutes=5, folder=["Archive"])
    add_rule_set(user_client, trigger="manual", folder=["Archive"])

    supervisor.reconcile()
    assert supervisor.watched == {inbox}
    watch = watches.latest(inbox)

    # Unchanged: left running.
    supervisor.reconcile()
    assert len(watches.started) == 1

    # Another folder.
    archived = add_rule_set(user_client, trigger="new_mail", folder=["Archive"])
    supervisor.reconcile()
    assert supervisor.watched == {inbox, archive}

    # A disabled rule set, or one that no longer triggers on new mail, needs no watch.
    user_client.patch(f"/api/v1/rule-sets/{archived}", json={"enabled": False})
    supervisor.reconcile()
    assert supervisor.watched == {inbox}
    assert watches.latest(archive).joined

    # A folder stays watched while any of its new-mail rule sets runs.
    user_client.patch(f"/api/v1/rule-sets/{first}", json={"trigger": "manual"})
    supervisor.reconcile()
    assert supervisor.watched == {inbox}
    assert watch.running

    # Run filters off: nothing at all for the account.
    set_run_filters(user_client, "work", on=False)
    supervisor.reconcile()
    assert supervisor.watched == frozenset()
    assert not watch.running
    assert watch.joined


def test_the_supervisor_restarts_changed_and_dead_watches(
    user_client: TestClient,
    user: User,
    migrated_engine: Engine,
    supervisor: IdleSupervisor,
    watches: Watches,
) -> None:
    inbox = WatchedFolder(user.id, "work", ("INBOX",))
    add_account(user_client)
    add_rule_set(user_client, trigger="new_mail")
    supervisor.reconcile()
    first = watches.latest(inbox)

    changed = new_account(login="other@example.org")
    del changed["id"], changed["password"]
    assert user_client.put("/api/v1/accounts/work", json=changed).status_code == 200
    supervisor.reconcile()
    second = watches.latest(inbox)
    assert second is not first
    assert first.joined
    assert second.revision > first.revision

    # The port alone is a change too.
    moved = changed | {"imap": changed["imap"] | {"port": 994}}
    assert user_client.put("/api/v1/accounts/work", json=moved).status_code == 200
    supervisor.reconcile()
    third = watches.latest(inbox)
    assert third is not second
    assert second.joined
    assert stored_account(migrated_engine, user).imap.port == 994

    # An edit the watch does not depend on leaves it running.
    approval = moved | {"send_mode": "approval"}
    assert user_client.put("/api/v1/accounts/work", json=approval).status_code == 200
    supervisor.reconcile()
    assert watches.latest(inbox) is third
    assert third.running

    third.running = False
    supervisor.reconcile()
    assert watches.latest(inbox) is not third
    assert len(watches.started) == 4

    supervisor.stop_all()
    assert supervisor.watched == frozenset()
    assert not watches.latest(inbox).running


def test_new_mail_queues_runs(
    user_client: TestClient,
    db_session: Session,
    user: User,
    supervisor: IdleSupervisor,
    watches: Watches,
) -> None:
    add_account(user_client)
    rule_set_id = add_rule_set(user_client, trigger="new_mail")
    supervisor.reconcile()
    watches.latest(WatchedFolder(user.id, "work", ("INBOX",))).on_new_mail()
    assert [run.trigger for run in runs(db_session, rule_set_id)] == ["new_mail"]


def test_an_account_that_cannot_be_watched_is_logged_once(
    user_client: TestClient,
    sessions: sessionmaker[Session],
    migrated_engine: Engine,
    watches: Watches,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mailroom.mail.connections import mail_ssl_context
    from mailroom.worker.context import Mailboxes

    add_account(user_client)
    add_rule_set(user_client, trigger="new_mail")
    keyless = Mailboxes(migrated_engine, None, mail_ssl_context())
    supervisor = IdleSupervisor(sessions, keyless, start_watch=watches)
    caplog.set_level(logging.WARNING, logger="mailroom.worker.idle")
    try:
        supervisor.reconcile()
        supervisor.reconcile()
    finally:
        keyless.close()
    assert supervisor.watched == frozenset()
    assert watches.started == []
    warnings = [r.getMessage() for r in caplog.records if r.name == "mailroom.worker.idle"]
    assert warnings == ["Cannot watch INBOX: secrets_key_missing"]


def test_a_starttls_account_is_not_watched_yet(
    user_client: TestClient,
    user: User,
    supervisor: IdleSupervisor,
    watches: Watches,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Its runs still go through the filter engine, which refuses STARTTLS."""
    inbox = WatchedFolder(user.id, "work", ("INBOX",))
    add_account(user_client)
    add_rule_set(user_client, trigger="new_mail")
    supervisor.reconcile()
    watch = watches.latest(inbox)

    starttls = new_account(imap={"host": "127.0.0.1", "port": 143, "tls": "starttls"})
    del starttls["id"], starttls["password"]
    assert user_client.put("/api/v1/accounts/work", json=starttls).status_code == 200
    caplog.set_level(logging.WARNING, logger="mailroom.worker.idle")
    supervisor.reconcile()
    supervisor.reconcile()
    assert watch.joined
    assert supervisor.watched == frozenset()
    assert len(watches.started) == 1
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.name == "mailroom.worker.idle" and r.levelno >= logging.WARNING
    ]
    assert warnings == ["Cannot watch INBOX: starttls_unsupported"]


def test_a_password_that_no_longer_opens_ends_the_watch_with_the_app_s_code() -> None:
    from mailroom.mail.connections import PASSWORD_MISSING_TEXT, PasswordMissing

    class Connections:
        def delimiter(self, _account_id: str) -> str:
            raise PasswordMissing

    class Pool:
        @contextmanager
        def lease(self, _user_id: int, _revision: int) -> Iterator[Connections]:
            yield Connections()

    watch = FolderWatch(cast("Mailboxes", Pool()), INBOX, 1, lambda: None)
    watch.join(5)
    assert not watch.alive
    assert watch.failure == f"password_missing: {PASSWORD_MISSING_TEXT}"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_watch_that_keeps_failing_is_retried_less_and_less_often(
    user_client: TestClient,
    user: User,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    watches: Watches,
    caplog: pytest.LogCaptureFixture,
) -> None:
    inbox = WatchedFolder(user.id, "work", ("INBOX",))
    add_account(user_client)
    add_rule_set(user_client, trigger="new_mail")
    clock = Clock()
    supervisor = IdleSupervisor(sessions, mailboxes, start_watch=watches, interval=30, clock=clock)
    caplog.set_level(logging.WARNING, logger="mailroom.worker.idle")

    def started() -> int:
        return len([folder for folder, _watch in watches.started if folder == inbox])

    supervisor.reconcile()
    waits = []
    for _ in range(8):
        watches.latest(inbox).die("unreachable: refused")
        before = started()
        supervisor.reconcile()
        waited = 0.0
        while started() == before:
            clock.now += 30
            waited += 30
            supervisor.reconcile()
        waits.append(waited)
    assert waits == [0, 30, 60, 120, 240, 480, 960, MAX_RETRY_SECONDS]
    warnings = [r.getMessage() for r in caplog.records if r.name == "mailroom.worker.idle"]
    assert warnings == ["Could not watch INBOX: unreachable: refused"]

    # A new reason is logged; a watch that reached its folder starts the count again.
    watches.latest(inbox).die("auth_failed: no")
    supervisor.reconcile()
    clock.now += MAX_RETRY_SECONDS
    supervisor.reconcile()
    assert started() == 10
    watches.latest(inbox).reached = True
    supervisor.reconcile()
    watches.latest(inbox).die("auth_failed: no")
    supervisor.reconcile()
    assert started() == 11
    warnings = [r.getMessage() for r in caplog.records if r.name == "mailroom.worker.idle"]
    assert warnings[1:] == ["Could not watch INBOX: auth_failed: no"] * 2


def test_a_watch_whose_first_connection_fails_ends(
    user_client: TestClient, user: User, migrated_engine: Engine, mailboxes: Mailboxes
) -> None:
    """The test account's IMAP port 9 on loopback is closed."""
    add_account(user_client)
    folder = WatchedFolder(user.id, "work", ("INBOX",))
    revision = load_accounts(user_store(migrated_engine, user.id)).revision
    queued: list[None] = []
    watch = FolderWatch(mailboxes, folder, revision, lambda: queued.append(None))
    watch.join(30)
    assert not watch.alive
    assert not watch.connected
    assert (watch.failure or "").startswith("unreachable: ")
    assert queued == []


def test_the_supervisor_stops_every_watch_when_told(
    user_client: TestClient,
    user: User,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    watches: Watches,
) -> None:
    add_account(user_client)
    add_rule_set(user_client, trigger="new_mail")
    supervisor = IdleSupervisor(sessions, mailboxes, start_watch=watches, interval=0.01)
    stop = threading.Event()
    thread = threading.Thread(target=supervisor.run, args=(stop,))
    thread.start()
    try:
        while not watches.started:
            stop.wait(0.01)
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
    assert supervisor.watched == frozenset()
    assert all(not watch.running and watch.joined for _folder, watch in watches.started)
