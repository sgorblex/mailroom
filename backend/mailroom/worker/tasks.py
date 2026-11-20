"""The worker's tasks, and queueing runs of rule sets and the mail index's syncs for them.

A run is queued as an ``app.filter_run`` row with no ``started_at`` plus the job
``run_rule_set(run_id)``, in one transaction: by Run now (the API), by the scheduled dispatcher
below, and by the IDLE supervisor (``mailroom.worker.idle``) when a folder gets new mail. Those run
only with the ``filters`` feature.

With the ``mail`` feature, the index dispatcher queues ``sync_account`` for every account every 5
minutes; it brings the account's index folders in step with its listing and queues ``sync_folder``
for each one due. A folder's job fetches a bounded share and queues the next one behind every
other job, so a large mailbox takes turns with the others, its own filter runs included.
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

from mailroom.config.features import features_from_env
from mailroom.filters.runs import prune_runs
from mailroom.models import AccountFilterSettings, FilterRun, RuleSet
from mailroom.worker.app import PROCRASTINATE_SCHEMA, app
from mailroom.worker.context import worker_context
from mailroom.worker.index import account_folders_due, index_accounts, run_folder_sync
from mailroom.worker.runs import execute_run

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Select
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

RUN_RULE_SET: Final = "run_rule_set"
"""The job running one queued ``app.filter_run`` (its ``run_id``) of a rule set."""
DISPATCH_SCHEDULED: Final = "dispatch_scheduled"
"""The periodic job queueing the scheduled rule sets that are due."""
DISPATCH_INDEX: Final = "dispatch_index"
"""The periodic job queueing ``sync_account`` for every account."""
SYNC_ACCOUNT: Final = "sync_account"
"""The job bringing one account's index folders in step and queueing the syncs due."""
SYNC_FOLDER: Final = "sync_folder"
"""The job running one share of an indexed folder's sync."""
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
    if not features_from_env().filters:
        # The job ends here; the run stays open until the next worker start closes it as
        # interrupted (``close_interrupted_runs``).
        logger.info("Filters are off: run %s is not run.", run_id, extra={"run_id": run_id})
        return
    context = worker_context()
    with context.sessions() as db:
        execute_run(db, run_id, context.mailboxes)


@app.periodic(cron="* * * * *")
@app.task(name=DISPATCH_SCHEDULED, queueing_lock=DISPATCH_SCHEDULED)
def dispatch_scheduled(timestamp: int) -> None:
    """Every minute: queue a run of each scheduled rule set that is due."""
    if not features_from_env().filters:
        return
    with worker_context().sessions() as db:
        queue_scheduled_runs(db, datetime.fromtimestamp(timestamp, UTC))
        db.commit()


@app.periodic(cron="*/5 * * * *")
@app.task(name=DISPATCH_INDEX, queueing_lock=DISPATCH_INDEX)
def dispatch_index(timestamp: int) -> None:
    """Every 5 minutes, with the mail client: queue ``sync_account`` for every account."""
    _ = timestamp
    if not features_from_env().mail:
        return
    context = worker_context()
    with context.sessions() as db:
        queue_index_accounts(db, context.mailboxes.engine)
        db.commit()


@app.task(name=SYNC_ACCOUNT)
def sync_account(user_id: int, account_id: int) -> None:
    """Bring the account's index folders in step with its listing, then queue ``sync_folder``
    for each one due."""
    if not features_from_env().mail:
        return
    context = worker_context()
    with context.sessions() as db:
        due = account_folders_due(db, context.mailboxes, user_id, account_id, datetime.now(UTC))
        for folder_id in due:
            queue_folder_sync(db, user_id, account_id, folder_id)
        db.commit()


@app.task(name=SYNC_FOLDER)
def sync_folder(user_id: int, account_id: int, folder_id: int) -> None:
    """One share of the indexed folder's sync; the next one queued when there is more."""
    if not features_from_env().mail:
        return
    context = worker_context()
    with context.sessions() as db:
        if run_folder_sync(db, context.mailboxes, user_id, account_id, folder_id):
            queue_folder_sync(db, user_id, account_id, folder_id)
            db.commit()


