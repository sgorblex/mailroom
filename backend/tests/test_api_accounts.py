from __future__ import annotations

import logging
import os
import ssl
import threading
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from mailroom.accounts import service as accounts_service
from mailroom.accounts.store import load_accounts, user_store
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.auth.users import create_user
from mailroom.mail.connections import mail_ssl_context
from mailroom.mail.service import Quotas
from mailroom.models import User
from mailroom_core.crypto import open_secret, seal_secret
from mailroom_core.mail import (
    CERTIFICATE_DETAIL,
    MESSAGES,
    Capabilities,
    MailConnections,
    MailError,
    Quota,
    QuotaUsage,
)
from mailroom_core.store import RevisionConflict, SettingsBusy, forget_user
from mailroom_core.testing import SmtpStub
from tests.conftest import PASSWORD
from tests.support.mail import mail_servers_ssl_context

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.testing import ImapTestServer


MAILBOX_PASSWORD = "mailbox-secret-3141"


def new_account(label: str = "work", **changes: Any) -> dict[str, Any]:
    account: dict[str, Any] = {
        "login": f"{label}@example.org",
        "password": MAILBOX_PASSWORD,
        "send_mode": "drafts",
        "imap": {"host": "127.0.0.1", "port": 9, "tls": "implicit"},
        "smtp": {"host": "127.0.0.1", "port": 9, "tls": "implicit"},
        "identities": [{"address": f"{label}@example.org", "name": "Alice"}],
    }
    return account | changes


def edited(account: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in account.items() if key != "password"}


def added(client: TestClient, account: dict[str, Any] | None = None) -> int:
    """Add ``account`` (``new_account()``); its id."""
    response = client.post("/api/v1/accounts", json=new_account() if account is None else account)
    assert response.status_code == 201
    account_id: int = response.json()["id"]
    return account_id


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/accounts").status_code == 401
    assert api_client.post("/api/v1/accounts", json=new_account()).status_code == 401
    assert api_client.post("/api/v1/accounts/1/test").status_code == 401


