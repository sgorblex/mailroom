"""The mail index kept in step with the app's own writes (``mailroom.mail.index.writes``): rows
moved, copied and flagged from a write's answer, the folders left unsure queued for a sync, and,
against the test Dovecot (``IMAP_TEST_HOST``; skipped without it), a move, a flag change and a
folder marked read through the API."""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, Any, cast

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from mailroom.mail.index import writes
from mailroom.models import MailIndexFolder
from mailroom.worker import tasks
from mailroom.worker.app import PROCRASTINATE_SCHEMA
from mailroom_core.mail import AuditItem, GroupResult, MailError, MessageRef, format_ref
from mailroom_core.testing import mime, raw_client, seed
from tests.test_filter_runner import add_account
from tests.test_mail_index_rows import message
from tests.test_mail_index_sync import folder_row, jobs, stored, synced

if TYPE_CHECKING:
    import psycopg
    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import sessionmaker
    from starlette.testclient import TestClient

    from mailroom.models import User
    from mailroom.worker.context import Mailboxes
    from mailroom_core.testing import ImapTestServer

VALIDITY = 7


@pytest.fixture(autouse=True)
def _every_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAILROOM_FEATURES", raising=False)


@pytest.fixture
def account(user_client: TestClient) -> int:
    return add_account(user_client)


def folder(
    db: Session,
    user: User,
    account: int,
    path: str,
    uids: tuple[int, ...] = (),
    **changes: Any,
) -> int:
    """An indexed, ready folder under ``VALIDITY`` holding ``uids``, each unread and subject
    ``s<uid>``."""
    fields: dict[str, Any] = {"uid_validity": VALIDITY, "state": "ready", "enabled": True}
    row = MailIndexFolder(user_id=user.id, account_id=account, path=path, **(fields | changes))
    db.add(row)
    db.flush()
    db.add_all(message(row.id, user.id, uid, subject=f"s{uid}") for uid in uids)
    db.commit()
    return row.id


def ref(account: int, path: str, uid: int, validity: int = VALIDITY) -> str:
    return format_ref(MessageRef(account, path, validity, uid))


def moved(account: int, path: str, uid: int, destination: str, **changes: Any) -> AuditItem:
    return AuditItem(
        account=account, folder=path, uid=uid, destination=destination, result="ok", **changes
    )


def queued(db: Session) -> list[int]:
    return sorted(job["folder_id"] for job in jobs(db, tasks.SYNC_FOLDER))


def subjects(db: Session, folder_id: int) -> dict[int, str]:
    return {uid: subject for uid, (subject, _, _) in stored(db, folder_id).items()}


def counts(db: Session, folder_id: int) -> tuple[int, int]:
    db.expire_all()
    row = db.get(MailIndexFolder, folder_id)
    assert row is not None
    return row.message_count, row.unread_count