def mailbox_lock(user_id: int, account_id: int) -> str:
    """procrastinate ``lock`` of every job acting on one mailbox: they run one at a time."""
    return f"mailbox:{user_id}:{account_id}"


def rule_set_queueing_lock(rule_set_id: int, account_id: int) -> str:
    """procrastinate ``queueing_lock``: one waiting run per rule set and account at most."""
    return f"rule_set:{rule_set_id}:{account_id}"


def _defer(db: Session, name: str, *, lock: str, queueing_lock: str, **arguments: int) -> int:
    """Queue the task ``name`` inside ``db``'s transaction; its job id. Raises procrastinate's
    ``AlreadyEnqueued`` when a job with ``queueing_lock`` is already waiting (the transaction is
    then unusable: run this inside a savepoint)."""
    connection = db.connection()
    search_path = connection.execute(text("SELECT current_setting('search_path')")).scalar_one()
    # procrastinate's SQL names its tables and functions unqualified.
    connection.execute(
        text("SELECT set_config('search_path', :path, true)"),
        {"path": f"{PROCRASTINATE_SCHEMA}, {search_path}"},
    )
    job_id = configure_task(
        name=name,
        job_manager=_IN_TRANSACTION,
        lock=lock,
        queueing_lock=queueing_lock,
        connection=connection.connection.driver_connection,
    ).defer(**arguments)
    connection.execute(text("SELECT set_config('search_path', :path, true)"), {"path": search_path})
    return job_id


def defer_rule_set_run(
    db: Session, *, run_id: int, rule_set_id: int, user_id: int, account_id: int
) -> int:
    """Queue ``run_rule_set`` for ``run_id`` inside ``db``'s transaction, so the job exists exactly
    when the run row does. Returns the job id. Raises procrastinate's ``AlreadyEnqueued`` when a
    run of the rule set on the account is already waiting (the transaction is then unusable: run
    this inside a savepoint)."""
    return _defer(
        db,
        RUN_RULE_SET,
        lock=mailbox_lock(user_id, account_id),
        queueing_lock=rule_set_queueing_lock(rule_set_id, account_id),
        run_id=run_id,
    )


def _defer_once(db: Session, name: str, *, lock: str, queueing_lock: str, **arguments: int) -> bool:
    """``_defer`` in a savepoint of ``db``'s transaction (the caller commits); False when a job
    with ``queueing_lock`` was already waiting."""
    try:
        with db.begin_nested():
            _defer(db, name, lock=lock, queueing_lock=queueing_lock, **arguments)
    except AlreadyEnqueued:
        return False
    return True


def queue_index_accounts(db: Session, engine: Engine) -> int:
    """Queue ``sync_account`` for every account of every user (the caller commits), one waiting
    per account at most; how many were queued."""
    return sum(
        _defer_once(
            db,
            SYNC_ACCOUNT,
            lock=mailbox_lock(user_id, account_id),
            queueing_lock=f"index_account:{user_id}:{account_id}",
            user_id=user_id,
            account_id=account_id,
        )
        for user_id, account_id in index_accounts(db, engine)
    )


def queue_folder_sync(db: Session, user_id: int, account_id: int, folder_id: int) -> bool:
    """Queue ``sync_folder`` for the indexed folder (the caller commits), one waiting per folder
    at most; whether it was queued."""
    return _defer_once(
        db,
        SYNC_FOLDER,
        lock=mailbox_lock(user_id, account_id),
        queueing_lock=f"index_folder:{folder_id}",
        user_id=user_id,
        account_id=account_id,
        folder_id=folder_id,
    )


def folder_syncs_running(db: Session, folder_ids: Sequence[int]) -> set[int]:
    """Which of the indexed folders ``folder_ids`` have a ``sync_folder`` job running now: one
    that surveyed its folder before a write may store the flags it read over that write's."""
    if not folder_ids:
        return set()
    query = text(
        f"SELECT (args->>'folder_id')::bigint FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs"
        " WHERE task_name = :name AND status = 'doing'"
        " AND (args->>'folder_id')::bigint = ANY(:ids)"
    )
    return set(db.scalars(query, {"name": SYNC_FOLDER, "ids": list(folder_ids)}))


