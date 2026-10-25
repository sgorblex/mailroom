"""The worker's tasks, and queueing runs of rule sets for them.

A run is queued as an ``app.filter_run`` row with no ``started_at`` plus the job
``run_rule_set(run_id)``, in one transaction: by Run now (the API), by the scheduled dispatcher
below, and by the IDLE supervisor (``mailroom.worker.idle``) when a folder gets new mail.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from procrastinate import SyncPsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued
from procrastinate.manager import JobManager
from procrastinate.tasks import configure_task
from sqlalchemy import and_, func, or_, select, text

from mailroom.filters.runs import prune_runs
from mailroom.models import AccountFilterSettings, FilterRun, RuleSet
from mailroom.worker.app import PROCRASTINATE_SCHEMA, app
from mailroom.worker.context import worker_context
from mailroom.worker.runs import execute_run

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Select
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

RUN_RULE_SET: Final = "run_rule_set"
"""The job running one queued ``app.filter_run`` (its ``run_id``) of a rule set."""
DISPATCH_SCHEDULED: Final = "dispatch_scheduled"
"""The periodic job queueing the scheduled rule sets that are due."""
_IN_TRANSACTION: Final = JobManager(SyncPsycopgConnector())
"""Defers jobs on a connection the caller passes, never its own (it is never opened): the app's
connector, once the worker opens it asynchronously, would take that connection for an async one."""
SCHEDULE_SLACK: Final = timedelta(seconds=30)
"""A scheduled rule set is due this much early: its last run was queued a moment after the
dispatcher's minute began, and would otherwise wait a whole extra minute."""


@app.task(name="ping")
def ping() -> None:
    """Does nothing: proves a job goes from the queue through a worker."""
    logger.info("pong")


@app.task(name=RUN_RULE_SET)
def run_rule_set(run_id: int) -> None:
    """Run the queued run ``run_id``: start it, run its rule set's enabled filters, and record the
    report or the error (``mailroom.worker.runs.execute_run``)."""
    context = worker_context()
    with context.sessions() as db:
        execute_run(db, run_id, context.mailboxes)


@app.periodic(cron="* * * * *")
@app.task(name=DISPATCH_SCHEDULED, queueing_lock=DISPATCH_SCHEDULED)
def dispatch_scheduled(timestamp: int) -> None:
    """Every minute: queue a run of each scheduled rule set that is due."""
    with worker_context().sessions() as db:
        queue_scheduled_runs(db, datetime.fromtimestamp(timestamp, UTC))
        db.commit()


def mailbox_lock(user_id: int, account_id: str) -> str:
    """procrastinate ``lock`` of every job acting on one mailbox: they run one at a time."""
    return f"mailbox:{user_id}:{account_id}"


def rule_set_queueing_lock(rule_set_id: int) -> str:
    """procrastinate ``queueing_lock``: one waiting run per rule set at most."""
    return f"rule_set:{rule_set_id}"


def defer_rule_set_run(
    db: Session, *, run_id: int, rule_set_id: int, user_id: int, account_id: str
) -> int:
    """Queue ``run_rule_set`` for ``run_id`` inside ``db``'s transaction, so the job exists exactly
    when the run row does. Returns the job id. Raises procrastinate's ``AlreadyEnqueued`` when a
    run of the rule set is already waiting (the transaction is then unusable: run this inside a
    savepoint)."""
    connection = db.connection()
    search_path = connection.execute(text("SELECT current_setting('search_path')")).scalar_one()
    # procrastinate's SQL names its tables and functions unqualified.
    connection.execute(
        text("SELECT set_config('search_path', :path, true)"),
        {"path": f"{PROCRASTINATE_SCHEMA}, {search_path}"},
    )
    job_id = configure_task(
        name=RUN_RULE_SET,
        job_manager=_IN_TRANSACTION,
        lock=mailbox_lock(user_id, account_id),
        queueing_lock=rule_set_queueing_lock(rule_set_id),
        connection=connection.connection.driver_connection,
    ).defer(run_id=run_id)
    connection.execute(text("SELECT set_config('search_path', :path, true)"), {"path": search_path})
    return job_id


def queue_run(db: Session, rule_set: RuleSet, trigger: str) -> int | None:
    """Queue a run of ``rule_set`` in ``db``'s transaction (the caller commits) and prune its
    history. Returns the run's id, or None when a run of it is already waiting."""
    run = FilterRun(rule_set_id=rule_set.id, trigger=trigger, dry_run=False)
    try:
        with db.begin_nested():
            db.add(run)
            db.flush()
            defer_rule_set_run(
                db,
                run_id=run.id,
                rule_set_id=rule_set.id,
                user_id=rule_set.user_id,
                account_id=rule_set.account_id,
            )
    except AlreadyEnqueued:
        return None
    prune_runs(db, rule_set.id)
    logger.info(
        "run_queued",
        extra={
            "event": "run_queued",
            "user_id": rule_set.user_id,
            "rule_set_id": rule_set.id,
            "run_id": run.id,
            "trigger": trigger,
        },
    )
    return run.id


def runnable_rule_sets(trigger: str) -> Select[RuleSet]:
    """The enabled rule sets with ``trigger`` whose account's Run filters is on."""
    return (
        select(RuleSet)
        .join(
            AccountFilterSettings,
            and_(
                AccountFilterSettings.user_id == RuleSet.user_id,
                AccountFilterSettings.account_id == RuleSet.account_id,
            ),
        )
        .where(RuleSet.enabled, RuleSet.trigger == trigger, AccountFilterSettings.run_filters)
    )


def due_rule_sets(db: Session, now: datetime) -> Sequence[RuleSet]:
    """The runnable scheduled rule sets never run on schedule, or last queued on schedule at least
    their interval (less ``SCHEDULE_SLACK``) before ``now``."""
    last = (
        select(func.max(FilterRun.created_at))
        .where(FilterRun.rule_set_id == RuleSet.id, FilterRun.trigger == "scheduled")
        .scalar_subquery()
    )
    every = func.make_interval(0, 0, 0, 0, 0, RuleSet.interval_minutes)
    statement = (
        runnable_rule_sets("scheduled")
        .where(or_(last.is_(None), last + every <= now + SCHEDULE_SLACK))
        .order_by(RuleSet.id)
    )
    return db.scalars(statement).all()


def queue_scheduled_runs(db: Session, now: datetime) -> list[int]:
    """Queue a run of each scheduled rule set due at ``now`` (the caller commits); a rule set with
    a run already waiting is skipped. Returns the queued runs' ids."""
    queued = (queue_run(db, rule_set, "scheduled") for rule_set in due_rule_sets(db, now))
    return [run_id for run_id in queued if run_id is not None]


def queue_new_mail_runs(
    db: Session, user_id: int, account_id: str, folder: Sequence[str]
) -> list[int]:
    """Queue a run of each runnable ``new_mail`` rule set on the folder (the caller commits).
    Returns the queued runs' ids."""
    statement = (
        runnable_rule_sets("new_mail")
        .where(
            RuleSet.user_id == user_id,
            RuleSet.account_id == account_id,
            RuleSet.folder == list(folder),
        )
        .order_by(RuleSet.id)
    )
    queued = (queue_run(db, rule_set, "new_mail") for rule_set in db.scalars(statement).all())
    return [run_id for run_id in queued if run_id is not None]
