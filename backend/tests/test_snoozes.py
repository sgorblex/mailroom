"""Snooze against real Dovecot (skipped without it): the message moved to Snoozed (made when
missing) with its row and wake job, and the worker's ``wake_snooze`` bringing it back unread; a
message gone from Snoozed recorded and dropped. The job's body runs here directly, as
procrastinate would run it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select, text, update

from mailroom.context import app_context
from mailroom.errors import Refusal
from mailroom.mail import service
from mailroom.models import Snooze
from mailroom.worker import snoozes as worker_snoozes
from mailroom.worker.app import PROCRASTINATE_SCHEMA
from mailroom.worker.snoozes import GRACE, RETRY, wake_snooze
from mailroom.worker.tasks import WAKE_SNOOZE, requeue_lost_snoozes
from mailroom_core import mail as core_mail
from mailroom_core.mail import SNOOZED_FOLDER, MailError
from mailroom_core.testing import create_folders, mime, raw_client
from tests.support.mail import mail_servers_ssl_context
from tests.test_api_compose import MAIL, Composer, add_account, detail

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


@pytest.fixture
def mine(
    user_client: TestClient,
    imap_server: ImapTestServer,
    application: FastAPI,
    audited: Callable[[int], list[AuditRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> Composer:
    """The signed-in user's account ``work``, with Work and Trash; no Snoozed yet."""
    _ = audited
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_servers_ssl_context())
    login = imap_server.unique_user("snooze")
    create_folders(imap_server, login, "Drafts", "Sent", "Trash", "Work")
    composer = Composer(user_client, imap_server, login, application)
    add_account(composer)
    return composer


def context(composer: Composer) -> AppContext:
    ctx = app_context(composer.application.state)
    assert ctx is not None
    return ctx


def job(db: Session, job_id: int | None) -> Any:
    return db.execute(
        text(
            "SELECT status, task_name, scheduled_at, lock, args"
            f" FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs WHERE id = :id"
        ),
        {"id": job_id},
    ).one_or_none()


def row(db: Session, snooze_id: str) -> Snooze | None:
    db.expire_all()
    return db.get(Snooze, int(snooze_id))


def later(hours: int = 1) -> datetime:
    return (datetime.now(UTC) + timedelta(hours=hours)).replace(microsecond=0)


def test_a_snoozed_message_waits_in_snoozed_and_comes_back_unread(
    mine: Composer,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    audited: Callable[[int], list[AuditRecord]],
) -> None:
    ref = mine.seed(mime("Later please", message_id="<s1@example.org>"), "Work", ("\\Seen",))
    until = later()

    snoozed = service.snooze(context(mine), user.id, ref, until)

    assert (snoozed.subject, snoozed.sender, snoozed.folder) == (
        "Later please",
        "maria@example.org",
        "Work",
    )
    assert (snoozed.account, snoozed.until, snoozed.code) == (1, until, None)
    assert mine.contents("Work") == []
    [waiting] = mine.contents(SNOOZED_FOLDER)
    assert waiting.subject == "Later please"
    stored = row(db_session, snoozed.snooze_id)
    assert stored is not None
    assert (stored.message_id, stored.snoozed) == ("<s1@example.org>", SNOOZED_FOLDER)
    assert stored.ref is not None
    queued = job(db_session, stored.job_id)
    assert (queued.status, queued.task_name, queued.scheduled_at) == ("todo", WAKE_SNOOZE, until)
    assert queued.lock is None
    assert queued.args == {"snooze_id": int(snoozed.snooze_id)}
    assert service.list_snoozes(context(mine), user.id) == [snoozed]
    with pytest.raises(Refusal) as again:
        service.snooze(context(mine), user.id, stored.ref, later())
    assert again.value.code == "invalid_argument"

    assert wake_snooze(sessions, mailboxes, int(snoozed.snooze_id)) == "ok"

    [back] = mine.contents("Work")
    assert back.subject == "Later please"
    assert "\\Seen" not in back.flags
    assert mine.contents(SNOOZED_FOLDER) == []
    assert row(db_session, snoozed.snooze_id) is None
    actions = [(record.action, record.result) for record in audited(user.id)]
    assert ("folder_create", "ok") in actions
    assert ("snooze", "ok") in actions
    assert actions.count(("wake", "ok")) == 2  # marked unread, then moved
    # The job run again, as procrastinate could: nothing is left to do.
    assert wake_snooze(sessions, mailboxes, int(snoozed.snooze_id)) == "gone"


