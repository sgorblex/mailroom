"""The Home page's summary of the user's own mail and filters (``GET /api/v1/home``), read from
the app's tables alone: the mail index's counts and flags, the snoozes and scheduled sends, and
the filter runs and match counts. Nothing asks a mail server, so it answers at once; a part of it
the instance does not run (mail, filters) is left out.

Specification: ``docs/requirements/mail-client.md`` (*Home*).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import func, not_, select
from sqlalchemy.dialects.postgresql import distinct_on

from mailroom.auth.users import inbox_subfolders_of, time_zone_of
from mailroom.mail.index.reads import inbox_subfolders_unread, inbox_unread, marked
from mailroom.models import (
    Filter,
    FilterMatchDay,
    FilterRun,
    RuleSet,
    ScheduledSend,
    Snooze,
)
from mailroom_core.config import AccountColour

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom.context import AppContext

WEEK_DAYS: Final = 7
"""How far back *Filters at a glance* looks: the last 7 days, today included."""


class Inbox(BaseModel):
    account_id: int
    display_name: str
    """What to call the account: its name, else its login."""
    colour: AccountColour | None
    unread: int | None
    """Unread messages in its INBOX as the index holds them (on Gmail, All Mail's with INBOX's
    label); None while the index has no readable copy of it, or holds it only under an age
    limit (``mailroom.mail.index.reads.inbox_unread``)."""


class MailSummary(BaseModel):
    inboxes: list[Inbox]
    """Each of the user's accounts, in their order."""
    unread: int
    """All inboxes: the sum of the counts known, with those of INBOX's subfolders when the user's
    takes them in."""
    starred: int
    to_sort: int
    """Messages starred, and marked To sort, in the indexed folders of every account (under an
    age limit, those since its day)."""
    snoozed: int
    snoozed_today: int
    """Snoozed messages, and those of them coming back before the user's midnight."""
    scheduled: int
    """Sends held for later, not failed."""
    next_scheduled_at: datetime | None
    scheduled_failed: int


class RuleSetGlance(BaseModel):
    id: int
    name: str
    account_id: int | None
    """None: a rule set for INBOX of every account."""
    enabled: bool
    disabled_reason: str | None
    """Why the app turned it off, when it did."""
    runs: int
    failed_runs: int
    """Its recent real runs, and those that failed: those that finished in the last ``WEEK_DAYS``
    days among the runs kept (``mailroom.filters.runs.RUNS_KEPT`` per account, so a rule set
    run often shows fewer than the week's)."""
    last_run_at: datetime | None
    last_run_failed: bool
    """Its newest finished real run (whenever it was), and whether it failed."""
    acted_on: int
    """Messages its filters acted on in real runs in the last ``WEEK_DAYS`` days (UTC days, as
    the match counts are kept)."""


class FiltersSummary(BaseModel):
    rule_sets: list[RuleSetGlance]
    """The user's rule sets, by name; never the hidden muting ones."""


class HomeSummary(BaseModel):
    mail: MailSummary | None
    """None where the instance does not run the mail client."""
    filters: FiltersSummary | None
    """None where the instance does not run filters."""


def _next_midnight(now: datetime, zone: str) -> datetime:
    """The start of the day after ``now``'s in ``zone``."""
    local = now.astimezone(ZoneInfo(zone))
    return datetime.combine(local.date() + timedelta(days=1), time(), ZoneInfo(zone))


