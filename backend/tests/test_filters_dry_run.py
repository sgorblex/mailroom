"""Dry runs against the Dovecot ``IMAP_TEST_HOST`` names (the dev one: ``IMAP_TEST_PORT=33993``,
``MAILROOM_MAIL_CA_FILE`` its CA); skipped without it."""

from __future__ import annotations

from email.message import EmailMessage
from typing import TYPE_CHECKING, Any

import pytest
from imap_thingy.filters import get_match_timeout, set_match_timeout
from imap_thingy.spec import load_filter
from imapclient import IMAPClient

from mailroom.accounts.mail import imap_account, mail_ssl_context
from mailroom.filters.imap import RuleSetFolder
from mailroom.filters.runs import DryRunTimeout, dry_run
from tests.test_api_accounts import new_account
from tests.test_api_filters import SHOP_SPEC, add_filter, rule_set

if TYPE_CHECKING:
    from fastapi import FastAPI
    from mailroom_core.testing import ImapTestServer
    from starlette.testclient import TestClient

SHOP = "Shop <news@shop.example>"
BANK = "Bank <alerts@bank.example>"


def message(sender: str, subject: str) -> bytes:
    mail = EmailMessage()
    mail["From"] = sender
    mail["To"] = "me@example.org"
    mail["Subject"] = subject
    mail.set_content("Hello.\n")
    return mail.as_bytes()


class Mailbox:
    def __init__(self, server: ImapTestServer, login: str) -> None:
        self.server = server
        self.login = login

    def client(self) -> IMAPClient:
        conn = IMAPClient(
            self.server.host, self.server.port, ssl=True, ssl_context=mail_ssl_context()
        )
        conn.login(self.login, self.server.password)
        return conn

    def append(self, folder: str, *messages: tuple[str, str, bool]) -> None:
        conn = self.client()
        try:
            if not conn.folder_exists(folder):
                conn.create_folder(folder)
            for sender, subject, starred in messages:
                conn.append(
                    folder, message(sender, subject), flags=[b"\\Flagged"] if starred else []
                )
        finally:
            conn.logout()

    def append_raw(self, folder: str, raw: bytes) -> None:
        conn = self.client()
        try:
            conn.append(folder, raw)
        finally:
            conn.logout()

    def seen(self, folder: str) -> list[bool]:
        conn = self.client()
        try:
            conn.select_folder(folder, readonly=True)
            fetched = conn.fetch(conn.search("ALL"), ["FLAGS"])
            return [b"\\Seen" in data[b"FLAGS"] for data in fetched.values()]
        finally:
            conn.logout()

    def subjects(self, folder: str) -> list[str]:
        conn = self.client()
        try:
            conn.select_folder(folder, readonly=True)
            fetched = conn.fetch(conn.search("ALL"), ["ENVELOPE"])
            return [data[b"ENVELOPE"].subject.decode() for data in fetched.values()]
        finally:
            conn.logout()


@pytest.fixture
def mailbox(
    imap_server: ImapTestServer, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> Mailbox:
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_ssl_context())
    return Mailbox(imap_server, imap_server.unique_user("filters"))


@pytest.fixture
def account(user_client: TestClient, mailbox: Mailbox) -> str:
    server = mailbox.server
    endpoint = {"host": server.host, "port": server.port, "tls": "implicit"}
    account = new_account(login=mailbox.login, password=server.password, imap=endpoint)
    assert user_client.post("/api/v1/accounts", json=account).status_code == 201
    return "work"


def dry_run_of(client: TestClient, rule_set_id: int) -> dict[str, Any]:
    response = client.post(f"/api/v1/rule-sets/{rule_set_id}/dry-run")
    assert response.status_code == 200, response.json()
    run: dict[str, Any] = response.json()
    return run


