"""The worker's tasks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import text

from mailroom.filters.runs import prune_runs
from mailroom.models import FilterRun
from mailroom.worker.app import PROCRASTINATE_SCHEMA, app

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom.models import RuleSet

logger = logging.getLogger(__name__)

RUN_RULE_SET: Final = "run_rule_set"
"""The job running one queued ``app.filter_run`` (its ``run_id``) of a rule set."""


@app.task(name="ping")
def ping() -> None:
    """Does nothing: proves a job goes from the queue through a worker."""
    logger.info("pong")


@app.task(name=RUN_RULE_SET)
def run_rule_set(run_id: int) -> None:
    """Run the queued run ``run_id``: start it, run its rule set's enabled filters, and record the
    report or the error. Not written yet: the run stays queued."""
    logger.warning("run_rule_set is not implemented yet", extra={"run_id": run_id})


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
    job_id = run_rule_set.configure(
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