def test_a_user_adds_edits_and_removes_an_account(
    user_client: TestClient,
    user: User,
    migrated_engine: Engine,
    secrets_subkey: SecretsSubkey,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    assert user_client.get("/api/v1/accounts").json() == []

    # A client's own id is no part of an account.
    response = user_client.post("/api/v1/accounts", json=new_account() | {"id": 7})
    assert response.status_code == 201
    account = response.json()
    work = account["id"]
    assert isinstance(work, int)
    assert work > 0
    assert account == {
        "id": work,
        # An unnamed account has no name, and its login to be called by.
        "name": None,
        "display_name": "work@example.org",
        "colour": None,
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
    assert user_client.get("/api/v1/accounts").json() == [account]
    # A second account takes another id, never one handed out before.
    home = added(user_client, new_account("home"))
    assert home > work

    # Stored as mailroom-core stores it: sealed, bound to the account.
    store = user_store(migrated_engine, user.id)
    blob = store.secrets()[work]
    assert MAILBOX_PASSWORD.encode() not in blob
    assert open_secret(secrets_subkey, work, "password", blob) == MAILBOX_PASSWORD
    assert load_accounts(store).account(work).auth.type == "password"

    # Without a password, an edit keeps the stored one.
    change = edited(new_account(send_mode="off", login="alice"))
    response = user_client.put(f"/api/v1/accounts/{work}", json=change)
    assert response.status_code == 200
    assert (response.json()["send_mode"], response.json()["login"]) == ("off", "alice")
    assert open_secret(secrets_subkey, work, "password", store.secrets()[work]) == (
        MAILBOX_PASSWORD
    )
    response = user_client.put(f"/api/v1/accounts/{work}", json=change | {"password": "new one"})
    assert response.status_code == 200
    assert open_secret(secrets_subkey, work, "password", store.secrets()[work]) == "new one"

    assert user_client.delete(f"/api/v1/accounts/{work}").status_code == 204
    assert [a["id"] for a in user_client.get("/api/v1/accounts").json()] == [home]
    assert list(store.secrets()) == [home]
    assert user_client.delete(f"/api/v1/accounts/{work}").status_code == 404

    assert MAILBOX_PASSWORD not in caplog.text
    assert "new one" not in caplog.text
    assert [record.msg for record in caplog.records if str(record.msg).startswith("account_")] == [
        "account_added",
        "account_added",
        "account_changed",
        "account_changed",
        "account_removed",
    ]


@pytest.mark.parametrize(
    ("account", "code", "path"),
    [
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
        (new_account(name=" "), "invalid_account", ["name"]),
    ],
    ids=["port", "no identity", "address", "login", "blank name"],
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


def test_an_account_has_a_name_and_a_colour(user_client: TestClient) -> None:
    response = user_client.post("/api/v1/accounts", json=new_account(name="Lavoro", colour="sky"))
    assert response.status_code == 201
    added = response.json()
    assert (added["name"], added["display_name"], added["colour"]) == ("Lavoro", "Lavoro", "sky")
    work = added["id"]

    renamed = edited(new_account(name="Ufficio", colour="pink"))
    response = user_client.put(f"/api/v1/accounts/{work}", json=renamed)
    assert (response.json()["name"], response.json()["colour"]) == ("Ufficio", "pink")
    [listed] = user_client.get("/api/v1/accounts").json()
    assert (listed["id"], listed["name"], listed["display_name"], listed["colour"]) == (
        work,
        "Ufficio",
        "Ufficio",
        "pink",
    )

    # A listed account goes back as it came: display_name is read-only, and ignored.
    sent = {key: listed[key] for key in renamed} | {"display_name": "Altro"}
    response = user_client.put(f"/api/v1/accounts/{work}", json=sent)
    assert (response.json()["name"], response.json()["display_name"]) == ("Ufficio", "Ufficio")

    # An account named after its login is not an unnamed one.
    named = edited(new_account(name="work@example.org"))
    response = user_client.put(f"/api/v1/accounts/{work}", json=named)
    assert (response.json()["name"], response.json()["display_name"]) == (
        "work@example.org",
        "work@example.org",
    )

    # An edit replaces them like any field: without them, the account is unnamed again.
    user_client.put(f"/api/v1/accounts/{work}", json=edited(new_account()))
    [listed] = user_client.get("/api/v1/accounts").json()
    assert (listed["name"], listed["display_name"], listed["colour"]) == (
        None,
        "work@example.org",
        None,
    )

    for wrong in ({"colour": "beige"}, {"name": "x" * 65}):
        change = edited(new_account(**wrong))
        assert user_client.put(f"/api/v1/accounts/{work}", json=change).status_code == 422


def test_a_user_names_each_account_differently(
    user_client: TestClient, db_session: Session
) -> None:
    work = added(user_client, new_account(name="Lavoro"))
    home = new_account("home", name=" lavoro ")
    taken = user_client.post("/api/v1/accounts", json=home)
    assert taken.status_code == 409
    message = "Another account is already named 'Lavoro'."
    assert taken.json()["detail"] == {
        "code": "account_name_taken",
        "message": message,
        "issues": [{"path": ["name"], "message": message}],
    }
    assert [a["id"] for a in user_client.get("/api/v1/accounts").json()] == [work]

    # Unnamed accounts share no name; an edit is checked too, but not against itself.
    home_id = added(user_client, new_account("home"))
    renamed = user_client.put(f"/api/v1/accounts/{home_id}", json=edited(home))
    assert (renamed.status_code, renamed.json()["detail"]["code"]) == (409, "account_name_taken")
    again = user_client.put(f"/api/v1/accounts/{work}", json=edited(new_account(name="LAVORO")))
    assert (again.status_code, again.json()["name"]) == (200, "LAVORO")
    other = edited(new_account("home", name="Casa"))
    assert user_client.put(f"/api/v1/accounts/{home_id}", json=other).status_code == 200

    # Another user's accounts take none of this user's names.
    bob = create_user(db_session, "bob", PASSWORD)
    user_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, bob))
    added(user_client, new_account(name="Lavoro"))


def test_logins_are_unique(user_client: TestClient) -> None:
    added(user_client)
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
    assert user_client.put("/api/v1/accounts/1", json=edited(new_account())).status_code == 404
    for path in ("0", "work", str(2**53)):
        assert (
            user_client.put(f"/api/v1/accounts/{path}", json=edited(new_account())).status_code
            == 422
        )


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
    work = added(user_client)
    bob = create_user(db_session, "bob", PASSWORD)
    user_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, bob))

    assert user_client.get("/api/v1/accounts").json() == []
    change = edited(new_account())
    assert user_client.put(f"/api/v1/accounts/{work}", json=change).status_code == 404
    assert user_client.delete(f"/api/v1/accounts/{work}").status_code == 404
    assert user_client.post(f"/api/v1/accounts/{work}/test").status_code == 404
    assert user_client.get(f"/api/v1/accounts/{work}/quota").status_code == 404
    # The same login is theirs to use, under an id of its own.
    assert added(user_client) != work


