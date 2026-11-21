"""The folders desktop notifications come from (``docs/requirements/mail-client.md``, *Desktop
notifications*): INBOX of each account until the user chooses, a choice per account, only the
user's own accounts, following a folder renamed or deleted, and forgotten with the account."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.mail.notifications import forget_folder, rename_folder, set_folders
from mailroom.models import MailNotificationFolders
from tests.test_api_accounts import added, new_account
from tests.test_api_mail import MAIL

if TYPE_CHECKING:
    import pytest
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User

NOTIFICATIONS = f"{MAIL}/notifications"


def _put(client: TestClient, account_id: int, folders: list[str]) -> tuple[int, object]:
    response = client.put(f"{NOTIFICATIONS}/accounts/{account_id}", json={"folders": folders})
    return response.status_code, response.json()


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    assert api_client.get(NOTIFICATIONS).status_code == 401
    assert _put(api_client, 1, ["INBOX"])[0] == 401


def test_inbox_of_each_account_until_the_user_chooses(
    user_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    assert user_client.get(NOTIFICATIONS).json() == {"accounts": []}
    work = added(user_client, new_account("work"))
    home = added(user_client, new_account("home"))
    assert user_client.get(NOTIFICATIONS).json() == {
        "accounts": [
            {"account_id": work, "folders": ["INBOX"]},
            {"account_id": home, "folders": ["INBOX"]},
        ]
    }

    caplog.set_level(logging.INFO)
    status, body = _put(user_client, work, ["inbox", "Lists/Secret", "INBOX", "Lists/Secret"])
    assert (status, body) == (200, {"account_id": work, "folders": ["INBOX", "Lists/Secret"]})
    assert _put(user_client, home, []) == (200, {"account_id": home, "folders": []})
    assert user_client.get(NOTIFICATIONS).json() == {
        "accounts": [
            {"account_id": work, "folders": ["INBOX", "Lists/Secret"]},
            {"account_id": home, "folders": []},
        ]
    }
    assert "Lists/Secret" not in caplog.text


def test_only_the_users_own_accounts(
    api_client: TestClient, db_session: Session, user: User, admin: User
) -> None:
    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, admin))
    others = added(api_client, new_account("admin"))
    user_client = api_client
    user_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, user))
    status, body = _put(user_client, others, ["INBOX"])
    assert status == 404
    assert isinstance(body, dict)
    assert body["detail"]["code"] == "not_found"
    assert db_session.scalars(select(MailNotificationFolders)).all() == []
    assert _put(user_client, others, [""])[0] == 422
    assert _put(user_client, others, ["INBOX"] * 201)[0] == 422


def test_forgotten_with_the_account(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = added(user_client, new_account("work"))
    _put(user_client, work, ["Archive"])
    assert user_client.delete(f"/api/v1/accounts/{work}").status_code == 204
    rows = db_session.scalars(
        select(MailNotificationFolders).where(MailNotificationFolders.user_id == user.id)
    )
    assert rows.all() == []


def _chosen(db_session: Session, user_id: int, account_id: int) -> list[str] | None:
    db_session.expire_all()
    row = db_session.get(MailNotificationFolders, (user_id, account_id))
    return None if row is None else row.folders


def test_the_choice_follows_a_rename_and_a_delete(
    db_session: Session, user: User, admin: User
) -> None:
    chosen = ["INBOX", "Old", "Old.Sub", "Older", "Gone", "Gone.In", "Gonely"]
    set_folders(db_session, user.id, 1, chosen)
    set_folders(db_session, user.id, 2, ["Old"])
    set_folders(db_session, admin.id, 1, ["Old"])

    rename_folder(db_session, user.id, 1, "Old", "Older.New", ".")
    assert _chosen(db_session, user.id, 1) == [
        "INBOX",
        "Older.New",
        "Older.New.Sub",
        "Older",
        "Gone",
        "Gone.In",
        "Gonely",
    ]
    forget_folder(db_session, user.id, 1, "Gone", ".")
    assert _chosen(db_session, user.id, 1) == [
        "INBOX",
        "Older.New",
        "Older.New.Sub",
        "Older",
        "Gonely",
    ]
    assert _chosen(db_session, user.id, 2) == ["Old"]
    assert _chosen(db_session, admin.id, 1) == ["Old"]

    set_folders(db_session, user.id, 1, ["INBOX", "Inbox/Lists", "Flat"])
    rename_folder(db_session, user.id, 1, "INBOX/Lists", "Lists", "/")
    rename_folder(db_session, user.id, 1, "Flat", "Plain", None)
    assert _chosen(db_session, user.id, 1) == ["INBOX", "Lists", "Plain"]

    rename_folder(db_session, user.id, 3, "Old", "New", "/")
    forget_folder(db_session, user.id, 3, "Old", "/")
    assert _chosen(db_session, user.id, 3) is None