def test_a_dry_run_reports_matches_and_changes_nothing(
    user_client: TestClient, mailbox: Mailbox, account: str
) -> None:
    mailbox.append(
        "INBOX", (SHOP, "Autumn sale", False), (BANK, "Statement", False), (SHOP, "Picks", True)
    )
    mailbox.append("Archive", (BANK, "Old", False))
    created = user_client.post(f"/api/v1/accounts/{account}/rule-sets", json=rule_set())
    rule_set_id = created.json()["id"]
    shop = add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    bank_spec = {
        "criterion": {"type": "subject_matches", "args": {"pattern": "^Statement"}},
        "action": {"type": "delete"},
    }
    bank = add_filter(user_client, rule_set_id, "Bank", bank_spec)
    off = add_filter(user_client, rule_set_id, "Off", SHOP_SPEC)
    user_client.patch(f"/api/v1/filters/{off}", json={"enabled": False})

    run = dry_run_of(user_client, rule_set_id)
    assert (run["dry_run"], run["trigger"], run["error"]) == (True, "manual", None)
    assert run["started_at"] is not None
    assert run["finished_at"] is not None
    result = run["result"]
    assert (result["folder"], result["dry_run"], result["limited_to_newest"]) == (
        ["INBOX"],
        True,
        None,
    )
    assert [entry["filter_id"] for entry in result["filters"]] == [shop, bank]
    assert result["filters"][0]["spec"] == load_filter(SHOP_SPEC).to_spec()
    assert [m["subject"] for m in result["filters"][0]["matched"]] == ["Autumn sale", "Picks"]
    assert result["filters"][0]["matched"][0]["from"] == "Shop <news@shop.example>"
    assert [m["subject"] for m in result["filters"][1]["matched"]] == ["Statement"]
    assert not any(entry["executed"] for entry in result["filters"])

    assert mailbox.subjects("INBOX") == ["Autumn sale", "Statement", "Picks"]
    assert mailbox.subjects("Archive") == ["Old"]
    assert mailbox.seen("INBOX") == [False, False, False]
    runs = user_client.get(f"/api/v1/rule-sets/{rule_set_id}/runs").json()
    assert [r["id"] for r in runs] == [run["id"]]


def test_a_nul_in_a_header_does_not_stop_the_report(
    user_client: TestClient, mailbox: Mailbox, account: str
) -> None:
    """A third party's encoded word may decode to a NUL, which ``jsonb`` refuses."""
    mailbox.append("INBOX", (SHOP, "Plain", False))
    raw = (
        b"From: Shop <news@shop.example>\r\nTo: me@example.org\r\n"
        b"Subject: =?utf-8?q?Sale=00now?=\r\n\r\nHello.\r\n"
    )
    mailbox.append_raw("INBOX", raw)
    created = user_client.post(f"/api/v1/accounts/{account}/rule-sets", json=rule_set())
    rule_set_id = created.json()["id"]
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    run = dry_run_of(user_client, rule_set_id)
    [entry] = run["result"]["filters"]
    assert [m["subject"] for m in entry["matched"]] == ["Plain", "Salenow"]


def test_starred_only_looks_at_starred_mail(
    user_client: TestClient, mailbox: Mailbox, account: str
) -> None:
    mailbox.append("INBOX", (SHOP, "Autumn sale", False), (SHOP, "Picks", True))
    body = rule_set(starred_only=True)
    rule_set_id = user_client.post(f"/api/v1/accounts/{account}/rule-sets", json=body).json()["id"]
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    result = dry_run_of(user_client, rule_set_id)["result"]
    assert [m["subject"] for m in result["filters"][0]["matched"]] == ["Picks"]
    assert result["filters"][0]["spec"] == load_filter(SHOP_SPEC).to_spec()


