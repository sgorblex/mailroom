from __future__ import annotations

import logging
import os
import ssl
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

import pytest
from imapclient import IMAPClient
from sqlalchemy import text

from mailroom.accounts import service as accounts_service
from mailroom.accounts.mail import MailFailure, mail_ssl_context
from mailroom.accounts.store import load_accounts, user_store
from mailroom.api.routes import accounts as accounts_routes
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.auth.users import create_user
from mailroom.models import User
from mailroom_core.crypto import open_secret
from mailroom_core.store import RevisionConflict, SettingsBusy, forget_user
from tests.conftest import PASSWORD

if TYPE_CHECKING:
    from fastapi import FastAPI
    from imap_thingy.accounts import Account
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.testing import ImapTestServer


MAILBOX_PASSWORD = "mailbox-secret-3141"


def new_account(account_id: str = "work", **changes: Any) -> dict[str, Any]:
    account: dict[str, Any] = {
        "id": account_id,
        "login": f"{account_id}@example.org",
        "password": MAILBOX_PASSWORD,
        "send_mode": "drafts",
        "imap": {"host": "127.0.0.1", "port": 9, "tls": "implicit"},
        "smtp": {"host": "127.0.0.1", "port": 9, "tls": "implicit"},
        "identities": [{"address": f"{account_id}@example.org", "name": "Alice"}],
    }
    return account | changes


def edited(account: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in account.items() if key not in {"id", "password"}}


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/accounts").status_code == 401
    assert api_client.post("/api/v1/accounts", json=new_account()).status_code == 401
    assert api_client.post("/api/v1/accounts/work/test").status_code == 401


