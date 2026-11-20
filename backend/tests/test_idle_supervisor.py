"""The IDLE supervisor's reconciliation, with stand-in watches: the folders wanted by the rule sets
and the index, the index's cap per account, one watch shared by both, and the viewing interests'
listener. A real watch on real mail: ``test_filter_runner_mail``."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import psycopg
import pytest
from sqlalchemy import select, text

from mailroom.accounts.store import load_accounts, user_store
from mailroom.database import libpq_database_url
from mailroom.mail.index import INTEREST_CHANNEL
from mailroom.models import MailViewInterest
from mailroom.worker import idle, tasks
from mailroom.worker.idle import (
    INDEX_WATCHES_PER_ACCOUNT,
    MAX_RETRY_SECONDS,
    FolderWatch,
    IdleSupervisor,
    Reactions,
    WatchedFolder,
    account_fingerprint,
    folder_path,
    wanted_folders,
    watch_changes,
)
from mailroom_core.mail import IdleChange
from tests.test_api_accounts import new_account
from tests.test_filter_runner import add_account, add_rule_set, runs, set_run_filters
from tests.test_mail_index_sync import jobs
from tests.test_mail_index_writes import folder as index_folder

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.testclient import TestClient

    from mailroom.models import User
    from mailroom.worker.context import Mailboxes
    from mailroom.worker.idle import Reactor
    from mailroom_core.config import AccountConfig

pytestmark = pytest.mark.usefixtures("numbered_accounts")

INBOX = WatchedFolder(1, 1, "INBOX")
ARCHIVE = WatchedFolder(1, 1, "Archive")
HOME = WatchedFolder(1, 2, "INBOX")


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


def stored_account(engine: Engine, user: User, account_id: int = 1) -> AccountConfig:
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


class Heard:
    """A ``Reactor`` that only keeps what it heard."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def connected(self) -> None:
        self.events.append("connected")

    def changed(self, change: IdleChange) -> None:
        self.events.append(change)

    def delimiter(self, delimiter: str | None) -> None:
        self.events.append(("delimiter", delimiter))


class FakeWatch:
    def __init__(self, revision: int, reactor: Reactor) -> None:
        self.revision = revision
        self.reactor = reactor
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

    def __call__(self, folder: WatchedFolder, revision: int, reactor: Reactor) -> FakeWatch:
        watch = FakeWatch(revision, reactor)
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
    inbox = WatchedFolder(user.id, 1, "INBOX")
    archive = WatchedFolder(user.id, 1, "Archive")
    add_account(user_client)
    first = add_rule_set(user_client, 1, trigger="new_mail")
    add_rule_set(user_client, 1, trigger="new_mail")
    add_rule_set(user_client, 1, trigger="scheduled", interval_minutes=5, folder=["Archive"])
    add_rule_set(user_client, 1, trigger="manual", folder=["Archive"])

    supervisor.reconcile()
    assert supervisor.watched == {inbox}
    watch = watches.latest(inbox)

    # Unchanged: left running.
    supervisor.reconcile()
    assert len(watches.started) == 1

    # Another folder.
    archived = add_rule_set(user_client, 1, trigger="new_mail", folder=["Archive"])
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
    set_run_filters(user_client, 1, on=False)
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
    inbox = WatchedFolder(user.id, 1, "INBOX")
    add_account(user_client)
    add_rule_set(user_client, 1, trigger="new_mail")
    supervisor.reconcile()
    first = watches.latest(inbox)

    changed = new_account(login="other@example.org")
    del changed["password"]
    assert user_client.put("/api/v1/accounts/1", json=changed).status_code == 200
    supervisor.reconcile()
    second = watches.latest(inbox)
    assert second is not first
    assert first.joined
    assert second.revision > first.revision

    # The port alone is a change too.
    moved = changed | {"imap": changed["imap"] | {"port": 994}}
    assert user_client.put("/api/v1/accounts/1", json=moved).status_code == 200
    supervisor.reconcile()
    third = watches.latest(inbox)
    assert third is not second
    assert second.joined
    assert stored_account(migrated_engine, user).imap.port == 994

    # An edit the watch does not depend on leaves it running.
    approval = moved | {"send_mode": "approval"}
    assert user_client.put("/api/v1/accounts/1", json=approval).status_code == 200
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
    rule_set_id = add_rule_set(user_client, 1, trigger="new_mail")
    supervisor.reconcile()
    watches.latest(WatchedFolder(user.id, 1, "INBOX")).reactor.connected()
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
    add_rule_set(user_client, 1, trigger="new_mail")
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