def queue_run(db: Session, rule_set: RuleSet, account_id: int, trigger: str) -> int | None:
    """Queue a run of ``rule_set`` on ``account_id`` (its own, or one of an all-accounts rule
    set's) in ``db``'s transaction (the caller commits) and prune that account's history of it.
    Returns the run's id, or None when a run of it on the account is already waiting."""
    run = FilterRun(rule_set_id=rule_set.id, account_id=account_id, trigger=trigger, dry_run=False)
    try:
        with db.begin_nested():
            db.add(run)
            db.flush()
            defer_rule_set_run(
                db,
                run_id=run.id,
                rule_set_id=rule_set.id,
                user_id=rule_set.user_id,
                account_id=account_id,
            )
    except AlreadyEnqueued:
        return None
    prune_runs(db, rule_set.id, account_id)
    logger.info(
        "run_queued",
        extra={
            "event": "run_queued",
            "user_id": rule_set.user_id,
            "rule_set_id": rule_set.id,
            "account_id": account_id,
            "run_id": run.id,
            "trigger": trigger,
        },
    )
    return run.id


def runnable_rule_sets(trigger: str) -> Select[RuleSet, int]:
    """The enabled rule sets with ``trigger``, each with an account it runs on whose Run filters
    is on: its own, or each such account of the user's for an all-accounts rule set."""
    return (
        select(RuleSet, AccountFilterSettings.account_id)
        .join(
            AccountFilterSettings,
            and_(
                AccountFilterSettings.user_id == RuleSet.user_id,
                or_(
                    RuleSet.account_id.is_(None),
                    AccountFilterSettings.account_id == RuleSet.account_id,
                ),
            ),
        )
        .where(RuleSet.enabled, RuleSet.trigger == trigger, AccountFilterSettings.run_filters)
    )


def due_rule_sets(db: Session, now: datetime) -> Sequence[tuple[RuleSet, int]]:
    """The runnable scheduled rule sets, each with its account, never run there on schedule, or
    last queued there on schedule at least their interval (less ``SCHEDULE_SLACK``) before
    ``now``."""
    last = (
        select(func.max(FilterRun.created_at))
        .where(
            FilterRun.rule_set_id == RuleSet.id,
            FilterRun.account_id == AccountFilterSettings.account_id,
            FilterRun.trigger == "scheduled",
        )
        .scalar_subquery()
    )
    every = func.make_interval(0, 0, 0, 0, 0, RuleSet.interval_minutes)
    statement = (
        runnable_rule_sets("scheduled")
        .where(or_(last.is_(None), last + every <= now + SCHEDULE_SLACK))
        .order_by(RuleSet.id, AccountFilterSettings.account_id)
    )
    return [(rule_set, account_id) for rule_set, account_id in db.execute(statement).all()]


def queue_scheduled_runs(db: Session, now: datetime) -> list[int]:
    """Queue a run of each scheduled rule set due at ``now`` on each of its accounts (the caller
    commits); one with a run already waiting there is skipped. Returns the queued runs' ids."""
    queued = (
        queue_run(db, rule_set, account_id, "scheduled")
        for rule_set, account_id in due_rule_sets(db, now)
    )
    return [run_id for run_id in queued if run_id is not None]


def queue_new_mail_runs(
    db: Session, user_id: int, account_id: int, folder: Sequence[str]
) -> list[int]:
    """Queue a run on ``account_id`` of each runnable ``new_mail`` rule set on the folder, the
    user's all-accounts ones included (the caller commits). Returns the queued runs' ids."""
    statement = (
        runnable_rule_sets("new_mail")
        .where(
            RuleSet.user_id == user_id,
            AccountFilterSettings.account_id == account_id,
            RuleSet.folder == list(folder),
        )
        .order_by(RuleSet.id)
    )
    queued = (
        queue_run(db, rule_set, account_id, "new_mail")
        for rule_set, _ in db.execute(statement).all()
    )
    return [run_id for run_id in queued if run_id is not None]
