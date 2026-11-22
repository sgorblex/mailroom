"""Vacation replies: the account's settings over REST, the index sync's new INBOX rows queueing
``vacation_reply``, and the job answering through the send gate, against real Dovecot and Mailpit
(skipped without them): one reply per sender, none to a list. The job's body runs here directly,
as procrastinate would run it."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select, text

from mailroom.accounts.app_data import forget_user_accounts
from mailroom.context import app_context
from mailroom.mail import service as mail_service
from mailroom.mail import vacation
from mailroom.mail.vacation import claim_reply, queue_vacation_replies
from mailroom.models import Vacation, VacationReplied
from mailroom.worker import vacation as worker_vacation
from mailroom.worker.app import PROCRASTINATE_SCHEMA
from mailroom.worker.snoozes import wake_snooze
from mailroom.worker.tasks import VACATION_REPLY
from mailroom.worker.vacation import reply_vacation
from mailroom_core.mail import MailError, format_ref, parse_ref
from mailroom_core.testing import SmtpTestServer, create_folders, mime
from tests.support.mail import mail_servers_ssl_context
from tests.test_api_compose import (
    ALIAS,
    ME,
    Composer,
    account_fields,
    add_account,
    smtp_endpoint,
)
from tests.test_mail_index_sync import synced
from tests.test_mail_index_threads import indexed

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.testclient import TestClient

    from mailroom.models import AuditRecord, User
    from mailroom.worker.context import Mailboxes
    from mailroom_core.testing import ImapTestServer

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("numbered_accounts")]

VACATION = "/api/v1/accounts/1/vacation"
AWAY = {"enabled": True, "subject": "Away", "html": "<p>Back on <b>Monday</b>.</p>"}


@pytest.fixture
def composer(
    user_client: TestClient,
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    application: FastAPI,
    audited: Callable[[int], list[AuditRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> Composer:
    """The signed-in user's account ``work``; the service's sessions are the test's."""
    _ = audited
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_servers_ssl_context())
    login = imap_server.unique_user("vacation")
    create_folders(imap_server, login, "Drafts", "Sent", "Trash", "Projects")
    mine = Composer(user_client, imap_server, login, application)
    add_account(mine, smtp=smtp_endpoint(smtp_server))
    return mine


def stranger() -> str:
    return f"away-{uuid.uuid4().hex}@example.org"


def incoming(sender: str, *, to: str = ME, extra: str = "", subject: str = "Lunch?") -> str:
    return f"Return-Path: <{sender}>\r\n{extra}" + mime(
        subject, sender=f"Friend <{sender}>", to=to, message_id=f"<{uuid.uuid4().hex}@example.org>"
    )


def queued(db: Session) -> list[dict[str, Any]]:
    query = text(
        f"SELECT args FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs "
        "WHERE task_name = :name AND status = 'todo' ORDER BY id"
    )
    return [row[0] for row in db.execute(query, {"name": VACATION_REPLY})]


def vacation_records(audited: Callable[[int], list[AuditRecord]], user: User) -> list[Any]:
    return [record for record in audited(user.id) if record.action == "vacation_reply"]


def test_the_settings_are_kept_and_checked(composer: Composer, user_client: TestClient) -> None:
    off = user_client.get(VACATION)
    assert off.status_code == 200
    assert off.json() == {
        "enabled": False,
        "starts_on": None,
        "ends_on": None,
        "subject": "",
        "html": "",
        "text": "",
        "identity": None,
        "answering": False,
        "indexed": False,
    }
    for change in (
        {"enabled": True, "subject": "", "html": "<p>x</p>"},
        {"enabled": True, "subject": "Away", "html": "<p> </p>"},
        AWAY | {"identity": "stranger@example.org"},
        AWAY | {"starts_on": "2026-12-10", "ends_on": "2026-12-01"},
    ):
        refused = user_client.put(VACATION, json=change)
        assert refused.status_code == 400, change
        assert refused.json()["detail"]["code"] == "invalid_vacation"

    later = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    kept = user_client.put(
        VACATION,
        json=AWAY
        | {"identity": ALIAS.upper(), "html": '<p onclick="x()">Back <img src="cid:a"></p>'},
    )
    assert kept.status_code == 200, kept.text
    assert kept.json() | {"html": None} == {
        "enabled": True,
        "starts_on": None,
        "ends_on": None,
        "subject": "Away",
        "html": None,
        "text": "Back",
        "identity": ALIAS,
        "answering": True,
        "indexed": False,
    }
    assert "onclick" not in kept.json()["html"]
    assert "img" not in kept.json()["html"]
    waiting = user_client.put(VACATION, json=AWAY | {"starts_on": later})
    assert waiting.json()["answering"] is False
    assert user_client.get(VACATION).json()["starts_on"] == later
    assert user_client.get("/api/v1/accounts/99/vacation").status_code == 404


