from __future__ import annotations

import imaplib
import logging
import os
import ssl
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

import pytest
from imapclient.exceptions import LoginError
from sqlalchemy import text

from mailroom.accounts import service as accounts_service
from mailroom.accounts.mail import MailFailure, mail_failure, mail_ssl_context
from mailroom.accounts.store import load_accounts, user_store
from mailroom.api.routes import accounts as accounts_routes
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.auth.users import create_user
from mailroom.models import User
from mailroom_core.crypto import open_secret
from mailroom_core.filters import Account
from mailroom_core.mail import CERTIFICATE_DETAIL, MESSAGES
from mailroom_core.store import RevisionConflict, SettingsBusy, forget_user
from mailroom_core.testing import SmtpStub
from tests.conftest import PASSWORD

if TYPE_CHECKING:
    from fastapi import FastAPI
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
    # The same id is theirs to use.
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201


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
        raise MailFailure("auth_failed", MESSAGES["auth_failed"])

    monkeypatch.setattr(accounts_routes, "check_login", check_login)
    assert user_client.post("/api/v1/accounts/work/test").json() == {
        "ok": False,
        "code": "auth_failed",
        "message": MESSAGES["auth_failed"],
    }


def test_an_unreachable_server_is_reported(user_client: TestClient) -> None:
    """Port 9 on loopback is closed: a real connection, refused."""
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201
    result = user_client.post("/api/v1/accounts/work/test").json()
    assert (result["ok"], result["code"]) == (False, "unreachable")
    assert result["message"] == MESSAGES["unreachable"]


def test_a_certificate_that_does_not_verify_is_said_by_the_test(user_client: TestClient) -> None:
    """The stub's TLS certificate is self-signed: the handshake fails before any IMAP."""
    with SmtpStub() as stub:
        account = new_account(imap={"host": "127.0.0.1", "port": stub.port, "tls": "implicit"})
        assert user_client.post("/api/v1/accounts", json=account).status_code == 201
        result = user_client.post("/api/v1/accounts/work/test").json()
    assert (result["ok"], result["code"]) == (False, "unreachable")
    assert result["message"] == f"{MESSAGES['unreachable']} {CERTIFICATE_DETAIL}"


def test_starttls_imap_is_refused_before_connecting(user_client: TestClient) -> None:
    account = new_account(imap={"host": "127.0.0.1", "port": 143, "tls": "starttls"})
    assert user_client.post("/api/v1/accounts", json=account).status_code == 201
    result = user_client.post("/api/v1/accounts/work/test").json()
    assert (result["ok"], result["code"]) == (False, "starttls_unsupported")


def test_a_password_that_does_not_open_is_reported(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailroom_core.crypto import secrets_subkey

    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201
    monkeypatch.setattr(application.state, "secrets_subkey", secrets_subkey(os.urandom(32)))
    result = user_client.post("/api/v1/accounts/work/test").json()
    assert (result["ok"], result["code"]) == (False, "password_missing")


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


def dev_mailbox_account(
    client: TestClient,
    imap_server: ImapTestServer,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    prefix: str,
) -> dict[str, Any]:
    """An account ``work`` on a fresh login of the Dovecot ``IMAP_TEST_HOST`` names (the dev one:
    ``IMAP_TEST_PORT=33993``, ``MAILROOM_MAIL_CA_FILE`` its CA); the tests using it are skipped
    without it."""
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_ssl_context())
    login = imap_server.unique_user(prefix)
    endpoint = {"host": imap_server.host, "port": imap_server.port, "tls": "implicit"}
    account = new_account(login=login, password=imap_server.password, imap=endpoint)
    assert client.post("/api/v1/accounts", json=account).status_code == 201
    return account


def test_the_dev_mailbox_logs_in(
    user_client: TestClient,
    imap_server: ImapTestServer,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = dev_mailbox_account(user_client, imap_server, application, monkeypatch, "accounts")
    assert user_client.post("/api/v1/accounts/work/test").json()["ok"] is True

    change = edited(account) | {"password": "wrong password"}
    assert user_client.put("/api/v1/accounts/work", json=change).status_code == 200
    assert user_client.post("/api/v1/accounts/work/test").json() == {
        "ok": False,
        "code": "auth_failed",
        "message": MESSAGES["auth_failed"],
    }


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


def _mailbox() -> Account:
    return Account("work", "imap.example", 993, "me", MAILBOX_PASSWORD)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Authentication failed."), "auth_failed"),
        (imaplib.IMAP4.error("select failed: [NONEXISTENT] Unknown Mailbox"), "mailbox_missing"),
        (imaplib.IMAP4.error("BAD something odd"), "server_error"),
        (ssl.SSLError("handshake"), "unreachable"),
        (ConnectionRefusedError(111, "Connection refused"), "unreachable"),
        (TimeoutError("timed out"), "timeout"),
    ],
)
def test_a_mailbox_failure_has_core_s_code_and_message(error: Exception, code: str) -> None:
    failure = mail_failure(error, _mailbox())
    assert failure is not None
    assert (failure.code, failure.message) == (code, MESSAGES[code])


def test_a_certificate_that_does_not_verify_is_said() -> None:
    failure = mail_failure(ssl.SSLCertVerificationError(1, "certificate verify failed"), _mailbox())
    assert failure is not None
    assert failure.code == "unreachable"
    assert failure.message == f"{MESSAGES['unreachable']} {CERTIFICATE_DETAIL}"


def test_a_failure_quoting_the_password_is_logged_by_type_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="mailroom_core.mail")
    failure = mail_failure(imaplib.IMAP4.error(f"LOGIN me {MAILBOX_PASSWORD}"), _mailbox())
    assert failure is not None
    assert MAILBOX_PASSWORD not in failure.message
    assert caplog.records
    assert all(MAILBOX_PASSWORD not in str(vars(record)) for record in caplog.records)


@pytest.mark.parametrize("password", ['it\'s "secret"', "back\\slash-secret", "perché-secret"])
def test_a_login_error_is_logged_by_type_only(
    caplog: pytest.LogCaptureFixture, password: str
) -> None:
    """imaplib prints the server's reply as bytes, so an echoed password comes back escaped:
    a login's error is never logged by its text."""
    echoed = repr(password.encode())
    assert password not in echoed
    caplog.set_level(logging.DEBUG, logger="mailroom_core.mail")
    error = LoginError(f"LOGIN command error: BAD [{echoed}]")
    failure = mail_failure(error, Account("work", "imap.example", 993, "me", password))
    assert failure is not None
    assert failure.code == "auth_failed"
    assert caplog.records
    assert all("secret" not in str(vars(record)) for record in caplog.records)


def test_a_bug_is_no_mailbox_failure() -> None:
    assert mail_failure(ValueError("bug"), _mailbox()) is None