def test_a_message_gone_from_snoozed_is_recorded_and_dropped(
    mine: Composer,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    audited: Callable[[int], list[AuditRecord]],
) -> None:
    create_folders(mine.server, mine.login, SNOOZED_FOLDER)
    ref = mine.seed(mime("Elsewhere", message_id="<s2@example.org>"))
    snoozed = service.snooze(context(mine), user.id, ref, later())
    # Another client takes it out of Snoozed meanwhile.
    with raw_client(mine.server, mine.login) as raw:
        raw.select_folder(SNOOZED_FOLDER)
        raw.move(raw.search("ALL"), "Trash")

    # Just snoozed, its move could still be on its way: tried again later.
    assert wake_snooze(sessions, mailboxes, int(snoozed.snooze_id)) == "not_found"
    stored = row(db_session, snoozed.snooze_id)
    assert stored is not None
    assert stored.code == "not_found"
    assert job(db_session, stored.job_id).status == "todo"
    db_session.execute(
        update(Snooze)
        .where(Snooze.id == stored.id)
        .values(created_at=datetime.now(UTC) - timedelta(hours=1))
    )
    db_session.commit()

    assert wake_snooze(sessions, mailboxes, int(snoozed.snooze_id)) == "missing"

    assert row(db_session, snoozed.snooze_id) is None
    assert len(mine.contents("Trash")) == 1
    assert mine.contents("INBOX") == []
    [record] = [record for record in audited(user.id) if record.action == "wake"]
    assert record.result == "not_found"
    assert record.detail["folder"] == SNOOZED_FOLDER


def test_snoozing_is_checked_and_another_users_snooze_is_not_found(
    mine: Composer, user: User, admin: User, db_session: Session
) -> None:
    ref = mine.seed(mime("Checked"))
    ctx = context(mine)
    for until in (
        datetime.now(),  # noqa: DTZ005
        datetime.now(UTC) - timedelta(minutes=1),
        datetime.now(UTC) + timedelta(days=400),
    ):
        with pytest.raises(Refusal) as refused:
            service.snooze(ctx, user.id, ref, until)
        assert refused.value.code == "invalid_argument"
    sent = mine.seed(mime("Mine"), "Sent")
    with pytest.raises(Refusal) as from_sent:
        service.snooze(ctx, user.id, sent, later())
    assert from_sent.value.code == "invalid_argument"
    assert mine.contents(SNOOZED_FOLDER) == []
    assert db_session.scalars(select(Snooze.id)).all() == []

    snoozed = service.snooze(ctx, user.id, ref, later())
    assert service.list_snoozes(ctx, admin.id) == []
    for other, snooze_id in ((admin.id, snoozed.snooze_id), (user.id, "1e3")):
        with pytest.raises(Refusal) as missing:
            service.wake_now(ctx, other, snooze_id)
        assert missing.value.code == "not_found"


def test_snooze_routes_snooze_list_and_wake_now(
    mine: Composer,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
) -> None:
    ref = mine.seed(mime("By route"))
    until = later(24)

    created = mine.client.post(f"{MAIL}/messages/{ref}/snooze", json={"until": until.isoformat()})

    assert created.status_code == 201, created.text
    snoozed = created.json()
    assert (snoozed["subject"], snoozed["folder"]) == ("By route", "INBOX")
    assert datetime.fromisoformat(snoozed["until"]) == until
    assert mine.client.get(f"{MAIL}/snoozes").json() == {"snoozes": [snoozed]}
    naive = mine.client.post(f"{MAIL}/messages/{ref}/snooze", json={"until": "2030-01-01T09:00"})
    assert naive.status_code == 422

    stored = row(db_session, snoozed["snooze_id"])
    assert stored is not None
    first_job = stored.job_id
    woken = mine.client.post(f"{MAIL}/snoozes/{snoozed['snooze_id']}/wake")

    assert woken.status_code == 200, woken.text
    stored = row(db_session, snoozed["snooze_id"])
    assert stored is not None
    assert job(db_session, first_job) is None
    assert job(db_session, stored.job_id).status == "todo"
    assert datetime.fromisoformat(woken.json()["until"]) <= datetime.now(UTC)
    assert wake_snooze(sessions, mailboxes, stored.id) == "ok"
    assert [message.subject for message in mine.contents("INBOX")] == ["By route"]
    assert mine.client.get(f"{MAIL}/snoozes").json() == {"snoozes": []}
    gone = mine.client.post(f"{MAIL}/snoozes/{snoozed['snooze_id']}/wake")
    assert gone.status_code == 404


def waiting_wakes(db: Session) -> list[Any]:
    return list(
        db.execute(
            text(
                f"SELECT scheduled_at FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs"
                " WHERE task_name = :name AND status = 'todo'"
            ),
            {"name": WAKE_SNOOZE},
        ).scalars()
    )