def test_new_inbox_mail_queues_a_reply_while_the_vacation_answers(
    composer: Composer,
    user_client: TestClient,
    user: User,
    db_session: Session,
    mailboxes: Mailboxes,
) -> None:
    composer.seed(incoming(stranger()))
    synced(db_session, mailboxes, user, 1)
    assert queued(db_session) == []  # a first sync: nothing is new mail
    assert user_client.get(VACATION).json()["indexed"] is True

    composer.seed(incoming(stranger()))
    synced(db_session, mailboxes, user, 1)
    assert queued(db_session) == []  # the vacation is off

    assert user_client.put(VACATION, json=AWAY).status_code == 200
    ref = composer.seed(incoming(stranger()))
    composer.seed(incoming(stranger()), "Projects")
    synced(db_session, mailboxes, user, 1)
    synced(db_session, mailboxes, user, 1, "Projects")
    synced(db_session, mailboxes, user, 1, "Projects")
    assert queued(db_session) == [{"user_id": user.id, "account_id": 1, "ref": ref}]

    # Past its last day, new mail queues nothing.
    ended = (datetime.now(UTC) - timedelta(days=2)).date().isoformat()
    assert user_client.put(VACATION, json=AWAY | {"ends_on": ended}).status_code == 200
    composer.seed(incoming(stranger()))
    synced(db_session, mailboxes, user, 1)
    assert len(queued(db_session)) == 1


def test_a_reply_goes_once_per_sender_through_the_gate(
    composer: Composer,
    user_client: TestClient,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    smtp_server: SmtpTestServer,
    audited: Callable[[int], list[AuditRecord]],
) -> None:
    friend = stranger()
    ref = composer.seed(incoming(friend, to=f"Me <{ALIAS}>"))
    assert reply_vacation(sessions, mailboxes, user.id, 1, ref) == "off"
    assert vacation_records(audited, user) == []

    assert user_client.put(VACATION, json=AWAY).status_code == 200
    assert reply_vacation(sessions, mailboxes, user.id, 1, ref) == "ok"

    [delivered] = smtp_server.delivered_to(friend)
    assert delivered.recipients == [friend]
    assert b"\r\nAuto-Submitted: auto-replied\r\n" in delivered.raw
    assert b"\r\nIn-Reply-To: <" in delivered.raw
    assert b"\r\nSubject: Away\r\n" in delivered.raw
    assert f"From: Alias <{ALIAS}>".encode() in delivered.raw  # the identity it was sent to
    assert len(composer.contents("Sent")) == 1
    [sent] = vacation_records(audited, user)
    assert (sent.actor, sent.result, sent.detail["delivery"]) == ("user", "ok", "sent")

    # The same message again, and another from the same sender: within 4 days, no reply.
    again = composer.seed(incoming(friend))
    assert reply_vacation(sessions, mailboxes, user.id, 1, ref) == "replied_recently"
    assert reply_vacation(sessions, mailboxes, user.id, 1, again) == "replied_recently"
    assert len(smtp_server.delivered_to(friend)) == 1
    refused = vacation_records(audited, user)[1:]
    assert [(r.result, r.detail["from"]) for r in refused] == [("replied_recently", friend)] * 2

    # Four days on, the sender is answered again.
    db_session.execute(
        VacationReplied.__table__.update().values(at=datetime.now(UTC) - timedelta(days=4))
    )
    db_session.commit()
    assert reply_vacation(sessions, mailboxes, user.id, 1, again) == "ok"
    assert len(smtp_server.delivered_to(friend)) == 2