def test_a_user_adds_edits_and_removes_an_account(
    user_client: TestClient,
    user: User,
    migrated_engine: Engine,
    secrets_subkey: SecretsSubkey,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    assert user_client.get("/api/v1/accounts").json() == []

    response = user_client.post("/api/v1/accounts", json=new_account())
    assert response.status_code == 201
    added = response.json()
    assert added == {
        "id": "work",
        "login": "work@example.org",
        "send_mode": "drafts",
        "imap": {"host": "127.0.0.1", "port": 9, "tls": "implicit"},
        "smtp": {"host": "127.0.0.1", "port": 9, "tls": "implicit"},
        # A lone identity is the default one, as mailroom-core has it.
        "identities": [{"address": "work@example.org", "name": "Alice", "default": True}],
        "auto_approve": [],
        "has_password": True,
        "run_filters": False,
        "attachment_max_mb": 10.0,
        "max_recipients": 10,
    }
    assert user_client.get("/api/v1/accounts").json() == [added]

    # Stored as mailroom-core stores it: sealed, bound to the account.
    store = user_store(migrated_engine, user.id)
    blob = store.secrets()["work"]
    assert MAILBOX_PASSWORD.encode() not in blob
    assert open_secret(secrets_subkey, "work", "password", blob) == MAILBOX_PASSWORD
    assert load_accounts(store).account("work").auth.type == "password"

    # Without a password, an edit keeps the stored one.
    change = edited(new_account(send_mode="off", login="alice"))
    response = user_client.put("/api/v1/accounts/work", json=change)
    assert response.status_code == 200
    assert (response.json()["send_mode"], response.json()["login"]) == ("off", "alice")
    assert open_secret(secrets_subkey, "work", "password", store.secrets()["work"]) == (
        MAILBOX_PASSWORD
    )
    response = user_client.put("/api/v1/accounts/work", json=change | {"password": "new one"})
    assert response.status_code == 200
    assert open_secret(secrets_subkey, "work", "password", store.secrets()["work"]) == "new one"

    assert user_client.delete("/api/v1/accounts/work").status_code == 204
    assert user_client.get("/api/v1/accounts").json() == []
    assert store.secrets() == {}
    assert user_client.delete("/api/v1/accounts/work").status_code == 404

    assert MAILBOX_PASSWORD not in caplog.text
    assert "new one" not in caplog.text
    assert [record.msg for record in caplog.records if str(record.msg).startswith("account_")] == [
        "account_added",
        "account_changed",
        "account_changed",
        "account_removed",
    ]


@pytest.mark.parametrize(
    ("account", "code", "path"),
    [
        (new_account("Not A Slug"), "invalid_account", ["id"]),
        (
            new_account(imap={"host": "x", "port": 0, "tls": "implicit"}),
            "invalid_account",
            ["imap", "port"],
        ),
        (new_account(identities=[]), "invalid_account", ["identities"]),
        (
            new_account(identities=[{"address": "nope"}]),
            "invalid_account",
            ["identities", 0, "address"],
        ),
        (new_account(login=""), "invalid_account", ["login"]),
    ],
    ids=["id", "port", "no identity", "address", "login"],
)
def test_accounts_are_checked_by_mailroom_core(
    user_client: TestClient, account: dict[str, Any], code: str, path: list[str | int]
) -> None:
    response = user_client.post("/api/v1/accounts", json=account)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert path in [issue["path"] for issue in detail["issues"]]
    assert user_client.get("/api/v1/accounts").json() == []


def test_ids_and_logins_are_unique(user_client: TestClient) -> None:
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201
    again = user_client.post("/api/v1/accounts", json=new_account())
    assert again.status_code == 400
    assert again.json()["detail"]["code"] == "account_exists"

    same_login = new_account(
        "home", login="work@example.org", identities=[{"address": "home@example.org"}]
    )
    response = user_client.post("/api/v1/accounts", json=same_login)
    assert response.status_code == 400
    assert response.json()["detail"]["issues"] == [
        {"path": ["login"], "message": 'duplicate login "work@example.org"'}
    ]


def test_a_new_account_needs_a_password(user_client: TestClient) -> None:
    account = new_account()
    del account["password"]
    assert user_client.post("/api/v1/accounts", json=account).status_code == 422


def test_editing_a_missing_account_is_a_404(user_client: TestClient) -> None:
    assert user_client.put("/api/v1/accounts/work", json=edited(new_account())).status_code == 404


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [(RevisionConflict, 409, "accounts_changed"), (SettingsBusy, 503, "accounts_busy")],
)
def test_a_refused_save_is_worded(
    user_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: type[Exception],
    status: int,
    code: str,
) -> None:
    def save_account(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(accounts_service, "save_account", save_account)
    response = user_client.post("/api/v1/accounts", json=new_account())
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def test_users_see_only_their_own_accounts(user_client: TestClient, db_session: Session) -> None:
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201
    bob = create_user(db_session, "bob", PASSWORD)
    user_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, bob))

    assert user_client.get("/api/v1/accounts").json() == []
    assert user_client.put("/api/v1/accounts/work", json=edited(new_account())).status_code == 404
    assert user_client.delete("/api/v1/accounts/work").status_code == 404
    assert user_client.post("/api/v1/accounts/work/test").status_code == 404
    assert user_client.get("/api/v1/accounts/work/folders").status_code == 404
    assert user_client.get("/api/v1/accounts/work/filter-settings").status_code == 404
    off = {"run_filters": True}
    assert user_client.put("/api/v1/accounts/work/filter-settings", json=off).status_code == 404
    # The same id is theirs to use.
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201


def test_run_filters_is_off_until_turned_on(user_client: TestClient) -> None:
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201
    settings = "/api/v1/accounts/work/filter-settings"
    assert user_client.get(settings).json() == {"run_filters": False}

    assert user_client.put(settings, json={"run_filters": True}).json() == {"run_filters": True}
    assert user_client.get(settings).json() == {"run_filters": True}
    assert user_client.get("/api/v1/accounts").json()[0]["run_filters"] is True

    # Removing the account takes the switch with it.
    assert user_client.delete("/api/v1/accounts/work").status_code == 204
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201
    assert user_client.get(settings).json() == {"run_filters": False}