def test_a_starttls_account_is_watched_and_the_tls_mode_alone_restarts_it(
    user_client: TestClient, user: User, supervisor: IdleSupervisor, watches: Watches
) -> None:
    inbox = WatchedFolder(user.id, 1, "INBOX")
    add_account(user_client)
    add_rule_set(user_client, 1, trigger="new_mail")
    supervisor.reconcile()
    watch = watches.latest(inbox)

    starttls = new_account(imap={"host": "127.0.0.1", "port": 9, "tls": "starttls"})
    del starttls["password"]
    assert user_client.put("/api/v1/accounts/1", json=starttls).status_code == 200
    supervisor.reconcile()
    assert watch.joined
    assert watches.latest(inbox) is not watch
    assert supervisor.watched == {inbox}
    assert len(watches.started) == 2


def test_a_password_that_no_longer_opens_ends_the_watch_with_the_app_s_code() -> None:
    from mailroom.mail.connections import PASSWORD_MISSING_TEXT, PasswordMissing

    class Connections:
        def delimiter(self, _account_id: int) -> str:
            raise PasswordMissing

    class Pool:
        @contextmanager
        def lease(self, _user_id: int, _revision: int) -> Iterator[Connections]:
            yield Connections()

    watch = FolderWatch(cast("Mailboxes", Pool()), INBOX, 1, Heard())
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
    inbox = WatchedFolder(user.id, 1, "INBOX")
    add_account(user_client)
    add_rule_set(user_client, 1, trigger="new_mail")
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
    folder = WatchedFolder(user.id, 1, "INBOX")
    revision = load_accounts(user_store(migrated_engine, user.id)).revision
    heard = Heard()
    watch = FolderWatch(mailboxes, folder, revision, heard)
    watch.join(30)
    assert not watch.alive
    assert not watch.connected
    assert (watch.failure or "").startswith("unreachable: ")
    assert heard.events == []


def test_the_supervisor_stops_every_watch_when_told(
    user_client: TestClient,
    user: User,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    watches: Watches,
) -> None:
    add_account(user_client)
    add_rule_set(user_client, 1, trigger="new_mail")
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


# The index's folders, and the watches the rule sets share with it.

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


def view(db: Session, user: User, account: int, folder: str, expires_at: datetime) -> None:
    db.add(
        MailViewInterest(user_id=user.id, account_id=account, folder=folder, expires_at=expires_at)
    )
    db.commit()


def sync_jobs(db: Session) -> list[int]:
    return sorted(job["folder_id"] for job in jobs(db, tasks.SYNC_FOLDER))