def test_a_move_takes_the_rows_to_their_new_uids(
    db_session: Session,
    sessions: sessionmaker[Session],
    user: User,
    account: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = folder(db_session, user, account, "INBOX", (1, 2, 3))
    archive = folder(db_session, user, account, "Archive", (10,))
    announced: list[tuple[int, int, int]] = []
    monkeypatch.setattr(writes, "notify", lambda _db, *ids: announced.append(ids))
    refs = [ref(account, "INBOX", 3), ref(account, "INBOX", 1)]
    group = GroupResult(
        account,
        "INBOX",
        count=2,
        new_refs=(ref(account, "Archive", 11), ref(account, "Archive", 12)),
        destination="Archive",
    )
    items = [moved(account, "INBOX", uid, "Archive") for uid in (3, 1)]

    writes.organized(sessions, user.id, items, [group], refs=refs)

    assert subjects(db_session, inbox) == {2: "s2"}
    assert subjects(db_session, archive) == {10: "s10", 11: "s1", 12: "s3"}
    assert counts(db_session, inbox) == (1, 1)
    assert counts(db_session, archive) == (3, 3)
    assert queued(db_session) == []
    assert sorted(announced) == sorted([(user.id, account, inbox), (user.id, account, archive)])


def test_what_the_answer_leaves_unsure_is_synced_again(
    db_session: Session, sessions: sessionmaker[Session], user: User, account: int
) -> None:
    """No COPYUID: the destination is synced. A ref under another UIDVALIDITY: its folder's rows
    stay, the folder is synced. A failed group: both its folders are synced."""
    inbox = folder(db_session, user, account, "INBOX", (1, 2))
    archive = folder(db_session, user, account, "Archive")
    old = folder(db_session, user, account, "Old", (5,))
    trash = folder(db_session, user, account, "Trash")
    folder(db_session, user, account, "Quiet")
    groups = [
        GroupResult(account, "INBOX", count=1, new_refs=None, destination="Archive"),
        GroupResult(
            account, "Old", count=1, new_refs=(ref(account, "Trash", 1),), destination="Trash"
        ),
    ]
    items = [moved(account, "INBOX", 1, "Archive"), moved(account, "Old", 5, "Trash")]
    refs = [ref(account, "INBOX", 1), ref(account, "Old", 5, validity=VALIDITY + 1)]
    writes.organized(sessions, user.id, items, groups, refs=refs)

    assert subjects(db_session, inbox) == {2: "s2"}
    assert subjects(db_session, old) == {5: "s5"}
    assert subjects(db_session, trash) == {}
    assert queued(db_session) == sorted([archive, old, trash])

    failed = GroupResult(account, "INBOX", error=MailError("server_error"))
    writes.organized(
        sessions,
        user.id,
        [AuditItem(account=account, folder="INBOX", uid=2, result="server_error")],
        [failed],
        refs=[ref(account, "INBOX", 2)],
    )
    assert subjects(db_session, inbox) == {2: "s2"}
    assert inbox in queued(db_session)


def test_a_flag_change_sets_the_flags_asked(
    db_session: Session, sessions: sessionmaker[Session], user: User, account: int
) -> None:
    inbox = folder(db_session, user, account, "INBOX", (1, 2, 3))
    items = [
        AuditItem(account=account, folder="INBOX", uid=uid, seen=True, to_sort=True, result="ok")
        for uid in (1, 2)
    ]
    writes.organized(
        sessions,
        user.id,
        items,
        [GroupResult(account, "INBOX", count=2, new_refs=())],
        refs=[ref(account, "INBOX", uid) for uid in (1, 2)],
    )
    assert stored(db_session, inbox) == {
        1: ("s1", ["\\Seen"], ["$MailroomSort"]),
        2: ("s2", ["\\Seen"], ["$MailroomSort"]),
        3: ("s3", [], []),
    }
    assert counts(db_session, inbox) == (3, 1)

    unread = AuditItem(account=account, folder="INBOX", uid=1, seen=False, result="ok")
    writes.organized(sessions, user.id, [unread], [], refs=[ref(account, "INBOX", 1)])
    assert stored(db_session, inbox)[1] == ("s1", [], ["$MailroomSort"])
    assert queued(db_session) == []


def test_a_bulk_call_changes_the_rows_and_syncs_them_again(
    db_session: Session, sessions: sessionmaker[Session], user: User, account: int
) -> None:
    inbox = folder(db_session, user, account, "INBOX", (1, 2))
    items = [AuditItem(account=account, folder="INBOX", uid=1, flagged=True, result="ok")]
    writes.organized(sessions, user.id, items, [GroupResult(account, "INBOX", count=1)])
    assert stored(db_session, inbox)[1] == ("s1", ["\\Flagged"], [])
    assert queued(db_session) == [inbox]


def test_a_write_while_its_folder_syncs_syncs_it_again(
    db_session: Session, sessions: sessionmaker[Session], user: User, account: int
) -> None:
    """The running sync may have read the flags before the write, and would store them over
    it: another sync puts them right."""
    inbox = folder(db_session, user, account, "INBOX", (1,))
    other = folder(db_session, user, account, "Other", (1,))
    for folder_id in (inbox, other):
        tasks.queue_folder_sync(db_session, user.id, account, folder_id)
    # As a worker would: procrastinate's triggers name its types unqualified.
    path = db_session.execute(text("SELECT current_setting('search_path')")).scalar_one()
    db_session.execute(text(f"SET LOCAL search_path = {PROCRASTINATE_SCHEMA}, {path}"))
    db_session.execute(
        text(
            "UPDATE procrastinate_jobs SET status = 'doing'"
            " WHERE task_name = :name AND (args->>'folder_id')::bigint = :id"
        ),
        {"name": tasks.SYNC_FOLDER, "id": inbox},
    )
    db_session.commit()
    item = AuditItem(account=account, folder="INBOX", uid=1, seen=True, result="ok")
    writes.organized(
        sessions,
        user.id,
        [item],
        [GroupResult(account, "INBOX", count=1, new_refs=())],
        refs=[ref(account, "INBOX", 1)],
    )
    assert stored(db_session, inbox)[1] == ("s1", ["\\Seen"], [])
    assert queued(db_session) == sorted([inbox, other])


def test_a_copy_older_than_the_destinations_age_limit_is_left_to_its_sync(
    db_session: Session, sessions: sessionmaker[Session], user: User, account: int
) -> None:
    inbox = folder(db_session, user, account, "INBOX", (1,))
    recent = folder(db_session, user, account, "Recent", oldest_indexed=date(2026, 12, 1))
    group = GroupResult(
        account, "INBOX", count=1, new_refs=(ref(account, "Recent", 4),), destination="Recent"
    )
    writes.organized(
        sessions,
        user.id,
        [moved(account, "INBOX", 1, "Recent")],
        [group],
        refs=[ref(account, "INBOX", 1)],
    )
    assert subjects(db_session, inbox) == {}
    assert subjects(db_session, recent) == {}
    assert queued(db_session) == [recent]


def test_a_move_to_another_account_syncs_its_destination_and_trash(
    user_client: TestClient,
    db_session: Session,
    sessions: sessionmaker[Session],
    user: User,
    account: int,
) -> None:
    home = add_account(user_client, "home")
    inbox = folder(db_session, user, account, "INBOX", (1,))
    trash = folder(db_session, user, account, "Trash")
    target = folder(db_session, user, home, "INBOX")
    items = [
        moved(account, "INBOX", 1, "INBOX", destination_account=home, removed="trashed"),
        AuditItem(
            account=home,
            folder="INBOX",
            uid=9,
            source_account=account,
            source_folder="INBOX",
            result="ok",
        ),
    ]
    group = GroupResult(
        account,
        "INBOX",
        count=1,
        new_refs=(ref(home, "INBOX", 9),),
        destination="INBOX",
        destination_account=home,
        trash="Trash",
    )
    writes.organized(sessions, user.id, items, [group], refs=[ref(account, "INBOX", 1)])
    assert subjects(db_session, inbox) == {}
    assert subjects(db_session, target) == {}
    assert queued(db_session) == sorted([trash, target])


def test_a_folder_without_a_row_syncs_the_gmail_folder_holding_its_mail(
    user_client: TestClient,
    db_session: Session,
    sessions: sessionmaker[Session],
    user: User,
    account: int,
) -> None:
    """INBOX of a Gmail account is read from All Mail, whose rows carry X-GM-MSGID; another
    account without such rows queues nothing."""
    all_mail = folder(db_session, user, account, "[Gmail]/All Mail")
    db_session.add(message(all_mail, user.id, 1, gm_msgid=42))
    other = add_account(user_client, "home")
    folder(db_session, user, other, "Archive", (1,))
    db_session.commit()
    items = [
        AuditItem(account=account, folder="INBOX", uid=3, seen=True, result="ok"),
        AuditItem(account=other, folder="INBOX", uid=3, seen=True, result="ok"),
    ]
    refs = [ref(account, "INBOX", 3), ref(other, "INBOX", 3)]
    writes.organized(sessions, user.id, items, [], refs=refs)
    assert queued(db_session) == [all_mail]


def test_a_folder_off_the_index_is_left_alone(
    db_session: Session, sessions: sessionmaker[Session], user: User, account: int
) -> None:
    junk = folder(db_session, user, account, "Junk", (1,), enabled=False)
    writes.organized(
        sessions,
        user.id,
        [AuditItem(account=account, folder="Junk", uid=1, result="server_error")],
        [],
        refs=[ref(account, "Junk", 1)],
    )
    assert subjects(db_session, junk) == {1: "s1"}
    assert queued(db_session) == []


def test_a_folder_marked_read_is_read_below_its_uidnext(
    db_session: Session, sessions: sessionmaker[Session], user: User, account: int
) -> None:
    inbox = folder(db_session, user, account, "INBOX", (1, 2, 3))
    options = {"uid_validity": VALIDITY, "uid_next": 3}
    writes.marked_read(sessions, user.id, account, "INBOX", done=True, **options)
    flags = {uid: system for uid, (_, system, _) in stored(db_session, inbox).items()}
    assert flags == {1: ["\\Seen"], 2: ["\\Seen"], 3: []}
    assert counts(db_session, inbox) == (3, 1)
    assert queued(db_session) == []

    writes.marked_read(sessions, user.id, account, "INBOX", done=False, **options)
    assert queued(db_session) == [inbox]


def test_an_index_write_that_fails_leaves_the_call_alone(
    user: User, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> Any:
        message = "the subject: secret"
        raise RuntimeError(message)

    item = AuditItem(account=1, folder="INBOX", uid=1, seen=True, result="ok")
    writes.organized(broken, user.id, [item], [], refs=[])  # type: ignore[arg-type]
    [record] = [r for r in caplog.records if r.msg == "mail_index_write_failed"]
    assert record.__dict__["error"] == "RuntimeError"
    assert "secret" not in str(record.__dict__)


def test_a_change_is_announced_by_ids_once_committed(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as listening:
        listening.execute(text(f"LISTEN {writes.NOTIFY_CHANNEL}"))
        listening.commit()
        with Session(migrated_engine) as db:
            writes.notify(db, 424_242, 2, 3)
            db.commit()
        raw = cast("psycopg.Connection[Any]", listening.connection.driver_connection)
        # Other tests' writes may announce on the same channel: wait for this one.
        announced = next(
            n for n in raw.notifies(timeout=5) if json.loads(n.payload).get("user_id") == 424_242
        )
    assert announced.channel == "mailroom_index"
    assert json.loads(announced.payload) == {"user_id": 424_242, "account_id": 2, "folder_id": 3}


# Through the API, against the test Dovecot.


@pytest.fixture
def login(imap_server: ImapTestServer) -> str:
    return imap_server.unique_user("writes")


@pytest.fixture
def mail_account(
    user_client: TestClient,
    imap_server: ImapTestServer,
    login: str,
    application: FastAPI,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> int:
    """The user's account on a fresh Dovecot user, the app's index writes in the test's
    transaction."""
    from tests.support.mail import mail_servers_ssl_context
    from tests.test_api_accounts import added, new_account

    monkeypatch.setattr(application.state, "mail_ssl_context", mail_servers_ssl_context())
    monkeypatch.setattr(application.state, "session_maker", sessions)
    endpoint = {"host": imap_server.host, "port": imap_server.port, "tls": "implicit"}
    return added(
        user_client, new_account(login=login, password=imap_server.password, imap=endpoint)
    )


def test_the_apis_writes_reach_the_index(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    mail_account: int,
) -> None:
    with raw_client(imap_server, login) as client:
        client.create_folder("Done")
    first, second, third = (seed(imap_server, login, "INBOX", mime(s)) for s in ("a", "b", "c"))
    inbox = synced(db_session, mailboxes, user, mail_account)
    done = synced(db_session, mailboxes, user, mail_account, "Done")
    mail = "/api/v1/mail"
    refs = [ref(mail_account, "INBOX", uid, inbox.uid_validity or 0) for uid in (first, second)]

    flagged = user_client.post(
        f"{mail}/messages/flag", json={"refs": refs[:1], "flagged": True, "seen": True}
    )
    assert flagged.status_code == 200, flagged.text
    assert stored(db_session, inbox.id)[first] == ("a", ["\\Flagged", "\\Seen"], [])

    response = user_client.post(f"{mail}/messages/move", json={"refs": refs, "folder": "Done"})
    assert response.status_code == 200, response.text
    [group] = response.json()["results"]
    assert len(group["new_refs"]) == 2
    assert set(stored(db_session, inbox.id)) == {third}
    in_done = stored(db_session, done.id)
    assert sorted(subject for subject, _, _ in in_done.values()) == ["a", "b"]
    assert ("a", ["\\Flagged", "\\Seen"], []) in in_done.values()
    assert folder_row(db_session, user, "Done").message_count == 2

    listed = user_client.get(f"{mail}/accounts/{mail_account}/folders").json()
    box = next(f for f in listed["folders"] if f["path"] == "Done")
    marked = user_client.post(
        f"{mail}/accounts/{mail_account}/folders/mark-read",
        json={"folder": "Done", "uid_validity": box["uid_validity"], "uid_next": box["uid_next"]},
    )
    assert marked.status_code == 200, marked.text
    assert folder_row(db_session, user, "Done").unread_count == 0
    assert jobs(db_session, tasks.SYNC_FOLDER) == []


def test_a_move_to_another_account_syncs_the_trash_its_originals_went_to(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    mail_account: int,
) -> None:
    from tests.test_api_accounts import added, new_account

    endpoint = {"host": imap_server.host, "port": imap_server.port, "tls": "implicit"}
    home_login = imap_server.unique_user("writes-home")
    home = added(
        user_client,
        new_account("home", login=home_login, password=imap_server.password, imap=endpoint),
    )
    uid = seed(imap_server, login, "INBOX", mime("away"))
    inbox = synced(db_session, mailboxes, user, mail_account)
    trash = folder_row(db_session, user, "Trash")

    response = user_client.post(
        "/api/v1/mail/messages/move-to-account",
        json={
            "refs": [ref(mail_account, "INBOX", uid, inbox.uid_validity or 0)],
            "account": home,
            "folder": "INBOX",
            "removal": "trash",
        },
    )
    assert response.status_code == 200, response.text
    assert stored(db_session, inbox.id) == {}
    assert trash.id in queued(db_session)


def test_a_stale_ref_keeps_the_rows_and_syncs_the_folder(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    mail_account: int,
) -> None:
    with raw_client(imap_server, login) as client:
        client.create_folder("Done")
    uid = seed(imap_server, login, "INBOX", mime("a"))
    inbox = synced(db_session, mailboxes, user, mail_account)
    stale = ref(mail_account, "INBOX", uid, (inbox.uid_validity or 0) + 1)

    response = user_client.post(
        "/api/v1/mail/messages/move", json={"refs": [stale], "folder": "Done"}
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "stale_reference"
    assert set(stored(db_session, inbox.id)) == {uid}
    assert queued(db_session) == [inbox.id]


def test_a_bulk_move_reaches_the_index(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    mail_account: int,
) -> None:
    with raw_client(imap_server, login) as client:
        client.create_folder("Done")
    for subject in ("a", "b"):
        seed(imap_server, login, "INBOX", mime(subject))
    inbox = synced(db_session, mailboxes, user, mail_account)
    done = synced(db_session, mailboxes, user, mail_account, "Done")

    response = user_client.post(
        "/api/v1/mail/messages/bulk",
        json={
            "account": mail_account,
            "folder": "INBOX",
            "action": {"kind": "move", "folder": "Done"},
            "expected": 2,
        },
    )
    assert response.status_code == 200, response.text
    assert stored(db_session, inbox.id) == {}
    assert sorted(subject for subject, _, _ in stored(db_session, done.id).values()) == ["a", "b"]
    assert set(queued(db_session)) >= {inbox.id, done.id}
