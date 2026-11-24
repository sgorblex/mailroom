"""The worker's tasks, and queueing runs of rule sets and the mail index's syncs for them.

A run is queued as an ``app.filter_run`` row with no ``started_at`` plus the job
``run_rule_set(run_id)``, in one transaction: by Run now (the API), by the scheduled dispatcher
below, and by the IDLE supervisor (``mailroom.worker.idle``) when a folder gets new mail. Those run
only with the ``filters`` feature.

With the ``mail`` feature, the index dispatcher queues ``sync_account`` for every account every 5
minutes; it brings the account's index folders in step with its listing and queues one
``sync_due_folders`` for all those due: the turn, gone through in shares of a few seconds, each
queueing the next. ``sync_folder`` syncs one folder: one that IDLE, a write of the app, a held
send or a filter run asks for, or one a job left with more to fetch. The index's routine work (the
turn, and what a job left) is queued at ``ROUTINE_PRIORITY``, so a sync asked for and a filter run
wait at most for the share under way, however long the turn.

A held send's ``send_draft`` is queued by the mail service (``defer_scheduled_send``) in the
transaction writing its ``app.scheduled_send`` row, to run at its time (``mailroom.worker.sends``);
a snooze's ``wake_snooze`` likewise with its ``app.snooze`` row (``mailroom.worker.snoozes``).
An index sync's new rows in INBOX queue ``vacation_reply`` for each while the account's vacation
reply answers (``mailroom.mail.vacation``), in the transaction storing them.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from procrastinate import SyncPsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued
from procrastinate.manager import JobManager
from procrastinate.tasks import configure_task
from sqlalchemy import and_, func, or_, select, text

from mailroom.config.features import features_from_env
from mailroom.filters.runs import prune_runs
from mailroom.models import AccountFilterSettings, FilterRun, RuleSet, ScheduledSend, Snooze
from mailroom.worker.app import PROCRASTINATE_SCHEMA, app
from mailroom.worker.context import worker_context
from mailroom.worker.index import (
    account_folders_due,
    index_accounts,
    run_folder_sync,
    run_turn_share,
)
from mailroom.worker.runs import execute_run
from mailroom.worker.sends import send_scheduled
from mailroom.worker.snoozes import wake_snooze as wake_snoozed
from mailroom.worker.vacation import reply_vacation

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from procrastinate.types import JSONValue
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
"""The job bringing one account's index folders in step and queueing the turn of those due."""
SYNC_DUE_FOLDERS: Final = "sync_due_folders"
"""The job running one share of the turn over an account's due folders (its ``folder_ids``)."""
SYNC_FOLDER: Final = "sync_folder"
"""The job running one share of an indexed folder's sync."""
SEND_DRAFT: Final = "send_draft"
"""The job sending one scheduled draft (its ``app.scheduled_send`` row) through the send gate."""
WAKE_SNOOZE: Final = "wake_snooze"
"""The job bringing one snoozed message (its ``app.snooze`` row) back to its folder."""
VACATION_REPLY: Final = "vacation_reply"
"""The job answering one new message (its ref) with its account's vacation reply."""
ASKED_PRIORITY: Final = 0
"""procrastinate's default priority: what someone waits for (a filter run, a sync of a folder that
changed)."""
ROUTINE_PRIORITY: Final = -1
"""The priority of the index's routine work, which every other job goes ahead of."""
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
    """Bring the account's index folders in step with its listing, then queue the turn of those
    due. While an earlier turn still waits, it goes on instead: begun again every time, a turn
    longer than the 5 minutes would never reach its last folders."""
    if not features_from_env().mail:
        return
    context = worker_context()
    with context.sessions() as db:
        due = account_folders_due(db, context.mailboxes, user_id, account_id, datetime.now(UTC))
        queue_turn(db, user_id, account_id, due)
        db.commit()


