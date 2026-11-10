from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, update

from mailroom import audit
from mailroom.audit import WEB, AuditTrail, Entry, Origin, retention, write_records
from mailroom.models import AuditRecord
from mailroom_core.mail.audit import AuditItem, AuditRecipients

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.models import User


def records(db_session: Session, user_id: int) -> list[AuditRecord]:
    query = select(AuditRecord).where(AuditRecord.user_id == user_id).order_by(AuditRecord.id)
    return list(db_session.scalars(query))


ITEM = AuditItem(
    account="work",
    folder="INBOX",
    uid=7,
    message_id="<m1@example.org>",
    sender="ana@example.org",
    recipients=AuditRecipients(to=("bo@example.org",)),
    subject_sha256="ab" * 32,
    destination="Trash",
    result="ok",
)


def test_an_item_is_kept_with_who_acted(
    db_session: Session, sessions: sessionmaker[Session], user: User
) -> None:
    origin = Origin("mcp", "client-1", "42")
    AuditTrail(sessions, user.id, origin, "trash").note(ITEM)

    [record] = records(db_session, user.id)
    assert (record.actor, record.client_id, record.grant_id) == ("agent", "client-1", "42")
    assert (record.action, record.account_id, record.result) == ("trash", "work", "ok")
    assert record.detail == {
        "folder": "INBOX",
        "uid": 7,
        "message_id": "<m1@example.org>",
        "from": "ana@example.org",
        "to": ["bo@example.org"],
        "cc": [],
        "bcc": [],
        "subject_sha256": "ab" * 32,
        "destination": "Trash",
    }


def test_the_web_acts_as_the_user(
    db_session: Session, sessions: sessionmaker[Session], user: User
) -> None:
    AuditTrail(sessions, user.id, WEB, "move").notes([ITEM, ITEM])
    assert [(r.actor, r.client_id) for r in records(db_session, user.id)] == [("user", None)] * 2


def test_a_failed_write_is_logged_and_never_raised(
    sessions: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    missing_user = 2**62
    write_records(sessions, missing_user, [_entry()])
    [logged] = [r for r in caplog.records if r.getMessage() == "audit_write_failed"]
    assert (getattr(logged, "records", None), getattr(logged, "error", None)) == (
        1,
        "IntegrityError",
    )


def test_records_older_than_the_retention_are_purged_on_a_later_write(
    db_session: Session,
    sessions: sessionmaker[Session],
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(audit.RETENTION_DAYS_ENV, "30")
    write_records(sessions, user.id, [_entry(), _entry()])
    old = records(db_session, user.id)[0].id
    db_session.execute(
        update(AuditRecord)
        .where(AuditRecord.id == old)
        .values(at=AuditRecord.at - timedelta(days=31))
    )
    audit.PURGE.reset()
    write_records(sessions, user.id, [_entry()])
    assert old not in {record.id for record in records(db_session, user.id)}
    assert len(records(db_session, user.id)) == 2


@pytest.mark.parametrize(
    ("raw", "days"), [(None, 365), ("", 365), ("x", 365), ("0", 1), ("90", 90), ("99999", 3650)]
)
def test_the_retention_is_read_and_clamped(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, days: int
) -> None:
    if raw is None:
        monkeypatch.delenv(audit.RETENTION_DAYS_ENV, raising=False)
    else:
        monkeypatch.setenv(audit.RETENTION_DAYS_ENV, raw)
    assert retention() == timedelta(days=days)


def test_records_go_with_their_user(
    db_session: Session, sessions: sessionmaker[Session], user: User
) -> None:
    write_records(sessions, user.id, [_entry()])
    db_session.delete(user)
    db_session.flush()
    assert db_session.scalar(select(AuditRecord.id).where(AuditRecord.user_id == user.id)) is None


def _entry() -> Entry:
    return Entry(actor="user", action="move", account_id="work", result="ok", detail={})
