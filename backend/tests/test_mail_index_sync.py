"""The mail index's sync in the worker: the dispatcher and its jobs, an account's folders kept in
step with its listing, and a folder's rows built and kept fresh against the test Dovecot
(``IMAP_TEST_HOST``; those tests are skipped without it): newest first in shares, new mail, flag
changes (CONDSTORE or every message's), expunges, a UIDVALIDITY change and the age limit."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from sqlalchemy import select, text

from mailroom.mail.connections import PASSWORD_MISSING
from mailroom.mail.index import discover_folders, due_folders
from mailroom.mail.index import sync as index_sync
from mailroom.models import MailIndexFolder, MailIndexMessage
from mailroom.worker import idle, tasks
from mailroom.worker import index as worker_index
from mailroom.worker.app import PROCRASTINATE_SCHEMA
from mailroom.worker.context import WorkerContext
from mailroom.worker.index import account_folders_due, run_folder_sync
from mailroom_core.config import TimeZoneName
from mailroom_core.mail import Folder, FolderFlags, IndexedMessage, IndexPlan, MailError
from mailroom_core.testing import mime, raw_client, seed
from tests.test_api_accounts import added, new_account
from tests.test_filter_runner import add_account
from tests.test_mail_index_rows import indexed, message

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.testclient import TestClient

    from mailroom.models import User
    from mailroom.worker.context import Mailboxes
    from mailroom_core.mail import MailConnections
    from mailroom_core.testing import ImapTestServer

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _every_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAILROOM_FEATURES", raising=False)


@pytest.fixture
def worker(
    monkeypatch: pytest.MonkeyPatch, sessions: sessionmaker[Session], mailboxes: Mailboxes
) -> None:
    """The tasks run in the test's transaction, without pauses between batches."""
    monkeypatch.setattr(tasks, "worker_context", lambda: WorkerContext(sessions, mailboxes))
    monkeypatch.setattr(index_sync, "BATCH_PAUSE_SECONDS", 0)


def jobs(db: Session, name: str) -> list[dict[str, Any]]:
    query = text(
        f"SELECT args FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs "
        "WHERE task_name = :name AND status = 'todo' ORDER BY id"
    )
    return [row[0] for row in db.execute(query, {"name": name})]


def folder_row(db: Session, user: User, path: str) -> MailIndexFolder:
    db.expire_all()
    row = db.scalar(
        select(MailIndexFolder).where(
            MailIndexFolder.user_id == user.id, MailIndexFolder.path == path
        )
    )
    assert row is not None
    return row


def stored(db: Session, folder_id: int) -> dict[int, tuple[str, list[str], list[str]]]:
    """Subject, flags and keywords by UID."""
    query = select(
        MailIndexMessage.uid,
        MailIndexMessage.subject,
        MailIndexMessage.flags,
        MailIndexMessage.keywords,
    ).where(MailIndexMessage.folder_id == folder_id)
    return {uid: (subject, flags, keywords) for uid, subject, flags, keywords in db.execute(query)}


# The dispatcher and the jobs, without a mail server.


def test_the_dispatcher_queues_each_account_once(
    user_client: TestClient, db_session: Session, mailboxes: Mailboxes, worker: None
) -> None:
    work = add_account(user_client)
    home = add_account(user_client, "home")
    tasks.dispatch_index(int(NOW.timestamp()))
    tasks.dispatch_index(int(NOW.timestamp()))
    queued = jobs(db_session, tasks.SYNC_ACCOUNT)
    assert sorted(job["account_id"] for job in queued) == sorted([work, home])