@app.task(name=SYNC_DUE_FOLDERS)
def sync_due_folders(user_id: int, account_id: int, folder_ids: list[int]) -> None:
    """One share of the turn over the account's due folders; the rest queued as the next one, and
    each folder with more to fetch as a ``sync_folder`` of its own."""
    if not features_from_env().mail:
        return
    context = worker_context()
    with context.sessions() as db:
        left = run_turn_share(db, context.mailboxes, user_id, account_id, folder_ids)
        queue_turn(db, user_id, account_id, left.rest)
        for folder_id in left.unfinished:
            queue_folder_sync(db, user_id, account_id, folder_id, priority=ROUTINE_PRIORITY)
        db.commit()


@app.task(name=SYNC_FOLDER)
def sync_folder(user_id: int, account_id: int, folder_id: int) -> None:
    """One share of the indexed folder's sync; the next one queued when there is more."""
    if not features_from_env().mail:
        return
    context = worker_context()
    with context.sessions() as db:
        if run_folder_sync(db, context.mailboxes, user_id, account_id, folder_id):
            # A first sync's, or a burst's older mail: its newest is in.
            queue_folder_sync(db, user_id, account_id, folder_id, priority=ROUTINE_PRIORITY)
            db.commit()


@app.task(name=SEND_DRAFT)
def send_draft(scheduled_id: int) -> None:
    """Send the scheduled draft ``scheduled_id`` (``mailroom.worker.sends.send_scheduled``)."""
    if not features_from_env().mail:
        # The row stays held: the user can cancel it, and it goes nowhere.
        return
    context = worker_context()
    send_scheduled(context.sessions, context.mailboxes, scheduled_id)


@app.task(name=WAKE_SNOOZE)
def wake_snooze(snooze_id: int) -> None:
    """Bring the snoozed message ``snooze_id`` back (``mailroom.worker.snoozes.wake_snooze``)."""
    if not features_from_env().mail:
        # The row stays: the next worker with the mail client queues its job again.
        return
    context = worker_context()
    wake_snoozed(context.sessions, context.mailboxes, snooze_id)


@app.task(name=VACATION_REPLY)
def vacation_reply(user_id: int, account_id: int, ref: str) -> None:
    """Answer the new message ``ref`` with the account's vacation reply, unless its checks
    refuse it (``mailroom.worker.vacation.reply_vacation``)."""
    if not features_from_env().mail:
        return
    context = worker_context()
    reply_vacation(context.sessions, context.mailboxes, user_id, account_id, ref)


def mailbox_lock(user_id: int, account_id: int) -> str:
    """procrastinate ``lock`` of every job acting on one mailbox: they run one at a time."""
    return f"mailbox:{user_id}:{account_id}"


def rule_set_queueing_lock(rule_set_id: int, account_id: int) -> str:
    """procrastinate ``queueing_lock``: one waiting run per rule set and account at most."""
    return f"rule_set:{rule_set_id}:{account_id}"


@contextmanager
def procrastinate_sql(db: Session) -> Iterator[Any]:
    """``db``'s psycopg connection with procrastinate's schema first on its search path, for the
    block: procrastinate's SQL names its tables and functions unqualified."""
    connection = db.connection()
    search_path = connection.execute(text("SELECT current_setting('search_path')")).scalar_one()
    connection.execute(
        text("SELECT set_config('search_path', :path, true)"),
        {"path": f"{PROCRASTINATE_SCHEMA}, {search_path}"},
    )
    yield connection.connection.driver_connection
    connection.execute(text("SELECT set_config('search_path', :path, true)"), {"path": search_path})


def _defer(
    db: Session,
    name: str,
    *,
    lock: str | None,
    queueing_lock: str,
    schedule_at: datetime | None = None,
    priority: int = ASKED_PRIORITY,
    arguments: Mapping[str, JSONValue],
) -> int:
    """Queue the task ``name`` with ``arguments`` inside ``db``'s transaction, to run from
    ``schedule_at`` (else at once); its job id. Raises procrastinate's ``AlreadyEnqueued`` when a
    job with ``queueing_lock`` is already waiting (the transaction is then unusable: run this
    inside a savepoint)."""
    with procrastinate_sql(db) as connection:
        return configure_task(
            name=name,
            job_manager=_IN_TRANSACTION,
            lock=lock,
            queueing_lock=queueing_lock,
            schedule_at=schedule_at,
            priority=priority,
            connection=connection,
        ).defer(**arguments)


