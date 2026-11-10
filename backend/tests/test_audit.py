from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, update

from mailroom import audit
from mailroom.audit import WEB, AuditTrail, Entry, Origin, retention, write_records
from mailroom.models import AuditRecord
from mailroom.worker.runs import run_records
from mailroom_core.mail.audit import AuditItem, AuditRecipients, subject_sha256

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


def _report(**filter_entry: object) -> dict[str, object]:
    entry = {"filter_id": 3, "action": "move to Archive", "executed": True, **filter_entry}
    return {"account": "acc", "folder": ["Lists", "News"], "dry_run": False, "filters": [entry]}


def _matched(count: int) -> list[dict[str, object]]:
    return [
        {"uid": uid, "from": "Shop <shop@example.org>", "subject": f"Sale {uid}", "date": "x"}
        for uid in range(count)
    ]


def test_a_run_records_each_message_its_filters_acted_on() -> None:
    entries = run_records(
        _report(matched=_matched(2)),
        {3: "Shop"},
        account_id="work",
        rule_set_id=5,
        run_id=9,
        error=None,
    )
    assert [(e.actor, e.action, e.account_id, e.result) for e in entries] == [
        ("filter", "move to Archive", "work", "ok")
    ] * 2
    assert (entries[0].rule_set_id, entries[0].run_id) == (5, 9)
    assert entries[1].detail == {
        "folder": "Lists/News",
        "filter_id": 3,
        "filter": "Shop",
        "uid": 1,
        "from": "shop@example.org",
        "subject_sha256": subject_sha256("Sale 1"),
        "date": "x",
    }


def test_a_run_counts_what_its_report_did_not_list() -> None:
    capped = run_records(
        _report(matched=_matched(2), matched_total=5),
        {},
        account_id="work",
        rule_set_id=5,
        run_id=9,
        error=None,
    )
    assert capped[-1].detail["not_listed"] == 3


def test_a_run_that_failed_partway_records_the_filter_it_failed_on() -> None:
    # imap-thingy reports the filters that finished; the one that raised is not in the report.
    entries = run_records(
        _report(matched=_matched(1)),
        {3: "Shop", 4: "Bank", 5: "Later"},
        account_id="work",
        rule_set_id=5,
        run_id=9,
        error="mail_failed: timeout",
    )
    failed = entries[-1]
    assert (failed.action, failed.result) == ("run_failed", "failed")
    assert failed.detail == {
        "folder": "Lists/News",
        "filter_id": 4,
        "filter": "Bank",
        "error": "mail_failed: timeout",
    }
    assert [e.result for e in entries[:-1]] == ["ok"]


def test_a_message_without_a_subject_keeps_no_hash() -> None:
    [entry] = run_records(
        _report(matched=[{"uid": 1, "from": None, "subject": None, "date": "x"}]),
        {},
        account_id="work",
        rule_set_id=5,
        run_id=9,
        error=None,
    )
    assert entry.detail == {
        "folder": "Lists/News",
        "filter_id": 3,
        "filter": None,
        "uid": 1,
        "date": "x",
    }


def test_a_dry_run_or_a_filter_that_matched_nothing_records_nothing() -> None:
    dry = {**_report(matched=_matched(1)), "dry_run": True}
    assert run_records(dry, {}, account_id="w", rule_set_id=1, run_id=1, error=None) == []
    idle = _report(matched=[])
    assert run_records(idle, {}, account_id="w", rule_set_id=1, run_id=1, error=None) == []
