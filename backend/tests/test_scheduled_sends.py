"""Held sends: scheduled through the mail service, sent by the worker's ``send_draft`` job through
the send gate, against real Dovecot and Mailpit (skipped without them). The job's body runs here
directly, as procrastinate would run it."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select, text

from mailroom.context import app_context
from mailroom.errors import Refusal
from mailroom.mail import service
from mailroom.models import ScheduledSend
from mailroom.worker.app import PROCRASTINATE_SCHEMA
from mailroom.worker.sends import send_scheduled
from mailroom.worker.tasks import (
    SEND_DRAFT,
    SYNC_FOLDER,
    queue_folder_sync,
    requeue_interrupted_sends,
)
from mailroom_core import send as gate
from mailroom_core.mail import parse_ref
from mailroom_core.testing import SmtpTestServer, create_folders
from tests.support.mail import mail_servers_ssl_context
from tests.test_api_compose import Composer, add_account, address, smtp_endpoint

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.testclient import TestClient

    from mailroom.context import AppContext
    from mailroom.models import AuditRecord, User
    from mailroom.worker.context import Mailboxes
    from mailroom_core.testing import ImapTestServer

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("numbered_accounts")]


class Crash(BaseException):
    """The worker going away: nothing in it catches this."""


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
    login = imap_server.unique_user("scheduled")
    create_folders(imap_server, login, "Drafts", "Sent", "Trash")
    mine = Composer(user_client, imap_server, login, application)
    add_account(mine, smtp=smtp_endpoint(smtp_server))
    return mine


def context(composer: Composer) -> AppContext:
    ctx = app_context(composer.application.state)
    assert ctx is not None
    return ctx


def draft_to(composer: Composer, to: str) -> str:
    return composer.save(to=[address(to)], subject="Later", text="Hello")["ref"]


def job(db: Session, job_id: int | None) -> Any:
    return db.execute(
        text(
            "SELECT status, task_name, scheduled_at, lock, args"
            f" FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs WHERE id = :id"
        ),
        {"id": job_id},
    ).one_or_none()


def row(db: Session, scheduled_id: str) -> ScheduledSend | None:
    db.expire_all()
    return db.get(ScheduledSend, int(scheduled_id))


def refused_sends(records: list[AuditRecord]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (record.actor, record.result, record.detail)
        for record in records
        if record.action == "send" and record.detail.get("delivery") == "refused"
    ]


def refused_record(ref: str, code: str, actor: str = "user") -> tuple[str, str, dict[str, Any]]:
    """What a held send that ended before the gate records: its code and the draft's place."""
    draft = parse_ref(ref)
    return (
        actor,
        code,
        {"folder": draft.folder, "uid": draft.uid, "by": actor, "delivery": "refused"},
    )


def stranger() -> str:
    return f"later-{uuid.uuid4().hex}@example.org"


def test_a_scheduled_send_waits_for_its_job_and_is_delivered_once(
    composer: Composer,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    smtp_server: SmtpTestServer,
    audited: Callable[[int], list[AuditRecord]],
) -> None:
    to = stranger()
    ref = draft_to(composer, to)
    send_at = datetime.now(UTC) + timedelta(hours=1)

    held = service.schedule_send(context(composer), user.id, ref, send_at)

    assert (held.state, held.ref, held.account, held.send_at) == ("held", ref, 1, send_at)
    assert service.list_scheduled(context(composer), user.id) == [held]
    stored = row(db_session, held.scheduled_id)
    assert stored is not None
    queued = job(db_session, stored.job_id)
    assert queued.status == "todo"
    assert queued.task_name == SEND_DRAFT
    assert queued.scheduled_at == send_at
    assert queued.lock is None
    assert queued.args == {"scheduled_id": int(held.scheduled_id)}
    assert smtp_server.delivered_to(to) == []

    assert send_scheduled(sessions, mailboxes, int(held.scheduled_id)) == "ok"

    [delivered] = smtp_server.delivered_to(to)
    assert b"X-Mailroom-Draft" not in delivered.raw
    assert composer.contents("Drafts") == []
    assert len(composer.contents("Sent")) == 1
    assert row(db_session, held.scheduled_id) is None
    assert service.list_scheduled(context(composer), user.id) == []
    records = audited(user.id)
    assert {(record.actor, record.action) for record in records} == {("user", "send")}
    assert any(record.detail.get("delivery") == "sent" for record in records)

    # The job run again, as procrastinate could: the row is gone, and so is nothing else.
    assert send_scheduled(sessions, mailboxes, int(held.scheduled_id)) == "gone"
    assert len(smtp_server.delivered_to(to)) == 1


