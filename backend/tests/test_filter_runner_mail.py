"""The worker's runs on real mail, in the Dovecot ``IMAP_TEST_HOST`` names (``mise run
test-backend-mail`` starts one); skipped without it."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from mailroom.accounts.store import load_accounts, user_store
from mailroom.models import AuditRecord, FilterRun, MutedThread
from mailroom.worker.idle import FolderWatch, WatchedFolder
from mailroom.worker.runs import execute_run
from mailroom.worker.tasks import queue_new_mail_runs
from mailroom_core.mail import TO_SORT, IdleChange, subject_sha256
from mailroom_core.testing import mime
from tests.support.mail import mail_servers_ssl_context
from tests.test_api_accounts import added, new_account
from tests.test_api_filters import SHOP_SPEC, add_filter
from tests.test_filter_runner import (
    add_rule_set,
    queued_run,
    run_out,
    set_run_filters,
)
from tests.test_filters_dry_run import BANK, SHOP, Mailbox, message
from tests.test_idle_supervisor import Heard

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
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
def account(user_client: TestClient, mailbox: Mailbox) -> int:
    """The mailbox as one of the user's accounts; its id."""
    server = mailbox.server
    endpoint = {"host": server.host, "port": server.port, "tls": "implicit"}
    body = new_account(login=mailbox.login, password=server.password, imap=endpoint)
    return added(user_client, body)


def has_folder(mailbox: Mailbox, folder: str) -> bool:
    conn = mailbox.client()
    try:
        return bool(conn.folder_exists(folder))
    finally:
        conn.logout()


def test_a_rule_set_for_all_accounts_moves_where_the_folder_is_and_fails_where_not(
    user_client: TestClient,
    db_session: Session,
    imap_server: ImapTestServer,
    mailbox: Mailbox,
    account: int,
    mailboxes: Mailboxes,
) -> None:
    """The run on each account is its own: the account without Archive fails, nothing being
    created there, and the other moves its mail all the same."""
    bare = Mailbox(imap_server, imap_server.unique_user("bare"))
    endpoint = {"host": imap_server.host, "port": imap_server.port, "tls": "implicit"}
    other = added(
        user_client,
        new_account("bare", login=bare.login, password=imap_server.password, imap=endpoint),
    )
    mailbox.append("INBOX", (SHOP, "Autumn sale", False))
    mailbox.append("Archive")
    bare.append("INBOX", (SHOP, "Winter sale", False))
    for each in (account, other):
        set_run_filters(user_client, each, on=True)
    rule_set_id = add_rule_set(user_client, "all")
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    response = user_client.post(f"/api/v1/rule-sets/{rule_set_id}/run")
    assert response.status_code == 202, response.json()
    for run in response.json():
        execute_run(db_session, run["id"], mailboxes)

    runs = {
        run["account_id"]: run
        for run in user_client.get(f"/api/v1/rule-sets/{rule_set_id}/runs").json()
    }
    assert runs[account]["error"] is None
    assert mailbox.subjects("Archive") == ["Autumn sale"]
    assert runs[other]["error"].startswith("mailbox_missing: ")
    assert bare.subjects("INBOX") == ["Winter sale"]
    assert not has_folder(bare, "Archive")


