"""The Home page's summary (``mailroom.home``, ``GET /api/v1/home``): only the user's own mail and
filters, read from the index, snoozes, scheduled sends and runs; today in the user's time zone; the
parts of features the instance does not run left out."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from mailroom.accounts.store import load_accounts, user_store
from mailroom.auth.users import create_user
from mailroom.config.features import ALL_FEATURES, Features
from mailroom.home import home_summary
from mailroom.models import (
    Filter,
    FilterMatchDay,
    FilterRun,
    MailIndexFolder,
    RuleSet,
    ScheduledSend,
    Snooze,
)
from mailroom.reach import WHOLE
from mailroom_core.mail import TO_SORT
from tests.conftest import PASSWORD
from tests.test_api_accounts import added, new_account
from tests.test_mail_index_rows import message

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.context import AppContext
    from mailroom.models import User

pytestmark = pytest.mark.usefixtures("numbered_accounts")

NOW = datetime(2026, 11, 20, 22, 30, tzinfo=UTC)
HOME = "/api/v1/home"


def context(engine: Engine, features: Features = ALL_FEATURES) -> AppContext:
    return cast(
        "AppContext",
        SimpleNamespace(
            features=features,
            reach=WHOLE,
            store=lambda user_id: user_store(engine, user_id),
            accounts=lambda user_id: load_accounts(user_store(engine, user_id)),
        ),
    )


def folder(db: Session, user: User, account: int, path: str, **changes: Any) -> MailIndexFolder:
    row = MailIndexFolder(
        user_id=user.id,
        account_id=account,
        path=path,
        **({"uid_validity": 7, "state": "ready", "enabled": True} | changes),
    )
    db.add(row)
    db.flush()
    return row


def snooze(user: User, until: datetime, account: int = 1) -> Snooze:
    return Snooze(
        user_id=user.id,
        account_id=account,
        via="web",
        message_id=f"<{until.isoformat()}@example.org>",
        size=10,
        folder="INBOX",
        snoozed="Snoozed",
        until=until,
    )


def scheduled(user: User, send_at: datetime, state: str = "held") -> ScheduledSend:
    return ScheduledSend(
        user_id=user.id,
        account_id=1,
        ref=f"ref-{send_at.isoformat()}",
        send_key=f"key-{send_at.isoformat()}-{state}",
        via="web",
        send_at=send_at,
        state=state,
    )


def test_unread_per_inbox_and_the_marks_of_the_users_own_mail(
    user_client: TestClient, db_session: Session, user: User, migrated_engine: Engine
) -> None:
    work = added(user_client)
    gmail = added(user_client, new_account("gmail"))
    unindexed = added(user_client, new_account("home"))
    inbox = folder(db_session, user, work, "INBOX", unread_count=3)
    archive = folder(db_session, user, work, "Archive", unread_count=9)
    all_mail = folder(db_session, user, gmail, "[Gmail]/All Mail")
    folder(db_session, user, unindexed, "INBOX", state="syncing", unread_count=4)
    db_session.add_all(
        [
            message(inbox.id, user.id, 1, flags=["\\Flagged"]),
            message(inbox.id, user.id, 2, keywords=[TO_SORT]),
            message(archive.id, user.id, 1, flags=["\\Flagged", "\\Seen"]),
            message(all_mail.id, user.id, 1, gm_msgid=11, labels=["\\Inbox"]),
            message(all_mail.id, user.id, 2, gm_msgid=12, labels=["\\Inbox"], flags=["\\Seen"]),
            message(all_mail.id, user.id, 3, gm_msgid=13, labels=["Work"]),
        ]
    )
    # Another user's mail and marks are not counted.
    other = create_user(db_session, "other", PASSWORD)
    theirs = folder(db_session, other, work, "INBOX", unread_count=50)
    db_session.add(message(theirs.id, other.id, 1, flags=["\\Flagged"], keywords=[TO_SORT]))
    db_session.commit()

    mail = home_summary(context(migrated_engine), db_session, user.id, NOW).mail
    assert mail is not None
    assert [(inbox.account_id, inbox.display_name, inbox.unread) for inbox in mail.inboxes] == [
        (work, "work@example.org", 3),
        (gmail, "gmail@example.org", 1),
        (unindexed, "home@example.org", None),
    ]
    assert (mail.unread, mail.starred, mail.to_sort) == (4, 2, 1)

    answer = user_client.get(HOME)
    assert answer.status_code == 200
    assert answer.json()["mail"]["unread"] == 4


def test_an_inbox_indexed_under_an_age_limit_is_not_counted(
    user_client: TestClient, db_session: Session, user: User, migrated_engine: Engine
) -> None:
    # Its rows leave out the unread mail older than the limit: a count would be too low.
    work = added(user_client)
    gmail = added(user_client, new_account("gmail"))
    limited = date(2026, 10, 1)
    folder(db_session, user, work, "INBOX", unread_count=3, oldest_indexed=limited)
    all_mail = folder(db_session, user, gmail, "[Gmail]/All Mail", oldest_indexed=limited)
    db_session.add(message(all_mail.id, user.id, 1, gm_msgid=11, labels=["\\Inbox"]))
    db_session.commit()

    mail = home_summary(context(migrated_engine), db_session, user.id, NOW).mail
    assert mail is not None
    assert [inbox.unread for inbox in mail.inboxes] == [None, None]
    assert mail.unread == 0


@pytest.mark.parametrize(
    ("zone", "today"),
    [
        # 22:30 UTC is still the 20th in UTC, already the 21st in Rome.
        ("UTC", 2),
        ("Europe/Rome", 1),
    ],
)
def test_snoozed_today_ends_at_the_users_midnight(
    db_session: Session, user: User, migrated_engine: Engine, zone: str, today: int
) -> None:
    user.time_zone = zone
    db_session.add_all(
        [
            snooze(user, NOW - timedelta(minutes=5)),  # overdue, still waiting
            snooze(user, NOW + timedelta(hours=1)),  # 23:30 UTC, 00:30 in Rome
            snooze(user, NOW + timedelta(days=2)),
        ]
    )
    other = create_user(db_session, "other", PASSWORD)
    db_session.add(snooze(other, NOW + timedelta(minutes=1)))
    db_session.commit()

    summary = home_summary(context(migrated_engine), db_session, user.id, NOW)
    assert summary.mail is not None
    assert (summary.mail.snoozed, summary.mail.snoozed_today) == (3, today)


def test_scheduled_sends_count_the_next_and_the_failed(
    db_session: Session, user: User, migrated_engine: Engine
) -> None:
    soon = NOW + timedelta(hours=2)
    db_session.add_all(
        [
            scheduled(user, NOW + timedelta(days=1)),
            scheduled(user, soon, "sending"),
            scheduled(user, NOW - timedelta(days=1), "failed"),
            scheduled(create_user(db_session, "other", PASSWORD), NOW),
        ]
    )
    db_session.commit()

    mail = home_summary(context(migrated_engine), db_session, user.id, NOW).mail
    assert mail is not None
    assert (mail.scheduled, mail.next_scheduled_at, mail.scheduled_failed) == (2, soon, 1)


def run(rule_set: RuleSet, finished: datetime | None, **changes: Any) -> FilterRun:
    return FilterRun(
        rule_set_id=rule_set.id,
        account_id=1,
        trigger="manual",
        **({"dry_run": False, "started_at": finished, "finished_at": finished} | changes),
    )


def test_filters_at_a_glance_counts_this_weeks_real_runs(
    db_session: Session, user: User, migrated_engine: Engine
) -> None:
    def rule_set(owner: User, name: str, **changes: Any) -> RuleSet:
        row = RuleSet(
            user_id=owner.id,
            account_id=1,
            folder=["INBOX"],
            name=name,
            trigger="manual",
            **changes,
        )
        db_session.add(row)
        db_session.flush()
        return row

    news = rule_set(user, "News", enabled=True)
    bills = rule_set(user, "Bills", disabled_reason="auth_failed: The login was refused.")
    rule_set(user, "Muted", kind="mute")
    theirs = rule_set(create_user(db_session, "other", PASSWORD), "Theirs")
    newsletters = Filter(rule_set_id=news.id, position=0, name="Newsletters", spec={})
    db_session.add(newsletters)
    db_session.flush()
    today = NOW.date()
    db_session.add_all(
        [
            run(news, NOW - timedelta(days=1)),
            run(news, NOW - timedelta(hours=1), error="auth_failed: The login was refused."),
            run(news, NOW - timedelta(days=10)),
            run(news, NOW, dry_run=True),
            run(news, None),
            run(bills, NOW - timedelta(days=20)),
            run(theirs, NOW),
            FilterMatchDay(filter_id=newsletters.id, day=today, count=4),
            FilterMatchDay(filter_id=newsletters.id, day=today - timedelta(days=6), count=2),
            FilterMatchDay(filter_id=newsletters.id, day=today - timedelta(days=7), count=100),
        ]
    )
    db_session.commit()

    filters = home_summary(context(migrated_engine), db_session, user.id, NOW).filters
    assert filters is not None
    glance = {
        each.name: (each.runs, each.failed_runs, each.last_run_failed, each.acted_on)
        for each in filters.rule_sets
    }
    assert glance == {"Bills": (0, 0, False, 0), "News": (2, 1, True, 6)}
    assert [each.name for each in filters.rule_sets] == ["Bills", "News"]
    assert filters.rule_sets[0].disabled_reason is not None
    assert filters.rule_sets[0].last_run_at == NOW - timedelta(days=20)


def test_a_feature_the_instance_does_not_run_is_left_out(
    db_session: Session, user: User, migrated_engine: Engine
) -> None:
    mail_only = Features(mail=True, mcp=False, filters=False)
    summary = home_summary(context(migrated_engine, mail_only), db_session, user.id, NOW)
    assert summary.mail is not None
    assert summary.filters is None
    filters = Features(mail=False, mcp=False, filters=True)
    summary = home_summary(context(migrated_engine, filters), db_session, user.id, NOW)
    assert summary.mail is None
    assert summary.filters is not None
    assert summary.filters.rule_sets == []