def test_without_a_secrets_key_nothing_is_stored(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(application.state, "secrets_subkey", None)
    response = user_client.post("/api/v1/accounts", json=new_account())
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "secrets_key_missing"
    assert user_client.get("/api/v1/accounts").json() == []


def test_a_test_logs_in_on_the_mail_pool(
    user_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = added(user_client)
    tested: list[int] = []

    def fake_login(_connections: MailConnections, account_id: int) -> None:
        tested.append(account_id)

    monkeypatch.setattr(MailConnections, "test_login", fake_login)
    response = user_client.post(f"/api/v1/accounts/{work}/test")
    assert response.json() == {"ok": True, "code": None, "message": None}
    assert tested == [work]


def test_a_refused_login_is_an_answer(
    user_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = added(user_client)

    def fake_login(_connections: MailConnections, _account_id: int) -> None:
        raise MailError("auth_failed")

    monkeypatch.setattr(MailConnections, "test_login", fake_login)
    assert user_client.post(f"/api/v1/accounts/{work}/test").json() == {
        "ok": False,
        "code": "auth_failed",
        "message": MESSAGES["auth_failed"],
    }


def test_a_test_still_going_at_the_pool_s_deadline_is_a_timeout(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = added(user_client)
    released = threading.Event()

    def fake_login(_connections: MailConnections, _account_id: int) -> None:
        released.wait(5)

    monkeypatch.setattr(MailConnections, "test_login", fake_login)
    monkeypatch.setattr(application.state.mail_pool, "deadline_seconds", 0.2)
    try:
        assert user_client.post(f"/api/v1/accounts/{work}/test").json() == {
            "ok": False,
            "code": "timeout",
            "message": MESSAGES["timeout"],
        }
    finally:
        released.set()


def test_an_unreachable_server_is_reported(user_client: TestClient) -> None:
    """Port 9 on loopback is closed: a real connection, refused."""
    work = added(user_client)
    result = user_client.post(f"/api/v1/accounts/{work}/test").json()
    assert (result["ok"], result["code"]) == (False, "unreachable")
    assert result["message"] == MESSAGES["unreachable"]


def test_a_certificate_that_does_not_verify_is_said_by_the_test(user_client: TestClient) -> None:
    """The stub's TLS certificate is self-signed: the handshake fails before any IMAP."""
    with SmtpStub() as stub:
        account = new_account(imap={"host": "127.0.0.1", "port": stub.port, "tls": "implicit"})
        work = added(user_client, account)
        result = user_client.post(f"/api/v1/accounts/{work}/test").json()
    assert (result["ok"], result["code"]) == (False, "unreachable")
    assert result["message"] == f"{MESSAGES['unreachable']} {CERTIFICATE_DETAIL}"


def test_a_starttls_account_is_tested_too(user_client: TestClient) -> None:
    """mailroom-core speaks STARTTLS: the test connects (to a closed port) rather than refusing."""
    account = new_account(imap={"host": "127.0.0.1", "port": 9, "tls": "starttls"})
    work = added(user_client, account)
    result = user_client.post(f"/api/v1/accounts/{work}/test").json()
    assert (result["ok"], result["code"]) == (False, "unreachable")


def test_without_a_secrets_key_a_test_is_refused(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = added(user_client)
    monkeypatch.setattr(application.state, "secrets_subkey", None)
    response = user_client.post(f"/api/v1/accounts/{work}/test")
    assert (response.status_code, response.json()["detail"]["code"]) == (
        503,
        "secrets_key_missing",
    )


def test_a_password_that_does_not_open_is_reported(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailroom_core.crypto import secrets_subkey

    work = added(user_client)
    monkeypatch.setattr(application.state, "secrets_subkey", secrets_subkey(os.urandom(32)))
    result = user_client.post(f"/api/v1/accounts/{work}/test").json()
    assert (result["ok"], result["code"]) == (False, "password_missing")


def test_a_password_sealed_to_a_former_slug_still_opens(
    user_client: TestClient,
    user: User,
    migrated_engine: Engine,
    secrets_subkey: SecretsSubkey,
) -> None:
    """An account numbered by ``core_0004`` keeps the secret it had, sealed to its slug, and a
    password saved for it later is sealed to the slug too."""
    from mailroom.accounts.store import account_password

    work = added(user_client)
    store = user_store(migrated_engine, user.id)
    sealed = seal_secret(secrets_subkey, work, "password", "old one", former_slug="work")
    with migrated_engine.begin() as connection:
        connection.execute(
            text("insert into core.former_slugs values (:user, :id, 'work')"),
            {"user": str(user.id), "id": work},
        )
        connection.execute(
            text("update core.account_secrets set blob = :blob where account_id = :id"),
            {"blob": sealed, "id": work},
        )
    assert account_password(store, secrets_subkey, work) == "old one"

    change = edited(new_account()) | {"password": "new one"}
    assert user_client.put(f"/api/v1/accounts/{work}", json=change).status_code == 200
    blob = store.secrets()[work]
    assert open_secret(secrets_subkey, work, "password", blob, former_slug="work") == "new one"
    assert account_password(store, secrets_subkey, work) == "new one"


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
    saved = save_account(
        store, secrets_subkey, account | {"auth": {"type": "password"}}, password, new=True
    )
    change = account | {"id": saved.id, "send_mode": "off", "auth": {"type": "password"}}
    save_account(store, None, change, None, new=False)
    store.record_send(datetime.now(UTC))
    set_run_filters(db_session, bob.id, saved.id, run_filters=True)
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
    assert list(app_data.run_filters_by_account(db_session, bob.id).values()) == [True]


def dev_mailbox_account(
    client: TestClient,
    imap_server: ImapTestServer,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    prefix: str,
) -> dict[str, Any]:
    """An account on a fresh login of the Dovecot ``IMAP_TEST_HOST`` names (``mise run
    test-backend-mail`` starts one; ``tests/support/mail.py`` says which TLS it trusts); the tests
    using it are skipped without it."""
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_servers_ssl_context())
    login = imap_server.unique_user(prefix)
    endpoint = {"host": imap_server.host, "port": imap_server.port, "tls": "implicit"}
    account = new_account(login=login, password=imap_server.password, imap=endpoint)
    return account | {"id": added(client, account)}


def test_the_dev_mailbox_logs_in(
    user_client: TestClient,
    imap_server: ImapTestServer,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = dev_mailbox_account(user_client, imap_server, application, monkeypatch, "accounts")
    work = account.pop("id")
    assert user_client.post(f"/api/v1/accounts/{work}/test").json()["ok"] is True

    change = edited(account) | {"password": "wrong password"}
    assert user_client.put(f"/api/v1/accounts/{work}", json=change).status_code == 200
    assert user_client.post(f"/api/v1/accounts/{work}/test").json() == {
        "ok": False,
        "code": "auth_failed",
        "message": MESSAGES["auth_failed"],
    }


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_quota_is_asked_once_in_ten_minutes(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = added(user_client)
    clock = _Clock()
    monkeypatch.setattr(application.state, "quotas", Quotas(clock))
    asked: list[int] = []

    def fake_quota(_connections: MailConnections, account_id: int) -> Quota:
        asked.append(account_id)
        return Quota(storage=QuotaUsage(used=len(asked), limit=2048), messages=None)

    monkeypatch.setattr(MailConnections, "quota", fake_quota)
    first = {"account_id": work, "storage": {"used": 1, "limit": 2048}, "messages": None}
    assert user_client.get(f"/api/v1/accounts/{work}/quota").json() == first
    clock.now += 599
    assert user_client.get(f"/api/v1/accounts/{work}/quota").json() == first
    clock.now += 2
    assert user_client.get(f"/api/v1/accounts/{work}/quota").json()["storage"]["used"] == 2
    # A changed account is asked afresh.
    change = edited(new_account()) | {"name": "Work"}
    assert user_client.put(f"/api/v1/accounts/{work}", json=change).status_code == 200
    assert user_client.get(f"/api/v1/accounts/{work}/quota").json()["storage"]["used"] == 3
    assert asked == [work, work, work]


def test_a_failed_quota_is_refused_and_not_kept(
    user_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = added(user_client)
    answers: list[Quota | MailError | None] = [MailError("server_error"), None]

    def fake_quota(_connections: MailConnections, _account_id: int) -> Quota | None:
        answer = answers.pop(0)
        if isinstance(answer, MailError):
            raise answer
        return answer

    monkeypatch.setattr(MailConnections, "quota", fake_quota)
    refused = user_client.get(f"/api/v1/accounts/{work}/quota")
    assert refused.status_code == 502
    assert refused.json()["detail"]["code"] == "server_error"
    none = {"account_id": work, "storage": None, "messages": None}
    assert user_client.get(f"/api/v1/accounts/{work}/quota").json() == none
    assert user_client.get(f"/api/v1/accounts/{work}/quota").json() == none
    assert answers == []


def test_a_server_that_answers_no_has_no_quota_for_ten_minutes(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = added(user_client)
    clock = _Clock()
    monkeypatch.setattr(application.state, "quotas", Quotas(clock))
    asked: list[str] = []

    class Imaplib:
        def getquotaroot(self, mailbox: str) -> tuple[str, list[list[bytes]]]:
            asked.append(mailbox)
            refusal = [b"[NONEXISTENT] No quota root"]
            return "NO", [refusal, refusal]

    class Refusing:
        _imap = Imaplib()

    offered = Capabilities(
        gmail=False,
        thread=True,
        move=True,
        uidplus=True,
        condstore=True,
        special_use=True,
        quota=True,
    )
    monkeypatch.setattr(MailConnections, "capabilities", lambda _connections, _id: offered)
    monkeypatch.setattr(
        MailConnections, "_on_connection", lambda _connections, _id, fn, **_: fn(Refusing())
    )
    none = {"account_id": work, "storage": None, "messages": None}
    assert user_client.get(f"/api/v1/accounts/{work}/quota").json() == none
    clock.now += 599
    assert user_client.get(f"/api/v1/accounts/{work}/quota").json() == none
    assert asked == ["INBOX"]
    clock.now += 2
    assert user_client.get(f"/api/v1/accounts/{work}/quota").json() == none
    assert asked == ["INBOX", "INBOX"]


def test_the_dev_mailbox_has_a_quota(
    user_client: TestClient,
    imap_server: ImapTestServer,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = dev_mailbox_account(user_client, imap_server, application, monkeypatch, "quota")
    answer = user_client.get(f"/api/v1/accounts/{account['id']}/quota").json()
    assert answer["storage"]["limit"] == 1024**3
    assert answer["messages"] == {"used": 0, "limit": 100000}


def test_the_mail_ssl_context_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAILROOM_MAIL_CA_FILE", "/nonexistent/ca.pem")
    context = mail_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