def test_the_index_watches_inbox_and_the_folder_viewed_last(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work, home = add_account(user_client), add_account(user_client, "home")
    inbox = index_folder(db_session, user, work, "INBOX")
    projects = index_folder(db_session, user, work, "Projects")
    receipts = index_folder(db_session, user, work, "Receipts")
    index_folder(db_session, user, work, "Junk", enabled=False)
    index_folder(db_session, user, home, "INBOX", enabled=False)
    soon, later = NOW + timedelta(minutes=1), NOW + timedelta(minutes=2)
    view(db_session, user, work, "Projects", soon)
    view(db_session, user, work, "Receipts", later)
    view(db_session, user, work, "Junk", later)  # Off the index: nothing to keep fresh.
    view(db_session, user, work, "Live", later)  # No row: read live.
    view(db_session, user, home, "INBOX", later)

    wanted = wanted_folders(db_session, NOW, rules=True, index=True)
    # The cap: INBOX and the folder viewed last; an INBOX off the index is not watched.
    assert dict(wanted.index) == {
        (user.id, work, "INBOX"): inbox,
        (user.id, work, "Receipts"): receipts,
    }
    assert len(wanted.index) == INDEX_WATCHES_PER_ACCOUNT

    # Once that interest expires, the one before it is the folder viewed last.
    wanted = wanted_folders(db_session, later, rules=True, index=True)
    assert dict(wanted.index) == {(user.id, work, "INBOX"): inbox}
    wanted = wanted_folders(db_session, soon - timedelta(seconds=1), rules=True, index=True)
    assert (user.id, work, "Receipts") in wanted.index
    db_session.delete(db_session.get(MailViewInterest, (user.id, work, "Receipts")))
    db_session.commit()
    wanted = wanted_folders(db_session, NOW, rules=True, index=True)
    assert dict(wanted.index) == {
        (user.id, work, "INBOX"): inbox,
        (user.id, work, "Projects"): projects,
    }

    # Without the mail client, nothing for the index.
    assert wanted_folders(db_session, NOW, rules=True, index=False).index == {}


def test_on_gmail_all_mail_stands_for_inbox(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    """On Gmail All Mail is the account's only row, INBOX one of its labels."""
    work = add_account(user_client)
    all_mail = index_folder(db_session, user, work, "[Gmail]/All Mail")
    view(db_session, user, work, "INBOX", NOW + timedelta(minutes=1))
    wanted = wanted_folders(db_session, NOW, rules=True, index=True)
    assert dict(wanted.index) == {(user.id, work, "[Gmail]/All Mail"): all_mail}


def test_a_folder_wanted_by_both_has_one_watch_doing_both(
    monkeypatch: pytest.MonkeyPatch,
    user_client: TestClient,
    db_session: Session,
    user: User,
    supervisor: IdleSupervisor,
    watches: Watches,
) -> None:
    work = add_account(user_client)
    inbox_id = index_folder(db_session, user, work, "INBOX")
    inbox = WatchedFolder(user.id, work, "INBOX")
    supervisor.reconcile()
    assert supervisor.watched == {inbox}
    assert supervisor.reactions(inbox) == Reactions(index_folder=inbox_id)
    watch = watches.latest(inbox)

    # A rule set on the same folder: the same watch does both, without a restart.
    rule_set_id = add_rule_set(user_client, work, trigger="new_mail")
    supervisor.reconcile()
    assert watches.latest(inbox) is watch
    assert supervisor.reactions(inbox) == Reactions(rule_folder=("INBOX",), index_folder=inbox_id)

    # A connection queues both.
    watch.reactor.connected()
    assert [run.trigger for run in runs(db_session, rule_set_id)] == ["new_mail"]
    assert sync_jobs(db_session) == [inbox_id]
    # Coalesced: one sync waits per folder.
    watch.reactor.changed(IdleChange(exists=2))
    assert sync_jobs(db_session) == [inbox_id]

    # New mail queues both; a flag change, any other FETCH or an expunge the sync alone; a mere
    # OK nothing.
    queued: list[tuple[str, object]] = []
    monkeypatch.setattr(
        idle, "queue_new_mail_runs", lambda _db, *args: queued.append(("runs", args))
    )
    monkeypatch.setattr(idle, "queue_folder_sync", lambda _db, *args: queued.append(("sync", args)))
    changes = [
        IdleChange(exists=3),
        IdleChange(flags={1: frozenset({"\\Seen"})}),
        IdleChange(responses=((3, b"FETCH", (b"MODSEQ", (12,))),)),
        IdleChange(expunged=(1,)),
        IdleChange(responses=((b"OK", b"Still here"),)),
    ]
    for change in changes:
        watch.reactor.changed(change)
    runs_args, sync_args = (user.id, work, ("INBOX",)), (user.id, work, inbox_id)
    assert queued == [("runs", runs_args)] + [("sync", sync_args)] * 4

    # The folder off the index: the watch goes on for the rule set alone.
    db_session.execute(text("UPDATE app.mail_index_folder SET enabled = false"))
    db_session.commit()
    supervisor.reconcile()
    assert watches.latest(inbox) is watch
    assert supervisor.reactions(inbox) == Reactions(rule_folder=("INBOX",))


def test_a_rule_set_folder_of_several_segments_joins_the_index_watch_once_named(
    user_client: TestClient,
    db_session: Session,
    user: User,
    supervisor: IdleSupervisor,
    watches: Watches,
) -> None:
    work = add_account(user_client)
    add_rule_set(user_client, work, trigger="new_mail", folder=["Archive", "2026"])
    unnamed = WatchedFolder(user.id, work, "", ("Archive", "2026"))
    named = WatchedFolder(user.id, work, "Archive/2026")
    supervisor.reconcile()
    assert supervisor.watched == {unnamed}
    watch = watches.latest(unnamed)

    # The watch learns the delimiter: it keeps going under the server's name.
    watch.reactor.delimiter("/")
    supervisor.reconcile()
    assert supervisor.watched == {named}
    assert len(watches.started) == 1
    assert watch.running

    # A browser views it: the same watch keeps its index fresh too.
    archive_id = index_folder(db_session, user, work, "Archive/2026")
    view(db_session, user, work, "Archive/2026", datetime.now(UTC) + timedelta(minutes=5))
    supervisor.reconcile()
    assert supervisor.watched == {named}
    assert len(watches.started) == 1
    assert supervisor.reactions(named) == Reactions(
        rule_folder=("Archive", "2026"), index_folder=archive_id
    )


def test_expired_interests_are_deleted(
    user_client: TestClient, db_session: Session, user: User, supervisor: IdleSupervisor
) -> None:
    work = add_account(user_client)
    now = datetime.now(UTC)
    view(db_session, user, work, "Old", now - timedelta(seconds=1))
    view(db_session, user, work, "Open", now + timedelta(minutes=5))
    supervisor.reconcile()
    folders = db_session.scalars(select(MailViewInterest.folder)).all()
    assert folders == ["Open"]


def test_only_the_features_on_are_watched(
    user_client: TestClient,
    db_session: Session,
    user: User,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    watches: Watches,
) -> None:
    work = add_account(user_client)
    index_folder(db_session, user, work, "INBOX")
    add_rule_set(user_client, work, trigger="new_mail", folder=["Archive"])
    filters_only = IdleSupervisor(sessions, mailboxes, index=False, start_watch=watches)
    filters_only.reconcile()
    assert filters_only.watched == {WatchedFolder(user.id, work, "Archive")}
    mail_only = IdleSupervisor(sessions, mailboxes, rules=False, start_watch=watches)
    mail_only.reconcile()
    assert mail_only.watched == {WatchedFolder(user.id, work, "INBOX")}


def test_an_announced_interest_reconciles_at_once(
    sessions: sessionmaker[Session], mailboxes: Mailboxes, migrated_engine: Engine
) -> None:
    url = libpq_database_url(migrated_engine.url.render_as_string(hide_password=False))
    supervisor = IdleSupervisor(
        sessions,
        mailboxes,
        listen=lambda: psycopg.connect(url, autocommit=True),
        start_watch=Watches(),
        interval=3600,
    )
    reconciled = threading.Semaphore(0)
    count = 0

    def reconcile() -> None:
        nonlocal count
        count += 1
        reconciled.release()

    supervisor.reconcile = reconcile  # type: ignore[method-assign]
    stop = threading.Event()
    thread = threading.Thread(target=supervisor.run, args=(stop,))
    thread.start()
    try:
        assert reconciled.acquire(timeout=5)
        with psycopg.connect(url, autocommit=True) as announcing:
            deadline = time.monotonic() + 10
            while count < 3 and time.monotonic() < deadline:
                announcing.execute(f"NOTIFY {INTEREST_CHANNEL}")
                reconciled.acquire(timeout=0.2)
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
    assert count >= 3


def test_a_listener_that_cannot_connect_leaves_the_interval(
    sessions: sessionmaker[Session], mailboxes: Mailboxes, caplog: pytest.LogCaptureFixture
) -> None:
    def refused() -> Any:
        raise psycopg.OperationalError("refused")

    supervisor = IdleSupervisor(
        sessions, mailboxes, listen=refused, start_watch=Watches(), interval=0.01
    )
    calls = threading.Semaphore(0)
    supervisor.reconcile = calls.release  # type: ignore[method-assign]
    caplog.set_level(logging.WARNING, logger="mailroom.worker.idle")
    stop = threading.Event()
    thread = threading.Thread(target=supervisor.run, args=(stop,))
    thread.start()
    try:
        for _ in range(5):
            assert calls.acquire(timeout=5)
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
    warnings = [r.getMessage() for r in caplog.records if r.name == "mailroom.worker.idle"]
    assert warnings == ["Could not listen for viewed folders: OperationalError"]