def test_without_the_mail_client_nothing_is_queued(
    user_client: TestClient,
    db_session: Session,
    worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_account(user_client)
    monkeypatch.setenv("MAILROOM_FEATURES", "filters")
    tasks.dispatch_index(int(NOW.timestamp()))
    assert jobs(db_session, tasks.SYNC_ACCOUNT) == []


def test_a_folder_whose_mailbox_fails_records_its_code(
    user_client: TestClient, db_session: Session, user: User, worker: None
) -> None:
    """The account's IMAP port 9 on loopback is closed: its listing fails, so its folders stay,
    and each sync records why."""
    work = add_account(user_client)
    folder_id = indexed(db_session, user.id, work, uids=())
    folder = db_session.get(MailIndexFolder, folder_id)
    assert folder is not None
    folder.state = "pending"
    db_session.commit()

    tasks.sync_account(user.id, work)
    [job] = jobs(db_session, tasks.SYNC_DUE_FOLDERS)
    assert job == {"user_id": user.id, "account_id": work, "folder_ids": [folder_id]}

    tasks.sync_due_folders(**job)
    row = folder_row(db_session, user, "INBOX")
    assert (row.state, row.error) == ("failed", "unreachable")


def test_due_folders_are_the_ones_not_ready_or_synced_long_ago(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = add_account(user_client)
    fresh = indexed(db_session, user.id, work, "Fresh", uids=())
    stale = indexed(db_session, user.id, work, "Stale", uids=())
    pending = indexed(db_session, user.id, work, "Pending", uids=())
    off = indexed(db_session, user.id, work, "Off", uids=())
    for folder_id, changes in (
        (fresh, {"last_synced_at": NOW - timedelta(seconds=30)}),
        (stale, {"last_synced_at": NOW - timedelta(minutes=5)}),
        (pending, {"state": "pending"}),
        (off, {"enabled": False, "state": "pending"}),
    ):
        row = db_session.get(MailIndexFolder, folder_id)
        assert row is not None
        for name, value in changes.items():
            setattr(row, name, value)
    db_session.commit()
    assert due_folders(db_session, user.id, work, NOW) == [stale, pending]


def test_a_folder_synced_late_in_a_turn_is_due_at_the_next(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    """Its job started a minute after the dispatcher's turn, behind the account's other jobs."""
    work = add_account(user_client)
    folder_id = indexed(db_session, user.id, work, uids=())
    row = db_session.get(MailIndexFolder, folder_id)
    assert row is not None
    row.last_synced_at = NOW + timedelta(minutes=1)
    db_session.commit()
    assert due_folders(db_session, user.id, work, NOW + index_sync.RESYNC_EVERY) == [folder_id]


def test_on_gmail_only_all_mail_gets_a_row(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = add_account(user_client)
    indexed(db_session, user.id, work, "INBOX", uids=(1,))
    listed = [
        Folder("INBOX", "/", "inbox", ()),
        Folder("[Gmail]/All Mail", "/", "all", ("\\All",)),
        Folder("[Gmail]/Spam", "/", "junk", ("\\Junk",)),
        Folder("Receipts", "/", None, ()),
    ]
    gmail = SimpleNamespace(
        capabilities=lambda _account: SimpleNamespace(gmail=True),
        folders=lambda _account: listed,
    )
    assert discover_folders(db_session, cast("MailConnections", gmail), user.id, work) == 1
    assert list(db_session.scalars(select(MailIndexFolder.path))) == ["[Gmail]/All Mail"]
    assert list(db_session.scalars(select(MailIndexMessage.uid))) == []


def test_the_listing_says_which_folders_are_inboxs_subfolders(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = add_account(user_client)
    other = indexed(db_session, user.id, work, "Other/Moved", uids=())
    listed = [
        Folder("INBOX", "/", "inbox", ()),
        Folder("INBOX/Lists", "/", None, ()),
        Folder("INBOX/Lists/Deep", "/", None, ()),
        Folder("INBOX/Old", "/", "archive", ("\\Archive", "\\Noselect")),
        Folder("INBOX/Old/2024", "/", None, ()),
        Folder("INBOX/Sent", "/", "sent", ("\\Sent",)),
        Folder("INBOX/Starred", "/", "flagged", ("\\Flagged",)),
        Folder("INBOXES", "/", None, ()),
        Folder("Other/Moved", "/", None, ()),
    ]
    row = db_session.get(MailIndexFolder, other)
    assert row is not None
    row.inbox_subfolder = True
    db_session.commit()
    server = SimpleNamespace(
        capabilities=lambda _account: SimpleNamespace(gmail=False),
        folders=lambda _account: listed,
    )

    def inside() -> list[str]:
        discover_folders(db_session, cast("MailConnections", server), user.id, work)
        query = select(MailIndexFolder.path).where(MailIndexFolder.inbox_subfolder.is_(True))
        return list(db_session.scalars(query.order_by(MailIndexFolder.path)))

    assert inside() == ["INBOX/Lists", "INBOX/Lists/Deep"]
    # A folder that takes a role leaves them.
    listed[1] = Folder("INBOX/Lists", "/", "junk", ("\\Junk",))
    assert inside() == []


# The turn over an account's due folders, each folder's sync a stub.


def pending(db: Session, user: User, account: int, *paths: str) -> list[int]:
    """Folders due a sync, in order."""
    ids = [indexed(db, user.id, account, path, uids=()) for path in paths]
    for folder_id in ids:
        row = db.get(MailIndexFolder, folder_id)
        assert row is not None
        row.state = "pending"
    db.commit()
    return ids


@pytest.fixture
def syncs(monkeypatch: pytest.MonkeyPatch, worker: None) -> list[int]:
    """The folders synced, in order; none has more to fetch."""
    ran: list[int] = []

    def run_folder_sync(
        _db: Session, _mailboxes: Mailboxes, _user: int, _account: int, folder_id: int, **_: Any
    ) -> bool:
        ran.append(folder_id)
        return False

    monkeypatch.setattr(worker_index, "run_folder_sync", run_folder_sync)
    monkeypatch.setattr(tasks, "run_folder_sync", run_folder_sync)
    return ran


def next_job(db: Session) -> tuple[str, dict[str, Any]] | None:
    """The job the worker would take now: its name and arguments, gone from the queue."""
    with tasks.procrastinate_sql(db):
        found = db.execute(
            text("SELECT id, task_name, args FROM procrastinate_fetch_job_v2(NULL, NULL)")
        ).one()
        if found.id is None:
            return None
        db.execute(text("DELETE FROM procrastinate_jobs WHERE id = :id"), {"id": found.id})
    return found.task_name, found.args


def test_a_turn_syncs_every_due_folder_in_one_job(
    user_client: TestClient, db_session: Session, user: User, syncs: list[int]
) -> None:
    work = add_account(user_client)
    ids = pending(db_session, user, work, "INBOX", "A", "B")
    tasks.sync_account(user.id, work)
    [job] = jobs(db_session, tasks.SYNC_DUE_FOLDERS)
    tasks.sync_due_folders(**job)
    assert syncs == ids
    assert jobs(db_session, tasks.SYNC_FOLDER) == []
    assert len(jobs(db_session, tasks.SYNC_DUE_FOLDERS)) == 1


def test_an_account_with_nothing_due_queues_no_turn(
    user_client: TestClient, db_session: Session, user: User, worker: None
) -> None:
    tasks.sync_account(user.id, add_account(user_client))
    assert jobs(db_session, tasks.SYNC_DUE_FOLDERS) == []


def test_a_long_turn_holds_back_neither_an_inbox_sync_nor_a_filter_run(
    user_client: TestClient,
    db_session: Session,
    user: User,
    syncs: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each share past its time with its first folder: what is asked during it goes before the
    rest, and the rest goes on from where it was."""
    monkeypatch.setattr(worker_index, "SHARE_SECONDS", 0)
    work = add_account(user_client)
    first, second, inbox, last = pending(db_session, user, work, "A", "B", "INBOX", "C")
    tasks.sync_account(user.id, work)
    taken = next_job(db_session)
    assert taken is not None
    name, args = taken
    assert name == tasks.SYNC_DUE_FOLDERS
    tasks.sync_due_folders(**args)
    assert syncs == [first]

    assert tasks.queue_folder_sync(db_session, user.id, work, inbox)
    tasks.defer_rule_set_run(db_session, run_id=1, rule_set_id=1, user_id=user.id, account_id=work)
    db_session.commit()
    order = [next_job(db_session) for _ in range(4)]
    assert order == [
        (tasks.SYNC_FOLDER, {"user_id": user.id, "account_id": work, "folder_id": inbox}),
        (tasks.RUN_RULE_SET, {"run_id": 1}),
        (
            tasks.SYNC_DUE_FOLDERS,
            {"user_id": user.id, "account_id": work, "folder_ids": [second, inbox, last]},
        ),
        None,
    ]


def test_a_share_passes_over_folders_no_longer_due(
    user_client: TestClient, db_session: Session, user: User, syncs: list[int]
) -> None:
    work = add_account(user_client)
    synced_since, off, due = pending(db_session, user, work, "A", "B", "C")
    row = db_session.get(MailIndexFolder, synced_since)
    assert row is not None
    row.state, row.last_synced_at = "ready", datetime.now(UTC)
    row = db_session.get(MailIndexFolder, off)
    assert row is not None
    row.enabled = False
    db_session.commit()
    gone = due + 1000
    tasks.sync_due_folders(user.id, work, [synced_since, off, gone, due])
    assert syncs == [due]
    tasks.sync_due_folders(user.id + 1, work, [due])
    assert syncs == [due]


def test_a_new_turn_leaves_the_one_still_waiting_to_go_on(
    user_client: TestClient,
    db_session: Session,
    user: User,
    syncs: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_index, "SHARE_SECONDS", 0)
    work = add_account(user_client)
    first, second, third = pending(db_session, user, work, "A", "B", "C")
    tasks.sync_due_folders(user.id, work, [first, second, third])
    tasks.sync_account(user.id, work)
    [job] = jobs(db_session, tasks.SYNC_DUE_FOLDERS)
    assert job["folder_ids"] == [second, third]


def test_a_folder_of_a_turn_that_breaks_or_has_more_holds_up_no_other(
    user_client: TestClient,
    db_session: Session,
    user: User,
    worker: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    work = add_account(user_client)
    broken, large, last = pending(db_session, user, work, "A", "B", "C")
    ran: list[int] = []

    def run_folder_sync(
        _db: Session, _mailboxes: Mailboxes, _user: int, _account: int, folder_id: int, **_: Any
    ) -> bool:
        ran.append(folder_id)
        if folder_id == broken:
            raise RuntimeError("the sender's text")
        return folder_id == large

    monkeypatch.setattr(worker_index, "run_folder_sync", run_folder_sync)
    tasks.sync_due_folders(user.id, work, [broken, large, last])
    assert ran == [broken, large, last]
    assert [job["folder_id"] for job in jobs(db_session, tasks.SYNC_FOLDER)] == [large]
    assert "RuntimeError" in caplog.text
    assert "the sender's text" not in caplog.text
    # What it left is routine: a sync asked for goes first.
    assert tasks.queue_folder_sync(db_session, user.id, work, last)
    taken = next_job(db_session)
    assert taken is not None
    assert taken[1]["folder_id"] == last


def test_a_sync_asked_for_takes_its_folders_routine_job_ahead(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = add_account(user_client)
    other, inbox = pending(db_session, user, work, "A", "INBOX")
    routine = tasks.ROUTINE_PRIORITY
    for folder_id in (other, inbox):
        assert tasks.queue_folder_sync(db_session, user.id, work, folder_id, priority=routine)
    assert not tasks.queue_folder_sync(db_session, user.id, work, inbox)
    # A routine one queued after leaves it where it is.
    assert not tasks.queue_folder_sync(db_session, user.id, work, inbox, priority=routine)
    taken = [next_job(db_session), next_job(db_session), next_job(db_session)]
    assert [None if job is None else job[1]["folder_id"] for job in taken] == [inbox, other, None]


def test_a_refresh_goes_ahead_of_every_job_with_its_first_share_only(
    user_client: TestClient,
    db_session: Session,
    user: User,
    syncs: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It syncs folders synced a moment ago too, and runs until its last share has."""
    monkeypatch.setattr(worker_index, "SHARE_SECONDS", 0)
    work = add_account(user_client)
    first, second, other = pending(db_session, user, work, "A", "B", "C")
    for folder_id in (first, second):
        row = db_session.get(MailIndexFolder, folder_id)
        assert row is not None
        row.state, row.last_synced_at = "ready", datetime.now(UTC)
    tasks.queue_folder_sync(db_session, user.id, work, other)
    tasks.defer_rule_set_run(db_session, run_id=1, rule_set_id=1, user_id=user.id, account_id=work)
    tasks.queue_refresh(db_session, user.id, work, [], refresh_id="r")
    assert jobs(db_session, tasks.REFRESH_FOLDERS) == []
    tasks.queue_refresh(db_session, user.id, work, [first, second], refresh_id="r")
    db_session.commit()
    assert tasks.refresh_running(db_session, user.id, "r")
    assert not tasks.refresh_running(db_session, user.id + 1, "r")
    assert not tasks.refresh_running(db_session, user.id, "s")

    taken = next_job(db_session)
    assert taken is not None
    assert taken[0] == tasks.REFRESH_FOLDERS
    tasks.refresh_folders(**taken[1])
    assert syncs == [first]
    assert tasks.refresh_running(db_session, user.id, "r")
    # The rest takes its turn behind what was asked before it.
    order = [next_job(db_session) for _ in range(3)]
    rest = {
        "user_id": user.id,
        "account_id": work,
        "folder_ids": [second],
        "refreshes": {"r": [second]},
    }
    assert [job and job[0] for job in order] == [
        tasks.SYNC_FOLDER,
        tasks.RUN_RULE_SET,
        tasks.REFRESH_FOLDERS,
    ]
    assert order[2] == (tasks.REFRESH_FOLDERS, rest)
    tasks.refresh_folders(**rest)
    assert syncs == [first, second]
    assert not tasks.refresh_running(db_session, user.id, "r")


def test_a_refresh_asked_while_one_goes_on_goes_first_and_ends_first(
    user_client: TestClient,
    db_session: Session,
    user: User,
    syncs: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_index, "SHARE_SECONDS", 0)
    work = add_account(user_client)
    a, b, c = pending(db_session, user, work, "A", "B", "C")
    # Asked while the first share of another runs.
    tasks.queue_refresh(db_session, user.id, work, [c], refresh_id="other")
    tasks.refresh_folders(user.id, work, [a, b, c], {"all": [a, b, c], "a": [a]})
    [job] = jobs(db_session, tasks.REFRESH_FOLDERS)
    assert job["folder_ids"] == [c, b]
    assert job["refreshes"] == {"other": [c], "all": [b, c]}
    assert not tasks.refresh_running(db_session, user.id, "a")

    taken = next_job(db_session)
    assert taken is not None
    tasks.refresh_folders(**taken[1])
    assert syncs == [a, c]
    assert not tasks.refresh_running(db_session, user.id, "other")
    assert tasks.refresh_running(db_session, user.id, "all")


def test_a_refresh_ends_for_an_account_that_cannot_be_reached(
    user_client: TestClient, db_session: Session, user: User, worker: None
) -> None:
    """The account's IMAP port 9 on loopback is closed."""
    work = add_account(user_client)
    first, second = pending(db_session, user, work, "A", "B")
    tasks.refresh_folders(user.id, work, [first, second], {"r": [first, second]})
    row = folder_row(db_session, user, "A")
    assert (row.state, row.error) == ("failed", "unreachable")
    assert folder_row(db_session, user, "B").state == "pending"
    assert not tasks.refresh_running(db_session, user.id, "r")


def test_a_refresh_goes_on_past_a_folder_that_fails_alone(
    user_client: TestClient,
    db_session: Session,
    user: User,
    worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = add_account(user_client)
    ids = pending(db_session, user, work, "A", "B")
    ran: list[int] = []

    def run_folder_sync(
        db: Session, _mailboxes: Mailboxes, user_id: int, account: int, folder_id: int, **_: Any
    ) -> bool:
        ran.append(folder_id)
        index_sync.fail_folder(db, user_id, account, folder_id, "mailbox_missing")
        return False

    monkeypatch.setattr(worker_index, "run_folder_sync", run_folder_sync)
    tasks.refresh_folders(user.id, work, ids, {"r": ids})
    assert ran == ids


def test_a_refresh_leaves_a_folder_with_more_to_fetch_as_routine_work(
    user_client: TestClient,
    db_session: Session,
    user: User,
    worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = add_account(user_client)
    large, other = pending(db_session, user, work, "A", "B")
    monkeypatch.setattr(worker_index, "run_folder_sync", lambda *_, **__: True)
    tasks.refresh_folders(user.id, work, [large], {"r": [large]})
    assert not tasks.refresh_running(db_session, user.id, "r")
    assert tasks.queue_folder_sync(db_session, user.id, work, other)
    taken = [next_job(db_session), next_job(db_session)]
    assert [job and job[1]["folder_id"] for job in taken] == [other, large]


# Against the test Dovecot.


@pytest.fixture
def login(imap_server: ImapTestServer) -> str:
    return imap_server.unique_user("index")


@pytest.fixture
def account(user_client: TestClient, imap_server: ImapTestServer, login: str) -> int:
    endpoint = {"host": imap_server.host, "port": imap_server.port, "tls": "implicit"}
    return added(
        user_client, new_account(login=login, password=imap_server.password, imap=endpoint)
    )


def synced(
    db: Session, mailboxes: Mailboxes, user: User, account: int, path: str = "INBOX"
) -> MailIndexFolder:
    """The folder's rows, discovered and synced through to the end."""
    account_folders_due(db, mailboxes, user.id, account, NOW)
    folder_id = folder_row(db, user, path).id
    while run_folder_sync(db, mailboxes, user.id, account, folder_id):
        pass
    return folder_row(db, user, path)


def test_an_account_gets_a_row_per_folder_and_loses_the_ones_gone(
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    account: int,
) -> None:
    with raw_client(imap_server, login) as client:
        client.create_folder("Projects")
    due = account_folders_due(db_session, mailboxes, user.id, account, NOW)
    rows = {row.path: row for row in db_session.scalars(select(MailIndexFolder))}
    assert set(rows) == {"INBOX", "Sent", "Drafts", "Junk", "Trash", "Projects"}
    assert not rows["Junk"].enabled
    assert sorted(due) == sorted(row.id for row in rows.values() if row.enabled)

    with raw_client(imap_server, login) as client:
        client.rename_folder("Projects", "Done")
    account_folders_due(db_session, mailboxes, user.id, account, NOW)
    paths = set(db_session.scalars(select(MailIndexFolder.path)))
    assert paths == {"INBOX", "Sent", "Drafts", "Junk", "Trash", "Done"}


def test_a_first_sync_goes_newest_first_in_shares(
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    account: int,
    worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uids = [seed(imap_server, login, "INBOX", mime(f"m{n}")) for n in range(5)]
    read = seed(imap_server, login, "INBOX", mime("read"), flags=["\\Seen", "$Work"])
    monkeypatch.setattr(index_sync, "INDEX_BATCH", 2)
    monkeypatch.setattr(index_sync, "SLICE_BATCHES", 1)
    account_folders_due(db_session, mailboxes, user.id, account, NOW)
    inbox = folder_row(db_session, user, "INBOX")

    tasks.sync_folder(user.id, account, inbox.id)
    first = folder_row(db_session, user, "INBOX")
    assert first.state == "syncing"
    assert first.message_count == 2
    assert set(stored(db_session, inbox.id)) == {read, uids[4]}
    [job] = jobs(db_session, tasks.SYNC_FOLDER)
    assert job["folder_id"] == inbox.id

    while run_folder_sync(db_session, mailboxes, user.id, account, inbox.id):
        pass
    done = folder_row(db_session, user, "INBOX")
    assert (done.state, done.error, done.message_count, done.unread_count) == ("ready", None, 6, 5)
    assert done.uid_next == read + 1
    assert done.highest_modseq is not None
    assert done.last_synced_at is not None
    assert stored(db_session, inbox.id)[read] == ("read", ["\\Seen"], ["$Work"])


@pytest.mark.parametrize("condstore", [True, False], ids=["changedsince", "every_flag"])
def test_a_resync_takes_new_mail_flag_changes_and_expunges(
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    account: int,
    condstore: bool,
) -> None:
    kept, read, expunged = (seed(imap_server, login, "INBOX", mime(s)) for s in "abc")
    inbox = synced(db_session, mailboxes, user, account)
    if not condstore:
        # As after a sync on a server without CONDSTORE: every message's flags are compared.
        inbox.highest_modseq = None
        db_session.commit()
    fresh = seed(imap_server, login, "INBOX", mime("d"))
    with raw_client(imap_server, login) as client:
        client.select_folder("INBOX")
        client.add_flags([read], ["\\Seen", "$Done"])
        client.delete_messages([expunged])
        client.uid_expunge([expunged])

    assert not run_folder_sync(db_session, mailboxes, user.id, account, inbox.id)
    assert stored(db_session, inbox.id) == {
        kept: ("a", [], []),
        read: ("b", ["\\Seen"], ["$Done"]),
        fresh: ("d", [], []),
    }
    row = folder_row(db_session, user, "INBOX")
    assert (row.message_count, row.unread_count) == (3, 2)
    assert row.highest_modseq is not None


def test_a_sync_announces_its_changes_with_the_range_of_new_mail(
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    account: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    announced: list[tuple[int, tuple[int, int] | None]] = []

    def notify(_db: Session, _user: int, _account: int, folder_id: int, *, new: Any = None) -> None:
        announced.append((folder_id, new))

    monkeypatch.setattr(index_sync, "notify", notify)
    first = seed(imap_server, login, "INBOX", mime("a"))
    inbox = synced(db_session, mailboxes, user, account)
    # A first sync: the state and the rows change, and nothing is new mail.
    assert announced
    assert {new for _, new in announced} == {None}
    announced.clear()
    assert not run_folder_sync(db_session, mailboxes, user.id, account, inbox.id)
    assert announced == []

    fresh = [seed(imap_server, login, "INBOX", mime(s)) for s in "bc"]
    with raw_client(imap_server, login) as client:
        client.select_folder("INBOX")
        client.add_flags([first], ["\\Seen"])
    assert not run_folder_sync(db_session, mailboxes, user.id, account, inbox.id)
    assert announced == [(inbox.id, None), (inbox.id, (fresh[0], fresh[1]))]


def test_a_watched_inbox_queues_its_sync_on_new_mail_and_flag_changes(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    account: int,
) -> None:
    """The IDLE supervisor's watch on the indexed INBOX: a sync once it connects, then on each
    change the server announces."""
    first = seed(imap_server, login, "INBOX", mime("a"))
    account_folders_due(db_session, mailboxes, user.id, account, NOW)
    inbox_id = folder_row(db_session, user, "INBOX").id
    queued: list[int] = []
    heard = threading.Condition()

    def queue_folder_sync(_db: Session, _user: int, _account: int, folder_id: int) -> bool:
        with heard:
            queued.append(folder_id)
            heard.notify_all()
        return True

    def wait_for(count: int) -> None:
        with heard:
            assert heard.wait_for(lambda: len(queued) >= count, 20)

    monkeypatch.setattr(idle, "queue_folder_sync", queue_folder_sync)
    supervisor = idle.IdleSupervisor(sessions, mailboxes, rules=False)
    try:
        supervisor.reconcile()
        assert supervisor.watched == {idle.WatchedFolder(user.id, account, "INBOX")}
        wait_for(1)
        seed(imap_server, login, "INBOX", mime("b"))
        wait_for(2)
        with raw_client(imap_server, login) as client:
            client.select_folder("INBOX")
            client.add_flags([first], ["\\Seen"])
        wait_for(3)
    finally:
        supervisor.stop_all()
    assert set(queued) == {inbox_id}


def test_a_new_uidvalidity_rebuilds_the_folder(
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    account: int,
) -> None:
    with raw_client(imap_server, login) as client:
        client.create_folder("Box")
    for subject in ("one", "two"):
        seed(imap_server, login, "Box", mime(subject))
    box = synced(db_session, mailboxes, user, account, "Box")
    before = box.uid_validity
    with raw_client(imap_server, login) as client:
        client.delete_folder("Box")
        client.create_folder("Box")
    again = seed(imap_server, login, "Box", mime("three"))

    assert not run_folder_sync(db_session, mailboxes, user.id, account, box.id)
    row = folder_row(db_session, user, "Box")
    assert row.uid_validity != before
    assert (row.state, row.message_count) == ("ready", 1)
    assert stored(db_session, box.id) == {again: ("three", [], [])}


def test_the_age_limit_keeps_newer_mail_and_prunes_the_rest(
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    account: int,
) -> None:
    old = seed(
        imap_server,
        login,
        "INBOX",
        mime("old"),
        internal_date=datetime.now(UTC) - timedelta(days=90),
    )
    new = seed(imap_server, login, "INBOX", mime("new"), internal_date=datetime.now(UTC))
    inbox = synced(db_session, mailboxes, user, account)
    assert set(stored(db_session, inbox.id)) == {old, new}
    assert inbox.oldest_indexed is None

    user.index_max_age_days = 30
    db_session.commit()
    assert not run_folder_sync(db_session, mailboxes, user.id, account, inbox.id)
    assert set(stored(db_session, inbox.id)) == {new}
    row = folder_row(db_session, user, "INBOX")
    assert row.oldest_indexed == datetime.now(UTC).date() - timedelta(days=30)
    assert row.message_count == 1


def test_a_folder_switched_off_meanwhile_is_left_alone(
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    account: int,
) -> None:
    seed(imap_server, login, "INBOX", mime("a"))
    account_folders_due(db_session, mailboxes, user.id, account, NOW)
    inbox = folder_row(db_session, user, "INBOX")
    inbox.enabled = False
    db_session.commit()
    assert not run_folder_sync(db_session, mailboxes, user.id, account, inbox.id)
    assert stored(db_session, inbox.id) == {}


# A folder's sync over a fake server: what the core reads answer is set by each test.


class FakeServer:
    """The folder as the survey and the batches read it: its UIDVALIDITY, its UIDs, flags and
    (on Gmail) labels; `broken` UIDs raise when fetched, `stale` makes every fetch refused."""

    def __init__(self, uids: tuple[int, ...], uid_validity: int = 7) -> None:
        self.uids = uids
        self.uid_validity = uid_validity
        self.flags: dict[int, frozenset[str]] = {}
        self.labels: dict[int, tuple[str, ...]] | None = None
        self.broken: set[int] = set()
        self.stale = False
        self.flag_reads = 0

    def context(self) -> SimpleNamespace:
        return SimpleNamespace(uid_validity=self.uid_validity, uid_next=None, highest_modseq=None)

    def connections(self) -> MailConnections:
        return cast("MailConnections", SimpleNamespace(with_mailbox=self._with_mailbox))

    def _with_mailbox(
        self, _account: int, _path: str, _read_only: bool, fn: Any, *, reselect: bool = False
    ) -> Any:
        # The survey asks changes since the HIGHESTMODSEQ it reads: never a reused selection.
        assert reselect
        return fn(self.context())

    def plan(self, _context: object, *, since: object = None) -> IndexPlan:
        return IndexPlan(self.uid_validity, None, (tuple(sorted(self.uids, reverse=True)),))

    def every_flag(self, _context: object) -> FolderFlags:
        self.flag_reads += 1
        return FolderFlags(self.uid_validity, None, dict(self.flags), self.labels)

    def fetched(
        self, _connections: object, account: int, folder: str, uids: Any, *, uid_validity: int
    ) -> list[IndexedMessage]:
        if self.stale or uid_validity != self.uid_validity:
            raise MailError("stale_reference")
        if self.broken.intersection(uids):
            raise ValueError("the sender's text")
        return [
            IndexedMessage(
                account=account,
                folder=folder,
                uid_validity=uid_validity,
                uid=uid,
                date=None,
                received=NOW,
                sender=(),
                to=(),
                cc=(),
                subject=f"m{uid}",
                flags=self.flags.get(uid, frozenset()),
                size=1,
                has_attachments=False,
                message_id=None,
                in_reply_to=None,
                references_root=None,
                list_id=None,
                snippet=None,
                gmail_labels=() if self.labels is None else self.labels.get(uid, ()),
            )
            for uid in uids
        ]


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    fake = FakeServer(())
    monkeypatch.setattr(index_sync, "plan_index", fake.plan)
    monkeypatch.setattr(index_sync, "flags_of", fake.every_flag)
    monkeypatch.setattr(index_sync, "indexed_messages", fake.fetched)
    monkeypatch.setattr(index_sync, "BATCH_PAUSE_SECONDS", 0)
    return fake


def run(db: Session, server: FakeServer, user: User, account: int, folder_id: int) -> bool:
    return index_sync.sync_folder(db, server.connections(), user.id, account, folder_id, now=NOW)


def test_a_resync_brings_gmail_labels_up_to_date(
    user_client: TestClient, db_session: Session, user: User, server: FakeServer
) -> None:
    """A message archived in Gmail's own web page loses its \\Inbox label, nothing else."""
    work = add_account(user_client)
    folder_id = indexed(db_session, user.id, work, "[Gmail]/All Mail", uids=())
    db_session.add(message(folder_id, user.id, 1, labels=["\\Inbox", "Work"]))
    row = db_session.get(MailIndexFolder, folder_id)
    assert row is not None
    row.uid_validity = 7
    db_session.commit()
    server.uids = (1,)
    server.flags = {1: frozenset()}
    server.labels = {1: ("Work",)}

    assert not run(db_session, server, user, work, folder_id)
    db_session.expire_all()
    assert db_session.scalars(select(MailIndexMessage.labels)).all() == [["Work"]]


def test_a_sync_past_its_time_stores_a_batch_and_asks_for_another_job(
    user_client: TestClient,
    db_session: Session,
    user: User,
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(index_sync, "INDEX_BATCH", 2)
    work = add_account(user_client)
    folder_id = indexed(db_session, user.id, work, uids=())
    server.uids = (1, 2, 3)

    def share() -> bool:
        return index_sync.sync_folder(
            db_session, server.connections(), user.id, work, folder_id, now=NOW, until=0.0
        )

    assert share()
    assert set(stored(db_session, folder_id)) == {2, 3}
    assert not share()
    assert set(stored(db_session, folder_id)) == {1, 2, 3}
    assert folder_row(db_session, user, "INBOX").state == "ready"


@pytest.mark.parametrize("missing", [2, 8], ids=["a_batch", "several_batches"])
def test_a_pass_reads_the_flags_once_whatever_its_jobs(
    user_client: TestClient,
    db_session: Session,
    user: User,
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
    missing: int,
) -> None:
    """Only the job that ends a pass cut short by its time reads the flags, also of rows its
    earlier jobs stored."""
    monkeypatch.setattr(index_sync, "INDEX_BATCH", 2)
    work = add_account(user_client)
    folder_id = indexed(db_session, user.id, work, uids=())
    db_session.add(message(folder_id, user.id, 1))
    row = db_session.get(MailIndexFolder, folder_id)
    assert row is not None
    row.uid_validity = 7
    db_session.commit()
    server.uids = tuple(range(1, missing + 2))

    def share(until: float) -> bool:
        return index_sync.sync_folder(
            db_session, server.connections(), user.id, work, folder_id, now=NOW, until=until
        )

    for _ in range(missing // 2 - 1):
        assert share(0.0)
        assert server.flag_reads == 0
    server.flags = {1: frozenset({"\\Seen"}), missing + 1: frozenset({"\\Seen"})}
    assert not share(0.0)
    assert server.flag_reads == 1
    rows = stored(db_session, folder_id)
    assert {uid for uid, (_, flags, _) in rows.items() if flags} == {1, missing + 1}
    assert folder_row(db_session, user, "INBOX").state == "ready"


def test_a_job_that_fetches_several_batches_reads_the_flags_after_them(
    user_client: TestClient,
    db_session: Session,
    user: User,
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(index_sync, "INDEX_BATCH", 2)
    work = add_account(user_client)
    folder_id = indexed(db_session, user.id, work, uids=())
    db_session.add(message(folder_id, user.id, 1))
    row = db_session.get(MailIndexFolder, folder_id)
    assert row is not None
    row.uid_validity = 7
    db_session.commit()
    server.uids = (1, 2, 3, 4, 5, 6)
    server.flags = {1: frozenset({"\\Seen"})}

    assert not run(db_session, server, user, work, folder_id)
    assert server.flag_reads == 1
    assert stored(db_session, folder_id)[1][1] == ["\\Seen"]
    row = folder_row(db_session, user, "INBOX")
    assert (row.state, row.message_count, row.unread_count) == ("ready", 6, 5)


def test_the_age_limit_counts_days_in_the_users_time_zone(
    user_client: TestClient, db_session: Session, user: User, server: FakeServer
) -> None:
    """At 23:30 UTC it is already the next day in Berlin, so the age limit starts a day later."""
    late = datetime(2026, 11, 20, 23, 30, tzinfo=UTC)
    assert index_sync.since_day(30, late, TimeZoneName("UTC")) == date(2026, 10, 21)
    assert index_sync.since_day(30, late, TimeZoneName("Europe/Berlin")) == date(2026, 10, 22)
    assert index_sync.since_day(None, late, TimeZoneName("Europe/Berlin")) is None

    work = add_account(user_client)
    folder_id = indexed(db_session, user.id, work, uids=())
    user.index_max_age_days = 30
    user.time_zone = "Europe/Berlin"
    db_session.commit()
    assert not index_sync.sync_folder(
        db_session, server.connections(), user.id, work, folder_id, now=late
    )
    row = db_session.get(MailIndexFolder, folder_id)
    assert row is not None
    db_session.refresh(row)
    assert row.oldest_indexed == date(2026, 10, 22)


def test_a_message_that_cannot_be_read_holds_up_no_other(
    user_client: TestClient,
    db_session: Session,
    user: User,
    server: FakeServer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    work = add_account(user_client)
    folder_id = indexed(db_session, user.id, work, uids=())
    server.uids = (1, 2, 3)
    server.broken = {2}

    with caplog.at_level(logging.WARNING, logger=index_sync.logger.name):
        assert not run(db_session, server, user, work, folder_id)
    assert set(stored(db_session, folder_id)) == {1, 3}
    row = folder_row(db_session, user, "INBOX")
    assert (row.state, row.message_count) == ("ready", 2)
    [record] = [r for r in caplog.records if "skipped" in r.getMessage()]
    assert "ValueError" in record.getMessage()
    assert "the sender's text" not in caplog.text


def test_an_unexpected_failure_logs_its_type_only(
    user_client: TestClient,
    db_session: Session,
    user: User,
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    work = add_account(user_client)
    folder_id = indexed(db_session, user.id, work, uids=())

    def broken(_context: object, *, since: object = None) -> IndexPlan:
        raise RuntimeError("Subject: the sender's text")

    monkeypatch.setattr(index_sync, "plan_index", broken)
    with caplog.at_level(logging.INFO):
        assert not run(db_session, server, user, work, folder_id)
    row = folder_row(db_session, user, "INBOX")
    assert (row.state, row.error) == ("failed", index_sync.SYNC_FAILED)
    assert "RuntimeError" in caplog.text
    assert "the sender's text" not in caplog.text


def test_a_uidvalidity_changed_mid_fetch_asks_again_then_rebuilds(
    user_client: TestClient, db_session: Session, user: User, server: FakeServer
) -> None:
    work = add_account(user_client)
    folder_id = indexed(db_session, user.id, work, uids=())
    server.uids = (1, 2)
    server.stale = True
    assert run(db_session, server, user, work, folder_id)
    assert stored(db_session, folder_id) == {}

    server.stale = False
    server.uid_validity = 8
    assert not run(db_session, server, user, work, folder_id)
    row = folder_row(db_session, user, "INBOX")
    assert (row.state, row.uid_validity, row.message_count) == ("ready", 8, 2)


def test_an_account_that_cannot_log_in_fails_its_folder(
    user_client: TestClient,
    db_session: Session,
    user: User,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = add_account(user_client)
    folder_id = indexed(db_session, user.id, work, uids=())
    monkeypatch.setattr(
        worker_index, "unusable", lambda *_args: (PASSWORD_MISSING, "No password is stored.")
    )
    assert not run_folder_sync(db_session, mailboxes, user.id, work, folder_id)
    row = folder_row(db_session, user, "INBOX")
    assert (row.state, row.error) == ("failed", PASSWORD_MISSING)


def test_without_the_mail_client_no_index_job_runs(
    user_client: TestClient,
    db_session: Session,
    user: User,
    worker: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = add_account(user_client)
    folder_id = indexed(db_session, user.id, work, uids=())
    row = db_session.get(MailIndexFolder, folder_id)
    assert row is not None
    row.state = "pending"
    db_session.commit()
    monkeypatch.setenv("MAILROOM_FEATURES", "filters")
    tasks.sync_account(user.id, work)
    tasks.sync_folder(user.id, work, folder_id)
    tasks.sync_due_folders(user.id, work, [folder_id])
    assert jobs(db_session, tasks.SYNC_FOLDER) == []
    tasks.refresh_folders(user.id, work, [folder_id], {"r": [folder_id]})
    assert jobs(db_session, tasks.SYNC_DUE_FOLDERS) == []
    assert folder_row(db_session, user, "INBOX").state == "pending"


def test_without_filters_no_rule_set_job_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_context() -> WorkerContext:
        raise AssertionError("a filters job ran without filters")

    monkeypatch.setattr(tasks, "worker_context", no_context)
    monkeypatch.setenv("MAILROOM_FEATURES", "mail")
    tasks.run_rule_set(1)
    tasks.dispatch_scheduled(int(NOW.timestamp()))
