"""Refreshing what a list shows (``docs/requirements/mail-client.md``, *Index*, *Refresh*): which
indexed folders are queued for a folder, a search, a view and a Gmail label, only the user's own,
ahead of the mailbox's other jobs; whether it still runs; its limit per user."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.auth.users import create_user
from mailroom.mail.service import REFRESHES_PER_MINUTE, refresh_limit
from mailroom.worker import tasks
from mailroom.worker.app import PROCRASTINATE_SCHEMA
from mailroom_core.testing import create_folders
from tests.test_api_accounts import added, new_account
from tests.test_api_mail import MAIL, Mailbox
from tests.test_api_mail import mailbox as mailbox  # the fixture
from tests.test_mail_index_reads import ALL_MAIL, ready
from tests.test_mail_index_rows import message

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User

REFRESH = f"{MAIL}/refresh"


@pytest.fixture(autouse=True)
def _a_limit_of_its_own(application: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(application.state, "refresh_limit", refresh_limit())


def ask(client: TestClient, **body: Any) -> str | None:
    response = client.post(REFRESH, json=body)
    assert response.status_code == 202, response.text
    asked: str | None = response.json()["refresh_id"]
    return asked


def refused(client: TestClient, **body: Any) -> tuple[int, str]:
    response = client.post(REFRESH, json=body)
    detail = response.json()["detail"]
    return response.status_code, detail["code"] if isinstance(detail, dict) else detail


def queued(db: Session) -> list[tuple[dict[str, Any], int, str]]:
    """The refreshes queued: their arguments, priority and lock, oldest first."""
    query = text(
        f"SELECT args, priority, lock FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs"
        " WHERE task_name = :name AND status = 'todo' ORDER BY id"
    )
    return [(r[0], r[1], r[2]) for r in db.execute(query, {"name": tasks.REFRESH_FOLDERS})]


def folders_queued(db: Session) -> dict[int, list[int]]:
    """The folders queued, by account."""
    return {args["account_id"]: args["folder_ids"] for args, _, _ in queued(db)}


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    assert api_client.post(REFRESH, json={"account": 1}).status_code == 401
    assert api_client.get(f"{REFRESH}/{uuid.uuid4()}").status_code == 401


def test_a_folder_is_synced_ahead_and_the_refresh_followed_to_its_end(
    user_client: TestClient, db_session: Session, user: User, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    work = added(user_client, new_account("work"))
    secret = ready(db_session, user, work, "Secret")
    ready(db_session, user, work, "Other")
    asked = ask(user_client, account=work, folder="Secret")
    assert queued(db_session) == [
        (
            {
                "user_id": user.id,
                "account_id": work,
                "folder_ids": [secret],
                "refreshes": {asked: [secret]},
            },
            tasks.REFRESH_PRIORITY,
            tasks.mailbox_lock(user.id, work),
        )
    ]
    assert "mail_refresh_asked" in caplog.text
    assert "Secret" not in caplog.text
    assert user_client.get(f"{REFRESH}/{asked}").json() == {"running": True}
    with tasks.procrastinate_sql(db_session):
        db_session.execute(text("UPDATE procrastinate_jobs SET status = 'succeeded'"))
    db_session.commit()
    assert user_client.get(f"{REFRESH}/{asked}").json() == {"running": False}
    assert user_client.get(f"{REFRESH}/{uuid.uuid4()}").json() == {"running": False}
    assert user_client.get(f"{REFRESH}/x").status_code == 422


def test_a_folder_off_the_index_queues_nothing(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = added(user_client, new_account("work"))
    ready(db_session, user, work, "Off", enabled=False)
    assert ask(user_client, account=work, folder="Off") is None
    assert ask(user_client, account=work, folder="Unknown") is None
    assert queued(db_session) == []


def test_a_gmail_folder_syncs_all_mail(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    gmail = added(user_client, new_account("gmail"))
    all_mail = ready(db_session, user, gmail, ALL_MAIL)
    db_session.add(message(all_mail, user.id, 1, gm_msgid=5, labels=["\\Inbox"]))
    db_session.commit()
    ask(user_client, account=gmail)
    assert folders_queued(db_session) == {gmail: [all_mail]}


def test_a_search_syncs_its_scope_inbox_first(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = added(user_client, new_account("work"))
    home = added(user_client, new_account("home", email="me@home.example"))
    archive = ready(db_session, user, work, "Archive")
    inbox = ready(db_session, user, work, "INBOX")
    ready(db_session, user, work, "Off", enabled=False)
    other = ready(db_session, user, home, "INBOX")
    ask(user_client, account="*", folder="inbox")
    assert folders_queued(db_session) == {work: [inbox], home: [other]}
    # One refresh, a job per mailbox.
    assert len({key for args, _, _ in queued(db_session) for key in args["refreshes"]}) == 1
    ask(user_client, account=work, folder="*")
    assert folders_queued(db_session)[work] == [inbox, archive]


def test_a_view_syncs_its_folders(user_client: TestClient, db_session: Session, user: User) -> None:
    work = added(user_client, new_account("work"))
    inbox = ready(db_session, user, work, "INBOX")
    lists = ready(db_session, user, work, "Lists")
    created = [
        user_client.post(f"{MAIL}/views", json=body).json()["id"]
        for body in (
            {"name": "Lists", "sources": [{"account_id": work, "folder": "Lists"}]},
            {"name": "Unread", "query": "is:unread in:*"},
        )
    ]
    asked = [ask(user_client, view=view) for view in ("all_inboxes", *created)]
    [(args, _, _)] = queued(db_session)
    assert args["refreshes"] == dict(zip(asked, ([inbox], [lists], [inbox, lists]), strict=True))


def test_refreshes_waiting_are_one_job_per_mailbox(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    """Pressed again and again, on whatever lists, it adds no job; the last asked goes first."""
    work = added(user_client, new_account("work"))
    home = added(user_client, new_account("home", email="me@home.example"))
    inbox = ready(db_session, user, work, "INBOX")
    lists = ready(db_session, user, work, "Lists")
    other = ready(db_session, user, home, "INBOX")
    first = ask(user_client, view="all_inboxes")
    assert ask(user_client, view="all_inboxes") == first
    second = ask(user_client, account=work, folder="Lists")
    assert second != first
    assert folders_queued(db_session) == {work: [lists, inbox], home: [other]}
    assert len(queued(db_session)) == 2

    # Home's is done: both still wait for work's.
    with tasks.procrastinate_sql(db_session):
        db_session.execute(
            text(
                "UPDATE procrastinate_jobs SET status = 'succeeded'"
                " WHERE task_name = :name AND (args->>'account_id')::bigint = :home"
            ),
            {"name": tasks.REFRESH_FOLDERS, "home": home},
        )
    db_session.commit()
    for asked in (first, second):
        assert user_client.get(f"{REFRESH}/{asked}").json() == {"running": True}


def test_all_inboxes_syncs_the_subfolders_it_takes_in(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = added(user_client, new_account("work"))
    lists = ready(db_session, user, work, "INBOX/Lists", inbox_subfolder=True)
    inbox = ready(db_session, user, work, "INBOX")
    ready(db_session, user, work, "INBOX/Off", inbox_subfolder=True, enabled=False)
    ask(user_client, view="all_inboxes", subfolders=True)
    assert folders_queued(db_session) == {work: [inbox, lists]}
    view = user_client.post(f"{MAIL}/views", json={"name": "U", "query": "is:unread"}).json()["id"]
    assert refused(user_client, view=view, subfolders=True) == (400, "invalid_argument")


def test_only_the_users_own_accounts_views_and_refreshes(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = added(user_client, new_account("work"))
    ready(db_session, user, work, "INBOX")
    view = user_client.post(f"{MAIL}/views", json={"name": "U", "query": "is:unread"}).json()["id"]
    asked = ask(user_client, account=work)

    token = create_session(db_session, create_user(db_session, "other", "a long enough password"))
    db_session.commit()
    user_client.cookies.set(SESSION_COOKIE_NAME, token)
    assert refused(user_client, account=work) == (404, "not_found")
    assert refused(user_client, view=view)[0] == 404
    assert ask(user_client, view="all_inboxes") is None
    assert ask(user_client, account="*", folder="*") is None
    assert user_client.get(f"{REFRESH}/{asked}").json() == {"running": False}
    assert len(queued(db_session)) == 1


def test_what_is_not_a_list_or_a_view_is_refused(user_client: TestClient) -> None:
    work = added(user_client, new_account("work"))
    assert refused(user_client, account=work, folder="*", subfolders=True) == (
        400,
        "invalid_argument",
    )
    for body in (
        {},
        {"account": 0},
        {"account": work, "folder": ""},
        {"account": work, "view": "all_inboxes"},
        {"view": "other"},
        {"view": "all_inboxes", "other": 1},
    ):
        assert user_client.post(REFRESH, json=body).status_code == 422, body


def test_refreshes_are_limited_per_user(user_client: TestClient, db_session: Session) -> None:
    work = added(user_client, new_account("work"))
    for _ in range(REFRESHES_PER_MINUTE):
        ask(user_client, account=work)
    assert refused(user_client, account=work) == (429, "refresh_limited")
    # Another user's are their own.
    token = create_session(db_session, create_user(db_session, "other", "a long enough password"))
    db_session.commit()
    user_client.cookies.set(SESSION_COOKIE_NAME, token)
    assert ask(user_client, view="all_inboxes") is None


def test_a_folder_with_its_subfolders(mailbox: Mailbox, db_session: Session, user: User) -> None:
    create_folders(mailbox.server, mailbox.login, "Projects")
    listed = mailbox.get("/accounts/1/folders")["folders"]
    delimiter = next(f["delimiter"] for f in listed if f["path"] == "INBOX")
    create_folders(mailbox.server, mailbox.login, f"Projects{delimiter}A", "Projects-not")
    ids = {
        path: ready(db_session, user, 1, path)
        for path in ("Projects", f"Projects{delimiter}A", "Projects-not")
    }
    ask(mailbox.client, account=1, folder="Projects", subfolders=True)
    wanted = sorted([ids["Projects"], ids[f"Projects{delimiter}A"]])
    assert folders_queued(db_session) == {1: wanted}
    missing = refused(mailbox.client, account=1, folder="Nowhere", subfolders=True)
    assert missing == (404, "mailbox_missing")