def test_a_nested_folder_is_found_with_the_servers_delimiter(
    user_client: TestClient, mailbox: Mailbox, account: str
) -> None:
    conn = mailbox.client()
    try:
        delimiter = conn.list_folders("", "")[0][1].decode()
    finally:
        conn.logout()
    mailbox.append(f"Archive{delimiter}2026", (SHOP, "Kept", False))
    body = rule_set(folder=["Archive", "2026"])
    rule_set_id = user_client.post(f"/api/v1/accounts/{account}/rule-sets", json=body).json()["id"]
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    result = dry_run_of(user_client, rule_set_id)["result"]
    assert result["folder"] == ["Archive", "2026"]
    assert [m["subject"] for m in result["filters"][0]["matched"]] == ["Kept"]


def test_a_missing_folder_is_a_mailbox_error(
    user_client: TestClient, mailbox: Mailbox, account: str
) -> None:
    _ = mailbox
    body = rule_set(folder=["Nowhere"])
    rule_set_id = user_client.post(f"/api/v1/accounts/{account}/rule-sets", json=body).json()["id"]
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    response = user_client.post(f"/api/v1/rule-sets/{rule_set_id}/dry-run")
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "imap_error"


@pytest.fixture
def quick_match_timeout() -> Any:
    before = get_match_timeout()
    set_match_timeout(0.05)
    yield
    set_match_timeout(before)


@pytest.mark.usefixtures("quick_match_timeout")
def test_a_pattern_out_of_time_fails_the_dry_run(
    user_client: TestClient, mailbox: Mailbox, account: str
) -> None:
    """imap-thingy times the match out; the run records the kind, not the pattern."""
    mailbox.append("INBOX", (SHOP, "a" * 40 + "!", False))
    rule_set_id = user_client.post(f"/api/v1/accounts/{account}/rule-sets", json=rule_set()).json()[
        "id"
    ]
    spec = {
        "criterion": {"type": "subject_matches", "args": {"pattern": "(a|aa)+$"}},
        "action": {"type": "delete"},
    }
    add_filter(user_client, rule_set_id, "Backtracks", spec)
    response = user_client.post(f"/api/v1/rule-sets/{rule_set_id}/dry-run")
    assert (response.status_code, response.json()["detail"]["code"]) == (400, "match_timeout")
    [run] = user_client.get(f"/api/v1/rule-sets/{rule_set_id}/runs").json()
    assert run["error"].startswith("match_timeout: ")
    assert "(a|aa)" not in run["error"]


def test_a_dry_run_looks_at_the_newest_messages_only(mailbox: Mailbox) -> None:
    mailbox.append("INBOX", *[(SHOP, f"Sale {n}", False) for n in range(5)])
    account = imap_account_for(mailbox)
    folder = RuleSetFolder(account, ["INBOX"])
    outcome = dry_run(folder, [load_filter(SHOP_SPEC)], max_messages=3)
    assert outcome.limited_to_newest == 3
    assert [m["subject"] for m in outcome.report["filters"][0]["matched"]] == [
        "Sale 2",
        "Sale 3",
        "Sale 4",
    ]

    folder = RuleSetFolder(account, ["INBOX"])
    outcome = dry_run(folder, [load_filter(SHOP_SPEC)], max_messages=5)
    assert outcome.limited_to_newest is None
    assert len(outcome.report["filters"][0]["matched"]) == 5


def test_a_dry_run_out_of_time_is_stopped(mailbox: Mailbox) -> None:
    mailbox.append("INBOX", (SHOP, "Sale", False))
    folder = RuleSetFolder(imap_account_for(mailbox), ["INBOX"])
    with pytest.raises(DryRunTimeout):
        dry_run(folder, [load_filter(SHOP_SPEC)], seconds=0)


def imap_account_for(mailbox: Mailbox) -> Any:
    from mailroom_core.config import AccountConfig

    server = mailbox.server
    endpoint = {"host": server.host, "port": server.port, "tls": "implicit"}
    raw = new_account(login=mailbox.login, imap=endpoint) | {"auth": {"type": "password"}}
    del raw["password"]
    return imap_account(AccountConfig.model_validate(raw), server.password, mail_ssl_context())