def test_a_filter_moves_to_another_account_and_the_copy_is_not_moved_back(
    user_client: TestClient,
    db_session: Session,
    imap_server: ImapTestServer,
    mailbox: Mailbox,
    account: int,
    mailboxes: Mailboxes,
    audited: Callable[[int], list[AuditRecord]],
    user: User,
) -> None:
    """The copy lands in the ledger, so the target's own rule set moving the same mail back
    leaves it where it arrived: no loop."""
    home_box = Mailbox(imap_server, imap_server.unique_user("home"))
    endpoint = {"host": imap_server.host, "port": imap_server.port, "tls": "implicit"}
    home = added(
        user_client,
        new_account("home", login=home_box.login, password=imap_server.password, imap=endpoint),
    )
    mailbox.append_raw("INBOX", mime("Autumn sale", sender=SHOP, message_id="<s@x.org>").encode())
    mailbox.append("INBOX", (BANK, "Statement", False))
    # No Message-ID: refused on its own, the other message still moving.
    mailbox.append_raw("INBOX", f"From: {SHOP}\r\nSubject: No id\r\n\r\nhi\r\n".encode())
    home_box.append_raw("INBOX", mime("Already home", sender=SHOP, message_id="<h@x.org>").encode())
    for each in (account, home):
        set_run_filters(user_client, each, on=True)
    shop = SHOP_SPEC["criterion"]
    out = add_rule_set(user_client, account, enabled=False)
    add_filter(
        user_client,
        out,
        "To home",
        {
            "criterion": shop,
            "action": {"type": "move_to_account", "args": {"account": home, "folder": ["INBOX"]}},
        },
    )
    back = add_rule_set(user_client, home, enabled=False)
    add_filter(
        user_client,
        back,
        "Back",
        {
            "criterion": shop,
            "action": {
                "type": "move_to_account",
                "args": {"account": account, "folder": ["INBOX"]},
            },
        },
    )
    for rule_set_id in (out, back):
        enabled = user_client.patch(f"/api/v1/rule-sets/{rule_set_id}", json={"enabled": True})
        assert enabled.status_code == 200, enabled.json()

    run_id = queued_run(user_client, out)
    execute_run(db_session, run_id, mailboxes)
    assert sorted(mailbox.subjects("INBOX")) == ["No id", "Statement"]
    assert mailbox.subjects("Trash") == ["Autumn sale"]
    assert sorted(home_box.subjects("INBOX")) == ["Already home", "Autumn sale"]
    [entry] = run_out(user_client, out, run_id)["result"]["filters"]
    [transfer] = entry["transfers"]
    assert sorted(m["result"] for m in transfer["messages"]) == ["ok", "unsupported"]
    [refused] = [m for m in transfer["messages"] if m["result"] != "ok"]
    assert "no Message-ID" in refused["reason"]
    recorded = sorted(
        (r.action, r.account_id, r.result) for r in audited(user.id) if r.run_id == run_id
    )
    assert recorded == [
        ("move_to_account", account, "ok"),
        ("move_to_account", account, "unsupported"),
        ("move_to_account", home, "ok"),
    ]

    # Home's run moves its own shop mail back, but not the copy it received.
    back_run = queued_run(user_client, back)
    execute_run(db_session, back_run, mailboxes)
    assert home_box.subjects("INBOX") == ["Autumn sale"]
    assert mailbox.subjects("INBOX") == ["Statement", "No id", "Already home"]
    assert user_client.get(f"/api/v1/rule-sets/{out}").json()["enabled"] is True


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
    account: int,
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
    assert (record.actor, record.account_id, record.result) == ("filter", account, "ok")
    assert (record.rule_set_id, record.run_id) == (rule_set_id, run_id)
    assert (record.detail["folder"], record.detail["filter"]) == ("INBOX", "Shop")
    assert record.detail["subject_sha256"] == subject_sha256("Autumn sale")
    assert "Autumn sale" not in str(record.detail)


def test_a_star_marked_run_handles_starred_mail_only(
    user_client: TestClient,
    db_session: Session,
    mailbox: Mailbox,
    account: int,
    mailboxes: Mailboxes,
) -> None:
    mailbox.append("INBOX", (SHOP, "Autumn sale", False), (SHOP, "Picks", True))
    mailbox.append("Archive")
    set_run_filters(user_client, account, on=True)
    rule_set_id = add_rule_set(user_client, account, marker="star")
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    run_id = queued_run(user_client, rule_set_id)
    execute_run(db_session, run_id, mailboxes)

    assert run_out(user_client, rule_set_id, run_id)["error"] is None
    assert mailbox.subjects("INBOX") == ["Autumn sale"]
    assert mailbox.subjects("Archive") == ["Picks"]
    assert starred(mailbox, "Archive") == []