def test_without_a_secrets_key_nothing_is_stored(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(application.state, "secrets_subkey", None)
    response = user_client.post("/api/v1/accounts", json=new_account())
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "secrets_key_missing"
    assert user_client.get("/api/v1/accounts").json() == []


def test_a_test_logs_in_with_the_stored_password(
    user_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201
    logins: list[tuple[str, str, int, str, str]] = []

    def check_login(account: Account) -> None:
        logins.append(
            (account.name, account.host, account.port, account.username, account.password)
        )

    monkeypatch.setattr(accounts_routes, "check_login", check_login)
    response = user_client.post("/api/v1/accounts/work/test")
    assert response.json() == {"ok": True, "code": None, "message": None}
    assert logins == [("work", "127.0.0.1", 9, "work@example.org", MAILBOX_PASSWORD)]


def test_a_refused_login_is_an_answer(
    user_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201

    def check_login(_account: Account) -> None:
        raise MailFailure("login_failed", "[AUTHENTICATIONFAILED] Authentication failed.")

    monkeypatch.setattr(accounts_routes, "check_login", check_login)
    assert user_client.post("/api/v1/accounts/work/test").json() == {
        "ok": False,
        "code": "login_failed",
        "message": "[AUTHENTICATIONFAILED] Authentication failed.",
    }


def test_an_unreachable_server_is_reported(user_client: TestClient) -> None:
    """Port 9 on loopback is closed: a real connection, refused."""
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201
    result = user_client.post("/api/v1/accounts/work/test").json()
    assert (result["ok"], result["code"]) == (False, "unreachable")
    assert MAILBOX_PASSWORD not in result["message"]

    response = user_client.get("/api/v1/accounts/work/folders")
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "unreachable"


def test_starttls_imap_is_refused_before_connecting(user_client: TestClient) -> None:
    account = new_account(imap={"host": "127.0.0.1", "port": 143, "tls": "starttls"})
    assert user_client.post("/api/v1/accounts", json=account).status_code == 201
    result = user_client.post("/api/v1/accounts/work/test").json()
    assert (result["ok"], result["code"]) == (False, "starttls_unsupported")
    assert user_client.get("/api/v1/accounts/work/folders").status_code == 409


def test_a_password_that_does_not_open_is_reported(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailroom_core.crypto import secrets_subkey

    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201
    monkeypatch.setattr(application.state, "secrets_subkey", secrets_subkey(os.urandom(32)))
    result = user_client.post("/api/v1/accounts/work/test").json()
    assert (result["ok"], result["code"]) == (False, "password_missing")
    response = user_client.get("/api/v1/accounts/work/folders")
    assert (response.status_code, response.json()["detail"]["code"]) == (409, "password_missing")


def _core_rows(db: Session, user_id: int) -> dict[str, int]:
    """How many rows of each mailroom-core table the user has, as ``db``'s transaction sees them
    (the removal commits only to the test's savepoint)."""
    counts = {}
    for table in ("settings", "account_secrets", "sends"):
        statement = text(f"select count(*) from core.{table} where user_id = :user_id")
        counts[table] = db.execute(statement, {"user_id": str(user_id)}).scalar_one()
    return counts


def _bob_with_an_account(
    db_session: Session, migrated_engine: Engine, secrets_subkey: SecretsSubkey
) -> User:
    from mailroom.accounts.app_data import set_run_filters
    from mailroom.accounts.store import save_account

    bob = create_user(db_session, "bob", PASSWORD)
    store = user_store(migrated_engine, bob.id)
    account = new_account()
    password = account.pop("password")
    save_account(
        store, secrets_subkey, account | {"auth": {"type": "password"}}, password, new=True
    )
    save_account(
        store, None, account | {"send_mode": "off", "auth": {"type": "password"}}, None, new=False
    )
    store.record_send(datetime.now(UTC))
    set_run_filters(db_session, bob.id, "work", run_filters=True)
    assert _core_rows(db_session, bob.id) == {"settings": 2, "account_secrets": 1, "sends": 1}
    return bob


def test_removing_a_user_forgets_their_accounts(
    admin_client: TestClient,
    db_session: Session,
    migrated_engine: Engine,
    secrets_subkey: SecretsSubkey,
) -> None:
    from mailroom.accounts.app_data import run_filters_by_account

    bob = _bob_with_an_account(db_session, migrated_engine, secrets_subkey)
    assert admin_client.delete(f"/api/v1/users/{bob.id}").status_code == 204
    # Settings with their history, the sealed password and the send log, with the user's row.
    assert _core_rows(db_session, bob.id) == {"settings": 0, "account_secrets": 0, "sends": 0}
    assert run_filters_by_account(db_session, bob.id) == {}
    assert db_session.get(User, bob.id) is None


def test_a_user_whose_accounts_are_being_saved_is_not_removed(
    admin_client: TestClient,
    db_session: Session,
    migrated_engine: Engine,
    secrets_subkey: SecretsSubkey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mailroom.accounts import app_data

    bob = _bob_with_an_account(db_session, migrated_engine, secrets_subkey)
    monkeypatch.setattr(
        app_data, "forget_user", partial(forget_user, lock_timeout=timedelta(milliseconds=100))
    )
    # Another transaction holds bob's settings lock, as a save of them would.
    with migrated_engine.connect() as other:
        held = other.begin()
        try:
            forget_user(other, str(bob.id))
            response = admin_client.delete(f"/api/v1/users/{bob.id}")
        finally:
            held.rollback()
    assert (response.status_code, response.json()["detail"]["code"]) == (
        503,
        "user_accounts_busy",
    )
    # Nothing went: neither bob nor any of his rows.
    assert db_session.get(User, bob.id) is not None
    assert _core_rows(db_session, bob.id) == {"settings": 2, "account_secrets": 1, "sends": 1}
    assert app_data.run_filters_by_account(db_session, bob.id) == {"work": True}


def test_the_dev_mailbox_logs_in_and_lists_folders(
    user_client: TestClient,
    imap_server: ImapTestServer,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Against the Dovecot ``IMAP_TEST_HOST`` names (the dev one: ``IMAP_TEST_PORT=33993``,
    ``MAILROOM_MAIL_CA_FILE`` its CA); skipped without it."""
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_ssl_context())
    login = imap_server.unique_user("accounts")
    endpoint = {"host": imap_server.host, "port": imap_server.port, "tls": "implicit"}
    account = new_account(login=login, password=imap_server.password, imap=endpoint)
    assert user_client.post("/api/v1/accounts", json=account).status_code == 201
    conn = IMAPClient(imap_server.host, imap_server.port, ssl=True, ssl_context=mail_ssl_context())
    try:
        conn.login(login, imap_server.password)
        conn.create_folder("Archive/2026")
    finally:
        conn.logout()

    assert user_client.post("/api/v1/accounts/work/test").json()["ok"] is True
    folders = user_client.get("/api/v1/accounts/work/folders").json()
    assert {"path": ["INBOX"]} in folders
    assert {"path": ["Archive"]} in folders
    # Split on the server's delimiter ("/" here), not imap-thingy's default.
    assert {"path": ["Archive", "2026"]} in folders

    change = edited(account) | {"password": "wrong password"}
    assert user_client.put("/api/v1/accounts/work", json=change).status_code == 200
    assert user_client.post("/api/v1/accounts/work/test").json()["code"] == "login_failed"


def test_mail_connections_time_out() -> None:
    from mailroom.accounts.mail import IMAP_TIMEOUT_SECONDS, imap_account
    from mailroom_core.config import AccountConfig

    raw = new_account()
    del raw["password"]
    config = AccountConfig.model_validate(raw | {"auth": {"type": "password"}})
    account = imap_account(config, MAILBOX_PASSWORD, mail_ssl_context())
    assert account.timeout == IMAP_TIMEOUT_SECONDS


def test_the_mail_ssl_context_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAILROOM_MAIL_CA_FILE", "/nonexistent/ca.pem")
    context = mail_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