def test_a_message_snoozed_twice_is_already_snoozed(mine: Composer) -> None:
    work = mine.seed(mime("Twice", message_id="<twice@example.org>"), "Work")
    inbox = mine.seed(mime("Twice", message_id="<twice@example.org>"))
    until = {"until": later().isoformat()}
    assert mine.client.post(f"{MAIL}/messages/{inbox}/snooze", json=until).status_code == 201

    again = mine.client.post(f"{MAIL}/messages/{work}/snooze", json=until)

    assert detail(again) == (409, "already_snoozed")
    assert len(mine.contents("Work")) == 1


@pytest.mark.parametrize(
    ("code", "kept"), [("server_error", False), ("timeout", True), ("unreachable", True)]
)
def test_a_failed_move_drops_the_snooze_unless_it_may_have_happened(
    mine: Composer,
    user: User,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    kept: bool,
) -> None:
    ref = mine.seed(mime("Unmoved"), "Work")

    def broken(*_args: object) -> None:
        raise MailError(code)  # type: ignore[arg-type]

    monkeypatch.setattr(core_mail, "move_messages", broken)

    with pytest.raises(Refusal) as refused:
        service.snooze(context(mine), user.id, ref, later(24))

    assert refused.value.code == code
    assert len(mine.contents("Work")) == 1
    stored = db_session.scalars(select(Snooze)).all()
    if not kept:
        assert stored == []
        assert waiting_wakes(db_session) == []
        return
    [snooze] = stored
    assert snooze.code == code
    [wake] = waiting_wakes(db_session)
    assert datetime.now(UTC) < wake <= datetime.now(UTC) + GRACE
    assert wake == snooze.until


def test_a_wake_whose_account_cannot_log_in_keeps_its_snooze_and_tries_later(
    mine: Composer,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snoozed = service.snooze(context(mine), user.id, mine.seed(mime("Kept back")), later())
    monkeypatch.setattr(
        worker_snoozes, "unusable", lambda *_args: ("password_missing", "No password.")
    )

    assert wake_snooze(sessions, mailboxes, int(snoozed.snooze_id)) == "password_missing"

    stored = row(db_session, snoozed.snooze_id)
    assert stored is not None
    assert stored.code == "password_missing"
    queued = job(db_session, stored.job_id)
    assert queued.status == "todo"
    soon = datetime.now(UTC) + RETRY
    assert soon - timedelta(minutes=1) < queued.scheduled_at <= soon
    assert [message.subject for message in mine.contents(SNOOZED_FOLDER)] == ["Kept back"]


def test_renaming_snoozed_and_the_folder_it_came_from_keeps_the_snooze(
    mine: Composer,
    user: User,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
) -> None:
    ctx = context(mine)
    snoozed = service.snooze(ctx, user.id, mine.seed(mime("Renamed"), "Work"), later())

    service.rename_folder(ctx, user.id, 1, SNOOZED_FOLDER, None, "Later")
    service.rename_folder(ctx, user.id, 1, "Work", None, "Projects")

    stored = row(db_session, snoozed.snooze_id)
    assert stored is not None
    assert (stored.snoozed, stored.folder, stored.ref) == ("Later", "Projects", None)
    assert wake_snooze(sessions, mailboxes, int(snoozed.snooze_id)) == "ok"
    assert [message.subject for message in mine.contents("Projects")] == ["Renamed"]
    assert mine.contents("Later") == []


def test_lost_wake_jobs_are_queued_again_and_the_account_takes_its_snoozes(
    mine: Composer, user: User, db_session: Session
) -> None:
    snoozed = service.snooze(context(mine), user.id, mine.seed(mime("Kept")), later())
    stored = row(db_session, snoozed.snooze_id)
    assert stored is not None
    lost = stored.job_id
    path = db_session.execute(text("SELECT current_setting('search_path')")).scalar_one()
    # Its triggers name procrastinate's tables unqualified.
    db_session.execute(
        text("SELECT set_config('search_path', :path, true)"),
        {"path": f"{PROCRASTINATE_SCHEMA}, {path}"},
    )
    db_session.execute(
        text("UPDATE procrastinate_jobs SET status = 'failed' WHERE id = :id"), {"id": lost}
    )
    db_session.execute(text("SELECT set_config('search_path', :path, true)"), {"path": path})
    db_session.commit()

    assert requeue_lost_snoozes(db_session) == [stored.id]
    stored = row(db_session, snoozed.snooze_id)
    assert stored is not None
    assert stored.job_id != lost
    assert job(db_session, stored.job_id).status == "todo"
    assert requeue_lost_snoozes(db_session) == []

    assert mine.client.delete("/api/v1/accounts/1").status_code == 204

    assert db_session.scalars(select(Snooze.id)).all() == []
    assert job(db_session, stored.job_id) is None