def test_no_reply_to_a_list_and_none_with_sending_off(
    composer: Composer,
    user_client: TestClient,
    user: User,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    smtp_server: SmtpTestServer,
    audited: Callable[[int], list[AuditRecord]],
    db_session: Session,
) -> None:
    assert user_client.put(VACATION, json=AWAY).status_code == 200
    lister = stranger()
    listed = composer.seed(incoming(lister, extra="List-Id: Friends <friends.example.org>\r\n"))
    assert reply_vacation(sessions, mailboxes, user.id, 1, listed) == "mailing_list"
    assert smtp_server.delivered_to(lister) == []
    [record] = vacation_records(audited, user)
    assert (record.result, record.detail["from"]) == ("mailing_list", lister)
    assert record.detail["uid"] == parse_ref(listed).uid
    assert db_session.scalars(select(VacationReplied)).all() == []

    friend = stranger()
    ref = composer.seed(incoming(friend))
    fields = account_fields(composer)
    fields.pop("password")
    changed = composer.client.put(
        "/api/v1/accounts/1",
        json=fields | {"send_mode": "off", "smtp": smtp_endpoint(smtp_server)},
    )
    assert changed.status_code == 200, changed.text
    assert reply_vacation(sessions, mailboxes, user.id, 1, ref) == "send_disabled"
    assert smtp_server.delivered_to(friend) == []


def test_removing_the_account_forgets_its_vacation(
    composer: Composer, user_client: TestClient, user: User, db_session: Session
) -> None:
    assert user_client.put(VACATION, json=AWAY).status_code == 200
    assert user_client.delete("/api/v1/accounts/1").status_code == 204
    db_session.expire_all()
    assert db_session.scalars(select(Vacation)).all() == []


