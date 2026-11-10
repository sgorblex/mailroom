"""The worker's runs on real mail, in the Dovecot ``IMAP_TEST_HOST`` names (the dev one:
``IMAP_TEST_PORT=33993``, ``MAILROOM_MAIL_CA_FILE`` its CA); skipped without it."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from mailroom.models import AuditRecord
from mailroom.worker.idle import MonitorWatch, WatchedFolder
from mailroom.worker.runs import execute_run
from mailroom.worker.tasks import queue_new_mail_runs
from mailroom_core.mail.audit import subject_sha256
from tests.test_api_accounts import new_account
from tests.test_api_filters import SHOP_SPEC, add_filter
from tests.test_filter_runner import add_rule_set, queued_run, run_out, set_run_filters
from tests.test_filters_dry_run import BANK, SHOP, Mailbox

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User
    from mailroom.worker.context import Mailboxes
    from mailroom_core.testing import ImapTestServer

WAIT_SECONDS = 20.0


@pytest.fixture
def mailbox(imap_server: ImapTestServer) -> Mailbox:
    return Mailbox(imap_server, imap_server.unique_user("runs"))


@pytest.fixture
def account(user_client: TestClient, mailbox: Mailbox) -> str:
    """The mailbox as the user's account ``work``."""
    server = mailbox.server
    endpoint = {"host": server.host, "port": server.port, "tls": "implicit"}
    body = new_account(login=mailbox.login, password=server.password, imap=endpoint)
    assert user_client.post("/api/v1/accounts", json=body).status_code == 201
    return "work"


def starred(mailbox: Mailbox, folder: str) -> list[int]:
    conn = mailbox.client()
    try:
        conn.select_folder(folder, readonly=True)
        return list(conn.search("FLAGGED"))
    finally:
        conn.logout()


def test_a_run_acts_on_the_folder_and_reports_it(
    user_client: TestClient,
    user: User,
    db_session: Session,
    mailbox: Mailbox,
    account: str,
    mailboxes: Mailboxes,
) -> None:
    mailbox.append("INBOX", (SHOP, "Autumn sale", False), (BANK, "Statement", False))
    mailbox.append("Archive", (BANK, "Old", False))
    set_run_filters(user_client, account, on=True)
    rule_set_id = add_rule_set(user_client, account)
    shop = add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    run_id = queued_run(user_client, rule_set_id)
    execute_run(db_session, run_id, mailboxes)

    assert mailbox.subjects("INBOX") == ["Statement"]
    assert mailbox.subjects("Archive") == ["Old", "Autumn sale"]
    run = run_out(user_client, rule_set_id, run_id)
    assert (run["error"], run["dry_run"], run["trigger"]) == (None, False, "manual")
    [entry] = run["result"]["filters"]
    assert entry["filter_id"] == shop
    assert entry["executed"] is True
    assert [m["subject"] for m in entry["matched"]] == ["Autumn sale"]
    [record] = db_session.scalars(select(AuditRecord).where(AuditRecord.user_id == user.id))
    assert (record.actor, record.account_id, record.result) == ("filter", "work", "ok")
    assert (record.rule_set_id, record.run_id) == (rule_set_id, run_id)
    assert (record.detail["folder"], record.detail["filter"]) == ("INBOX", "Shop")
    assert record.detail["subject_sha256"] == subject_sha256("Autumn sale")
    assert "Autumn sale" not in str(record.detail)


def test_a_starred_only_run_handles_starred_mail_only(
    user_client: TestClient,
    db_session: Session,
    mailbox: Mailbox,
    account: str,
    mailboxes: Mailboxes,
) -> None:
    mailbox.append("INBOX", (SHOP, "Autumn sale", False), (SHOP, "Picks", True))
    mailbox.append("Archive")
    set_run_filters(user_client, account, on=True)
    rule_set_id = add_rule_set(user_client, account, starred_only=True)
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    run_id = queued_run(user_client, rule_set_id)
    execute_run(db_session, run_id, mailboxes)

    assert run_out(user_client, rule_set_id, run_id)["error"] is None
    assert mailbox.subjects("INBOX") == ["Autumn sale"]
    assert mailbox.subjects("Archive") == ["Picks"]
    assert starred(mailbox, "Archive") == []


def test_a_watched_folder_queues_runs_that_move_new_mail(
    user_client: TestClient,
    db_session: Session,
    user: User,
    mailbox: Mailbox,
    account: str,
    mailboxes: Mailboxes,
) -> None:
    """imap-thingy's monitor on the folder: once on connecting, then on new mail. The runs are
    queued and carried out here, as the supervisor and the worker's jobs would."""
    set_run_filters(user_client, account, on=True)
    rule_set_id = add_rule_set(user_client, account, trigger="new_mail")
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    folder = WatchedFolder(user.id, account, ("INBOX",))
    mailbox.append("Archive")

    calls = 0
    changed = threading.Condition()

    def on_new_mail() -> None:
        nonlocal calls
        with changed:
            calls += 1
            changed.notify_all()

    def wait_for(count: int) -> None:
        with changed:
            assert changed.wait_for(lambda: calls >= count, WAIT_SECONDS)

    watch = MonitorWatch(folder, mailboxes.account(user.id, account), on_new_mail)
    try:
        wait_for(1)
        mailbox.append("INBOX", (SHOP, "Autumn sale", False), (BANK, "Statement", False))
        wait_for(2)
    finally:
        watch.stop()
        watch.join()
    assert not watch.alive()

    [run_id] = queue_new_mail_runs(db_session, user.id, account, folder.folder)
    execute_run(db_session, run_id, mailboxes)
    run = run_out(user_client, rule_set_id, run_id)
    assert (run["trigger"], run["error"]) == ("new_mail", None)
    assert mailbox.subjects("INBOX") == ["Statement"]
    assert mailbox.subjects("Archive") == ["Autumn sale"]