def test_a_send_run_again_after_a_crash_between_claim_and_delivery_does_not_resend(
    composer: Composer,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    smtp_server: SmtpTestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    to = stranger()
    ref = draft_to(composer, to)
    held = service.schedule_send(context(composer), user.id, ref, datetime.now(UTC))

    def crash(*_args: object) -> None:
        raise Crash

    with monkeypatch.context() as patched:
        patched.setattr(gate, "deliver", crash)
        with pytest.raises(Crash):
            send_scheduled(sessions, mailboxes, int(held.scheduled_id))
    stored = row(db_session, held.scheduled_id)
    assert stored is not None
    assert stored.state == "sending"
    with pytest.raises(Refusal) as refused:
        service.cancel_scheduled(context(composer), user.id, held.scheduled_id)
    assert refused.value.code == "send_started"

    # The next worker marks the stopped one's job failed, then queues the send again.
    first_job = stored.job_id
    path = db_session.execute(text("SELECT current_setting('search_path')")).scalar_one()
    # Its triggers name procrastinate's tables unqualified.
    db_session.execute(
        text("SELECT set_config('search_path', :path, true)"),
        {"path": f"{PROCRASTINATE_SCHEMA}, {path}"},
    )
    db_session.execute(
        text("UPDATE procrastinate_jobs SET status = 'failed' WHERE id = :id"), {"id": first_job}
    )
    db_session.execute(text("SELECT set_config('search_path', :path, true)"), {"path": path})
    with sessions() as db:
        assert requeue_interrupted_sends(db) == [int(held.scheduled_id)]
    stored = row(db_session, held.scheduled_id)
    assert stored is not None
    assert stored.job_id != first_job
    assert job(db_session, stored.job_id).status == "todo"

    assert send_scheduled(sessions, mailboxes, int(held.scheduled_id)) == "delivery_unknown"

    assert smtp_server.delivered_to(to) == []
    assert len(composer.contents("Drafts")) == 1
    stored = row(db_session, held.scheduled_id)
    assert stored is not None
    assert (stored.state, stored.code) == ("failed", "delivery_unknown")


def test_a_send_held_for_later_holds_back_no_job_of_its_mailbox(
    composer: Composer, user: User, db_session: Session
) -> None:
    service.schedule_send(
        context(composer),
        user.id,
        draft_to(composer, stranger()),
        datetime.now(UTC) + timedelta(days=1),
    )
    assert queue_folder_sync(db_session, user.id, 1, 1)
    path = db_session.execute(text("SELECT current_setting('search_path')")).scalar_one()
    db_session.execute(
        text("SELECT set_config('search_path', :path, true)"),
        {"path": f"{PROCRASTINATE_SCHEMA}, {path}"},
    )

    fetched = db_session.execute(
        text("SELECT task_name FROM procrastinate_fetch_job_v2(NULL, NULL)")
    ).scalar_one()

    assert fetched == SYNC_FOLDER


def test_a_draft_sent_at_once_while_held_leaves_the_held_send_nothing_to_do(
    composer: Composer,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    smtp_server: SmtpTestServer,
) -> None:
    to = stranger()
    ref = draft_to(composer, to)
    held = service.schedule_send(
        context(composer), user.id, ref, datetime.now(UTC) + timedelta(hours=1)
    )
    assert composer.sent(ref)["state"] == "sent"

    assert send_scheduled(sessions, mailboxes, int(held.scheduled_id)) == "ok"

    assert len(smtp_server.delivered_to(to)) == 1
    assert row(db_session, held.scheduled_id) is None


def test_an_unexpected_error_leaves_the_held_send_failed(
    composer: Composer,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    smtp_server: SmtpTestServer,
    monkeypatch: pytest.MonkeyPatch,
    audited: Callable[[int], list[AuditRecord]],
) -> None:
    to = stranger()
    ref = draft_to(composer, to)
    held = service.schedule_send(context(composer), user.id, ref, datetime.now(UTC))

    def broken(*_args: object) -> None:
        raise RuntimeError

    monkeypatch.setattr(gate, "prepare_draft", broken)

    assert send_scheduled(sessions, mailboxes, int(held.scheduled_id)) == "internal"

    stored = row(db_session, held.scheduled_id)
    assert stored is not None
    assert (stored.state, stored.code) == ("failed", "internal")
    assert smtp_server.delivered_to(to) == []
    assert refused_sends(audited(user.id)) == [refused_record(ref, "internal")]


def test_cancel_deletes_the_job_and_leaves_the_draft(
    composer: Composer,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    smtp_server: SmtpTestServer,
) -> None:
    to = stranger()
    ref = draft_to(composer, to)
    ctx = context(composer)
    held = service.schedule_send(ctx, user.id, ref, datetime.now(UTC) + timedelta(seconds=10))
    stored = row(db_session, held.scheduled_id)
    assert stored is not None
    job_id = stored.job_id

    with pytest.raises(Refusal) as twice:
        service.schedule_send(ctx, user.id, ref, datetime.now(UTC))
    assert twice.value.code == "already_scheduled"

    assert service.cancel_scheduled(ctx, user.id, held.scheduled_id) == held

    assert job(db_session, job_id) is None
    assert row(db_session, held.scheduled_id) is None
    assert send_scheduled(sessions, mailboxes, int(held.scheduled_id)) == "gone"
    assert smtp_server.delivered_to(to) == []
    assert composer.read(ref)["send"] is None
    with pytest.raises(Refusal) as gone:
        service.cancel_scheduled(ctx, user.id, held.scheduled_id)
    assert gone.value.code == "not_found"


def test_a_send_whose_draft_is_gone_fails_and_stays_listed(
    composer: Composer,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    audited: Callable[[int], list[AuditRecord]],
) -> None:
    ref = draft_to(composer, stranger())
    ctx = context(composer)
    held = service.schedule_send(ctx, user.id, ref, datetime.now(UTC))
    # Saving it again elsewhere would leave the scheduled version gone.
    composer.client.delete(f"/api/v1/mail/drafts/{ref}")

    assert send_scheduled(sessions, mailboxes, int(held.scheduled_id)) == "not_found"
    assert refused_sends(audited(user.id)) == [refused_record(ref, "not_found")]

    [listed] = service.list_scheduled(ctx, user.id)
    assert (listed.state, listed.code) == ("failed", "not_found")
    fresh = draft_to(composer, stranger())
    again = service.schedule_send(ctx, user.id, fresh, datetime.now(UTC))
    assert [view.scheduled_id for view in service.list_scheduled(ctx, user.id)] == [
        listed.scheduled_id,
        again.scheduled_id,
    ]


def test_scheduling_is_checked_and_another_users_send_is_not_found(
    composer: Composer, user: User, admin: User, db_session: Session
) -> None:
    ref = draft_to(composer, stranger())
    ctx = context(composer)
    with pytest.raises(Refusal) as naive:
        service.schedule_send(ctx, user.id, ref, datetime.now())  # noqa: DTZ005
    assert naive.value.code == "invalid_argument"
    with pytest.raises(Refusal) as far:
        service.schedule_send(ctx, user.id, ref, datetime.now(UTC) + timedelta(days=400))
    assert far.value.code == "invalid_argument"
    nobody = composer.save(subject="Nobody", text="Hello")["ref"]
    with pytest.raises(Refusal):
        service.schedule_send(ctx, user.id, nobody, datetime.now(UTC))

    held = service.schedule_send(ctx, user.id, ref, datetime.now(UTC) + timedelta(hours=1))
    assert service.list_scheduled(ctx, admin.id) == []
    with pytest.raises(Refusal) as other:
        service.cancel_scheduled(ctx, admin.id, held.scheduled_id)
    assert other.value.code == "not_found"
    with pytest.raises(Refusal) as nonsense:
        service.cancel_scheduled(ctx, user.id, "1e3")
    assert nonsense.value.code == "not_found"


def test_removing_the_account_forgets_its_held_sends_and_their_jobs(
    composer: Composer, user: User, db_session: Session
) -> None:
    ctx = context(composer)
    held = service.schedule_send(
        ctx, user.id, draft_to(composer, stranger()), datetime.now(UTC) + timedelta(hours=1)
    )
    stored = row(db_session, held.scheduled_id)
    assert stored is not None
    job_id = stored.job_id

    assert composer.client.delete("/api/v1/accounts/1").status_code == 204

    assert service.list_scheduled(ctx, user.id) == []
    assert job(db_session, job_id) is None
    assert db_session.scalars(select(ScheduledSend.id)).all() == []