def test_a_failure_to_queue_replies_costs_the_index_nothing(
    composer: Composer,
    user_client: TestClient,
    user: User,
    db_session: Session,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synced(db_session, mailboxes, user, 1)
    assert user_client.put(VACATION, json=AWAY).status_code == 200

    def broken(*_args: object) -> int:
        raise RuntimeError

    monkeypatch.setattr(vacation, "queue_vacation_replies", broken)
    composer.seed(incoming(stranger()))

    assert synced(db_session, mailboxes, user, 1).message_count == 1
    assert queued(db_session) == []


def test_gmail_all_mail_rows_labelled_inbox_queue_a_reply(
    composer: Composer, user_client: TestClient, user: User, db_session: Session
) -> None:
    assert user_client.put(VACATION, json=AWAY).status_code == 200
    now = datetime.now(UTC)
    all_mail = "[Gmail]/All Mail"
    inbox = indexed(1, "Hi", folder=all_mail, received=now, gmail_labels=("\\Inbox",))
    archived = indexed(2, "Hi", folder=all_mail, received=now, gmail_labels=())

    assert queue_vacation_replies(db_session, user.id, 1, [inbox, archived]) == 1

    assert queued(db_session) == [
        {"user_id": user.id, "account_id": 1, "ref": format_ref(inbox.message_ref)}
    ]


def test_a_snoozed_message_woken_into_inbox_gets_no_reply(
    composer: Composer,
    user_client: TestClient,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
) -> None:
    synced(db_session, mailboxes, user, 1)
    assert user_client.put(VACATION, json=AWAY).status_code == 200
    ref = composer.seed(incoming(stranger()))
    synced(db_session, mailboxes, user, 1)
    assert queued(db_session) == [{"user_id": user.id, "account_id": 1, "ref": ref}]
    ctx = app_context(composer.application.state)
    assert ctx is not None
    snoozed = mail_service.snooze(ctx, user.id, ref, datetime.now(UTC) + timedelta(hours=1))

    assert wake_snooze(sessions, mailboxes, int(snoozed.snooze_id)) == "ok"
    synced(db_session, mailboxes, user, 1)

    # Back in INBOX under a new UID, a new row there, yet it came back: it did not arrive.
    [back] = composer.contents("INBOX")
    assert back.uid != parse_ref(ref).uid
    assert queued(db_session) == [{"user_id": user.id, "account_id": 1, "ref": ref}]


@pytest.mark.parametrize("mode", ["drafts", "approval"])
def test_a_reply_goes_as_the_user_in_any_send_mode_but_off(
    composer: Composer,
    user_client: TestClient,
    user: User,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    smtp_server: SmtpTestServer,
    mode: str,
) -> None:
    assert user_client.put(VACATION, json=AWAY).status_code == 200
    fields = account_fields(composer)
    fields.pop("password")
    changed = composer.client.put(
        "/api/v1/accounts/1", json=fields | {"send_mode": mode, "smtp": smtp_endpoint(smtp_server)}
    )
    assert changed.status_code == 200, changed.text
    friend = stranger()

    assert reply_vacation(sessions, mailboxes, user.id, 1, composer.seed(incoming(friend))) == "ok"

    assert len(smtp_server.delivered_to(friend)) == 1


def test_a_reply_the_gate_refuses_gives_its_sender_back(
    composer: Composer,
    user_client: TestClient,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    smtp_server: SmtpTestServer,
    migrated_engine: Engine,
) -> None:
    assert user_client.put(VACATION, json=AWAY).status_code == 200
    composer.set_limits(migrated_engine, user.id, send_per_hour=1)
    first, second = stranger(), stranger()
    assert reply_vacation(sessions, mailboxes, user.id, 1, composer.seed(incoming(first))) == "ok"

    limited = reply_vacation(sessions, mailboxes, user.id, 1, composer.seed(incoming(second)))

    assert limited == "rate_limited"
    assert smtp_server.delivered_to(second) == []
    db_session.expire_all()
    assert [row.sender for row in db_session.scalars(select(VacationReplied))] == [first]


class FailingSmtp:
    """An SMTP sender whose every delivery ends in ``error``."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def send(self, *_args: object) -> None:
        raise self.error


@pytest.mark.parametrize(
    ("error", "outcome", "kept"),
    [
        (MailError("unreachable"), "unreachable", False),
        (MailError("auth_failed"), "auth_failed", False),
        (MailError("recipient_refused"), "recipient_refused", False),
        (MailError("delivery_unknown"), "delivery_unknown", True),
        (RuntimeError(), "delivery_unknown", True),
    ],
)
def test_a_delivery_that_surely_failed_gives_its_sender_back(
    composer: Composer,
    user_client: TestClient,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    outcome: str,
    kept: bool,
) -> None:
    """Failed before the server took it, the reply did not leave: the sender can be answered
    again. Unknown, it may have, so the claim stays."""
    assert user_client.put(VACATION, json=AWAY).status_code == 200
    monkeypatch.setattr(worker_vacation, "smtp_sender", lambda *_args: FailingSmtp(error))
    friend = stranger()

    assert (
        reply_vacation(sessions, mailboxes, user.id, 1, composer.seed(incoming(friend))) == outcome
    )

    db_session.expire_all()
    claimed = [row.sender for row in db_session.scalars(select(VacationReplied))]
    assert claimed == ([friend] if kept else [])


def test_answered_senders_are_forgotten_once_they_decide_nothing(
    composer: Composer, user_client: TestClient, user: User, db_session: Session
) -> None:
    assert user_client.put(VACATION, json=AWAY).status_code == 200
    now = datetime.now(UTC)
    old, recent, new = stranger(), stranger(), stranger()
    assert claim_reply(db_session, user.id, 1, old, now - timedelta(days=5))
    assert claim_reply(db_session, user.id, 1, recent, now - timedelta(days=1))

    assert claim_reply(db_session, user.id, 1, new, now)

    db_session.expire_all()
    kept = {row.sender for row in db_session.scalars(select(VacationReplied))}
    assert kept == {recent, new}


def test_deleting_the_user_forgets_their_vacations(
    composer: Composer, user_client: TestClient, user: User, db_session: Session
) -> None:
    assert user_client.put(VACATION, json=AWAY).status_code == 200
    assert claim_reply(db_session, user.id, 1, stranger(), datetime.now(UTC))

    forget_user_accounts(db_session, user.id)

    db_session.expire_all()
    assert db_session.scalars(select(Vacation)).all() == []
    assert db_session.scalars(select(VacationReplied)).all() == []