def mail_summary(
    ctx: AppContext, db: Session, user_id: int, zone: str, now: datetime
) -> MailSummary:
    accounts = ctx.accounts(user_id).settings.account
    ids = [account.id for account in accounts]
    unread = inbox_unread(db, user_id, ids)
    total = sum(unread.values())
    if inbox_subfolders_of(db, user_id):
        total += inbox_subfolders_unread(db, user_id, ids)
    starred, to_sort = marked(db, user_id, ids)
    tomorrow = _next_midnight(now, zone)
    snoozed, snoozed_today = db.execute(
        select(func.count(), func.count().filter(Snooze.until < tomorrow)).where(
            Snooze.user_id == user_id, ctx.reach.covers(Snooze.account_id)
        )
    ).one()
    waiting = ScheduledSend.state != "failed"
    scheduled, next_at, failed = db.execute(
        select(
            func.count().filter(waiting),
            func.min(ScheduledSend.send_at).filter(waiting),
            func.count().filter(not_(waiting)),
        ).where(ScheduledSend.user_id == user_id, ctx.reach.covers(ScheduledSend.account_id))
    ).one()
    return MailSummary(
        inboxes=[
            Inbox(
                account_id=account.id,
                display_name=account.name or account.login,
                colour=account.colour,
                unread=unread.get(account.id),
            )
            for account in accounts
        ],
        unread=total,
        starred=starred,
        to_sort=to_sort,
        snoozed=snoozed,
        snoozed_today=snoozed_today,
        scheduled=scheduled,
        next_scheduled_at=next_at,
        scheduled_failed=failed,
    )


def filters_summary(ctx: AppContext, db: Session, user_id: int, now: datetime) -> FiltersSummary:
    rule_sets = list(
        db.scalars(
            select(RuleSet)
            .where(
                RuleSet.user_id == user_id,
                RuleSet.kind == "user",
                ctx.reach.covers(RuleSet.account_id),
            )
            .order_by(RuleSet.name, RuleSet.id)
        )
    )
    ids = [rule_set.id for rule_set in rule_sets]
    since = now - timedelta(days=WEEK_DAYS)
    real = (
        FilterRun.rule_set_id.in_(ids),
        FilterRun.dry_run.is_(False),
        FilterRun.finished_at.is_not(None),
    )
    week: dict[int, tuple[int, int]] = {
        rule_set: (runs, failed)
        for rule_set, runs, failed in db.execute(
            select(
                FilterRun.rule_set_id,
                func.count(),
                func.count().filter(FilterRun.error.is_not(None)),
            )
            .where(*real, FilterRun.finished_at >= since)
            .group_by(FilterRun.rule_set_id)
        )
    }
    newest = (
        select(FilterRun.rule_set_id, FilterRun.finished_at, FilterRun.error)
        .where(*real)
        .ext(distinct_on(FilterRun.rule_set_id))
        .order_by(FilterRun.rule_set_id, FilterRun.finished_at.desc(), FilterRun.id.desc())
    )
    last: dict[int, tuple[datetime | None, bool]] = {
        rule_set: (finished, error is not None) for rule_set, finished, error in db.execute(newest)
    }
    first_day: date = now.astimezone(UTC).date() - timedelta(days=WEEK_DAYS - 1)
    acted: dict[int, int] = {
        rule_set: int(total)
        for rule_set, total in db.execute(
            select(Filter.rule_set_id, func.sum(FilterMatchDay.count))
            .join(FilterMatchDay, FilterMatchDay.filter_id == Filter.id)
            .where(Filter.rule_set_id.in_(ids), FilterMatchDay.day >= first_day)
            .group_by(Filter.rule_set_id)
        )
    }
    return FiltersSummary(
        rule_sets=[
            RuleSetGlance(
                id=rule_set.id,
                name=rule_set.name,
                account_id=rule_set.account_id,
                enabled=rule_set.enabled,
                disabled_reason=rule_set.disabled_reason,
                runs=week.get(rule_set.id, (0, 0))[0],
                failed_runs=week.get(rule_set.id, (0, 0))[1],
                last_run_at=last.get(rule_set.id, (None, False))[0],
                last_run_failed=last.get(rule_set.id, (None, False))[1],
                acted_on=acted.get(rule_set.id, 0),
            )
            for rule_set in rule_sets
        ]
    )


def home_summary(
    ctx: AppContext, db: Session, user_id: int, now: datetime | None = None
) -> HomeSummary:
    """The user's summary as of ``now`` (the current time when None)."""
    now = datetime.now(UTC) if now is None else now
    zone = time_zone_of(db, user_id)
    return HomeSummary(
        mail=mail_summary(ctx, db, user_id, zone, now) if ctx.features.mail else None,
        filters=filters_summary(ctx, db, user_id, now) if ctx.features.filters else None,
    )