def cancel_job(db: Session, job_id: int) -> bool:
    """Delete the job ``job_id`` inside ``db``'s transaction while it is waiting; whether it
    was."""
    with procrastinate_sql(db) as connection:
        return _IN_TRANSACTION.cancel_job_by_id(job_id, delete_job=True, connection=connection)


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
        arguments={"run_id": run_id},
    )


def defer_scheduled_send(db: Session, *, scheduled_id: int, send_at: datetime) -> int:
    """Queue ``send_draft`` for the scheduled send ``scheduled_id`` from ``send_at``, inside
    ``db``'s transaction, so the job exists exactly when the row does; its job id. Without the
    mailbox's lock: a job waiting for its time would hold back every later one of the mailbox,
    and a send goes alongside them as the API's does."""
    return _defer(
        db,
        SEND_DRAFT,
        lock=None,
        queueing_lock=f"scheduled_send:{scheduled_id}",
        schedule_at=send_at,
        arguments={"scheduled_id": scheduled_id},
    )


def defer_snooze_wake(db: Session, *, snooze_id: int, until: datetime | None) -> int:
    """Queue ``wake_snooze`` for the snooze ``snooze_id`` from ``until`` (None: at once), inside
    ``db``'s transaction, so the job exists exactly when the row does; its job id. Without the
    mailbox's lock, as ``send_draft``: waiting for its time, it would hold back every later job
    of the mailbox."""
    return _defer(
        db,
        WAKE_SNOOZE,
        lock=None,
        queueing_lock=f"snooze:{snooze_id}",
        schedule_at=until,
        arguments={"snooze_id": snooze_id},
    )


def _alive(db: Session, task: str) -> set[int]:
    """The ids of the jobs of ``task`` waiting or running."""
    queued = text(
        f"SELECT id FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs"
        " WHERE task_name = :name AND status IN ('todo', 'doing')"
    )
    return set(db.scalars(queued, {"name": task}))


def requeue_interrupted_sends(db: Session) -> list[int]:
    """Queue ``send_draft`` again for each scheduled send whose job is neither waiting nor running,
    and commit (at start, once a stopped worker's jobs are marked failed): one left ``sending``
    runs at once, and its send key keeps it from going out twice. The rows' ids."""
    alive = _alive(db, SEND_DRAFT)
    lost = [
        scheduled
        for scheduled in db.scalars(
            select(ScheduledSend)
            .where(ScheduledSend.state.in_(("held", "sending")))
            .order_by(ScheduledSend.id)
        )
        if scheduled.job_id not in alive
    ]
    for scheduled in lost:
        scheduled.job_id = defer_scheduled_send(
            db, scheduled_id=scheduled.id, send_at=scheduled.send_at
        )
    db.commit()
    if lost:
        logger.warning(
            "scheduled_sends_requeued",
            extra={"event": "scheduled_sends_requeued", "count": len(lost)},
        )
    return [scheduled.id for scheduled in lost]


def requeue_lost_snoozes(db: Session) -> list[int]:
    """Queue ``wake_snooze`` again for each snooze whose job is neither waiting nor running, and
    commit (at start, once a stopped worker's jobs are marked failed): one due runs at once. The
    rows' ids."""
    alive = _alive(db, WAKE_SNOOZE)
    lost = [
        row
        for row in db.scalars(select(Snooze).order_by(Snooze.id).with_for_update())
        if row.job_id not in alive
    ]
    for row in lost:
        row.job_id = defer_snooze_wake(db, snooze_id=row.id, until=row.until)
    db.commit()
    if lost:
        logger.warning("snoozes_requeued", extra={"event": "snoozes_requeued", "count": len(lost)})
    return [row.id for row in lost]