def test_a_to_sort_run_handles_mail_marked_to_sort_only(
    user_client: TestClient,
    db_session: Session,
    mailbox: Mailbox,
    account: int,
    mailboxes: Mailboxes,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The test Dovecot keeps keywords, so the account takes a To sort rule set (the API asks)."""
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_servers_ssl_context())
    mailbox.append("INBOX", (SHOP, "Autumn sale", True))
    mailbox.append("Archive")
    conn = mailbox.client()
    try:
        conn.append("INBOX", message(SHOP, "Picks"), flags=[TO_SORT.encode()])
    finally:
        conn.logout()
    set_run_filters(user_client, account, on=True)
    rule_set_id = add_rule_set(user_client, account, marker="to_sort")
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    run_id = queued_run(user_client, rule_set_id)
    execute_run(db_session, run_id, mailboxes)

    assert run_out(user_client, rule_set_id, run_id)["error"] is None
    assert mailbox.subjects("INBOX") == ["Autumn sale"]
    assert mailbox.subjects("Archive") == ["Picks"]
    conn = mailbox.client()
    try:
        conn.select_folder("Archive", readonly=True)
        assert conn.search(["KEYWORD", TO_SORT]) == []
    finally:
        conn.logout()


def unread_subjects(mailbox: Mailbox) -> list[str]:
    conn = mailbox.client()
    try:
        conn.select_folder("INBOX", readonly=True)
        fetched = conn.fetch(conn.search("UNSEEN"), ["ENVELOPE"])
        return sorted(data[b"ENVELOPE"].subject.decode() for data in fetched.values())
    finally:
        conn.logout()


def test_a_muted_threads_new_mail_arrives_read_once_run_filters_is_on(
    user_client: TestClient,
    db_session: Session,
    user: User,
    mailbox: Mailbox,
    account: int,
    mailboxes: Mailboxes,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    audited: Callable[[int], list[AuditRecord]],
) -> None:
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_servers_ssl_context())
    mailbox.append_raw("INBOX", mime("Plans", message_id="<root@example.org>").encode())
    mail = f"/api/v1/mail/accounts/{account}/messages"
    [listed] = user_client.get(mail, params={"folder": "INBOX"}).json()["messages"]
    muted = user_client.post(f"/api/v1/mail/messages/{listed['ref']}/mute")
    assert muted.status_code == 201, muted.text
    replies = (
        ("Re: Plans", "<r1@example.org>", "<root@example.org>"),
        ("Re: Re: Plans", "<r2@example.org>", "<r1@example.org>"),
        ("Other", "<o@example.org>", "<elsewhere@example.org>"),
    )
    for subject, own, cited in replies:
        raw = mime(subject, message_id=own, in_reply_to=cited, references=cited)
        mailbox.append_raw("INBOX", raw.encode())

    # Muting rides on the account's runs: with Run filters off, nothing runs.
    assert queue_new_mail_runs(db_session, user.id, account, ["INBOX"]) == []
    set_run_filters(user_client, account, on=True)
    [run_id] = queue_new_mail_runs(db_session, user.id, account, ["INBOX"])
    db_session.commit()
    execute_run(db_session, run_id, mailboxes)

    # Marked read, never moved; the second reply cites only the first, which joined before it.
    assert unread_subjects(mailbox) == ["Other"]
    assert len(mailbox.subjects("INBOX")) == 4
    run = db_session.get_one(FilterRun, run_id)
    db_session.refresh(run)
    assert run.error is None
    assert run.result is not None
    assert [m["subject"] for m in run.result["filters"][0]["matched"]] == [
        "Plans",
        "Re: Plans",
        "Re: Re: Plans",
    ]
    [thread] = db_session.scalars(select(MutedThread)).all()
    db_session.refresh(thread)
    assert thread.message_ids == ["<root@example.org>", "<r1@example.org>", "<r2@example.org>"]
    records = db_session.scalars(
        select(AuditRecord).where(AuditRecord.run_id == run_id).order_by(AuditRecord.id)
    ).all()
    assert [(r.actor, r.detail["filter"]) for r in records] == [("filter", "Muted threads")] * 3


def test_a_watched_folder_queues_runs_that_move_new_mail(
    user_client: TestClient,
    db_session: Session,
    user: User,
    mailbox: Mailbox,
    account: int,
    mailboxes: Mailboxes,
) -> None:
    """mailroom-core's IdleWatch on the folder: once on connecting, then on new mail. The runs are
    queued and carried out here, as the supervisor and the worker's jobs would."""
    set_run_filters(user_client, account, on=True)
    rule_set_id = add_rule_set(user_client, account, trigger="new_mail")
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    folder = WatchedFolder(user.id, account, "INBOX")
    mailbox.append("Archive")

    calls = 0
    changed = threading.Condition()

    class Reactor:
        def connected(self) -> None:
            self.changed(None)

        def changed(self, change: IdleChange | None) -> None:
            nonlocal calls
            if change is not None and not change.new_mail:
                return
            with changed:
                calls += 1
                changed.notify_all()

        def delimiter(self, _delimiter: str | None) -> None:
            pass

    def wait_for(count: int) -> None:
        with changed:
            assert changed.wait_for(lambda: calls >= count, WAIT_SECONDS)

    revision = load_accounts(user_store(mailboxes.engine, user.id)).revision
    watch = FolderWatch(mailboxes, folder, revision, Reactor())
    try:
        wait_for(1)
        assert watch.connected
        # The lease ended with the delimiter's lookup: the pool logs that connection out, and
        # the watch, on a connection of its own, goes on.
        pool = mailboxes.pool
        assert pool.close_idle(time.monotonic() + pool.idle_seconds + 1) == 1
        mailbox.append("INBOX", (SHOP, "Autumn sale", False), (BANK, "Statement", False))
        wait_for(2)
    finally:
        watch.stop()
        watch.join(WAIT_SECONDS)
    assert not watch.alive
    assert watch.failure is None

    [run_id] = queue_new_mail_runs(db_session, user.id, account, [folder.path])
    execute_run(db_session, run_id, mailboxes)
    run = run_out(user_client, rule_set_id, run_id)
    assert (run["trigger"], run["error"]) == ("new_mail", None)
    assert mailbox.subjects("INBOX") == ["Statement"]
    assert mailbox.subjects("Archive") == ["Autumn sale"]


def test_a_watch_on_a_missing_folder_ends_with_its_code(
    user: User, account: int, mailboxes: Mailboxes
) -> None:
    folder = WatchedFolder(user.id, account, "Nowhere")
    revision = load_accounts(user_store(mailboxes.engine, user.id)).revision
    heard = Heard()
    watch = FolderWatch(mailboxes, folder, revision, heard)
    watch.join(WAIT_SECONDS)
    assert not watch.alive
    assert not watch.connected
    assert (watch.failure or "").startswith("mailbox_missing: ")
    # The delimiter was looked up; nothing else came.
    assert [event for event in heard.events if event != ("delimiter", "/")] == []