def _defer_once(
    db: Session,
    name: str,
    *,
    lock: str,
    queueing_lock: str,
    priority: int = ASKED_PRIORITY,
    **arguments: JSONValue,
) -> bool:
    """``_defer`` in a savepoint of ``db``'s transaction (the caller commits); False when a job
    with ``queueing_lock`` was already waiting, which then takes ``priority`` if that is higher."""
    try:
        with db.begin_nested():
            _defer(
                db,
                name,
                lock=lock,
                queueing_lock=queueing_lock,
                priority=priority,
                arguments=arguments,
            )
    except AlreadyEnqueued:
        # One that has started meanwhile keeps its own: it reads the mailbox after this call.
        db.execute(
            text(
                f"UPDATE {PROCRASTINATE_SCHEMA}.procrastinate_jobs SET priority = :priority"
                " WHERE queueing_lock = :queueing_lock AND status = 'todo'"
                " AND priority < :priority"
            ),
            {"priority": priority, "queueing_lock": queueing_lock},
        )
        return False
    return True


def defer_vacation_reply(db: Session, *, user_id: int, account_id: int, ref: str) -> bool:
    """Queue ``vacation_reply`` for the message ``ref`` (the caller commits), once while one
    waits; whether it was queued."""
    return _defer_once(
        db,
        VACATION_REPLY,
        lock=mailbox_lock(user_id, account_id),
        queueing_lock=f"vacation:{user_id}:{ref}",
        user_id=user_id,
        account_id=account_id,
        ref=ref,
    )


def queue_index_accounts(db: Session, engine: Engine) -> int:
    """Queue ``sync_account`` for every account of every user (the caller commits), one waiting
    per account at most; how many were queued."""
    return sum(
        _defer_once(
            db,
            SYNC_ACCOUNT,
            lock=mailbox_lock(user_id, account_id),
            queueing_lock=f"index_account:{user_id}:{account_id}",
            priority=ROUTINE_PRIORITY,
            user_id=user_id,
            account_id=account_id,
        )
        for user_id, account_id in index_accounts(db, engine)
    )


def queue_turn(db: Session, user_id: int, account_id: int, folder_ids: Sequence[int]) -> bool:
    """Queue ``sync_due_folders`` for the account's folders ``folder_ids``, if any (the caller
    commits), one waiting per account at most; whether it was queued."""
    if not folder_ids:
        return False
    return _defer_once(
        db,
        SYNC_DUE_FOLDERS,
        lock=mailbox_lock(user_id, account_id),
        queueing_lock=f"index_turn:{user_id}:{account_id}",
        priority=ROUTINE_PRIORITY,
        user_id=user_id,
        account_id=account_id,
        folder_ids=list(folder_ids),
    )


def queue_folder_sync(
    db: Session,
    user_id: int,
    account_id: int,
    folder_id: int,
    *,
    priority: int = ASKED_PRIORITY,
) -> bool:
    """Queue ``sync_folder`` for the indexed folder (the caller commits), one waiting per folder
    at most, at the highest ``priority`` asked of it; whether it was queued."""
    return _defer_once(
        db,
        SYNC_FOLDER,
        lock=mailbox_lock(user_id, account_id),
        queueing_lock=f"index_folder:{folder_id}",
        priority=priority,
        user_id=user_id,
        account_id=account_id,
        folder_id=folder_id,
    )


def folder_syncs_running(db: Session, folder_ids: Sequence[int]) -> set[int]:
    """Which of the indexed folders ``folder_ids`` may be syncing now: those of a ``sync_folder``
    running, and every one of a turn's share running. A sync that surveyed its folder before a
    write may store the flags it read over that write's."""
    if not folder_ids:
        return set()
    query = text(
        "SELECT folder.id::bigint"
        f" FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs AS job,"
        " jsonb_array_elements_text(CASE job.task_name"
        " WHEN :folder THEN jsonb_build_array(job.args->'folder_id')"
        " WHEN :turn THEN job.args->'folder_ids' END) AS folder(id)"
        " WHERE job.task_name IN (:folder, :turn) AND job.status = 'doing'"
        " AND folder.id::bigint = ANY(:ids)"
    )
    names = {"folder": SYNC_FOLDER, "turn": SYNC_DUE_FOLDERS}
    return set(db.scalars(query, {**names, "ids": list(folder_ids)}))


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
