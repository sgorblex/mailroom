"""A queued run of a rule set, carried out: its enabled filters on its folder, for real, and the
report or the error recorded in its ``app.filter_run`` row.

The rule set is checked again when the run starts: one whose account's Run filters was turned off,
or which was disabled, since it was queued records that as its error and touches no mail. A run
whose job is gone (a worker stopped during it) is closed as ``interrupted`` when the next worker
starts (``close_interrupted_runs``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from imap_thingy.filters import MatchTimeoutError
from sqlalchemy import BigInteger, column, func, text, update
from sqlalchemy.exc import SQLAlchemyError

from mailroom.accounts.mail import MailFailure, mail_failure
from mailroom.accounts.store import AccountNotFound
from mailroom.filters.imap import RuleSetFolder
from mailroom.filters.runs import (
    MATCH_TIMEOUT_MESSAGE,
    RunRefused,
    enabled_filters,
    labelled_report,
    run_filters_on,
    thingy_filters,
)
from mailroom.filters.spec import InvalidSpecError
from mailroom.models import FilterRun, RuleSet
from mailroom.worker.app import PROCRASTINATE_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from mailroom.models import Filter
    from mailroom.worker.context import Mailboxes

logger = logging.getLogger(__name__)

MATCHED_KEPT: Final = 200
"""A run's report keeps this many matched messages per filter; ``matched_total`` counts them all
when there were more."""


INTERRUPTED: Final = "interrupted: the worker stopped during the run."
"""The error of a run whose job ended with its worker."""


def execute_run(db: Session, run_id: int, mailboxes: Mailboxes) -> None:
    """Start the queued run ``run_id``, run it and record how it went. Commits ``db``; no
    transaction stays open while the mailbox works. A run removed meanwhile (with its rule set or
    account) is left alone. Whatever goes wrong, the run is finished with its error."""
    run = db.get(FilterRun, run_id)
    rule_set = db.get(RuleSet, run.rule_set_id) if run is not None else None
    if run is None or rule_set is None:
        logger.info("Run %s is gone: nothing to do.", run_id, extra={"run_id": run_id})
        db.rollback()
        return
    trigger, user_id, rule_set_id = run.trigger, rule_set.user_id, rule_set.id
    started_at = datetime.now(UTC)
    result: dict[str, Any] | None = None
    try:
        if not run_filters_on(db, user_id, rule_set.account_id):
            msg = "Run filters is off for this account."
            raise RunRefused("run_filters_off", msg)
        if not rule_set.enabled:
            raise RunRefused("rule_set_disabled", "The rule set is disabled.")
        filters = enabled_filters(db, rule_set_id)
        db.execute(update(FilterRun).where(FilterRun.id == run_id).values(started_at=started_at))
        # Not idle in a transaction while the mailbox works.
        db.commit()
        result, error = _run(rule_set, filters, mailboxes)
    except RunRefused as refused:
        error = refused.error
    except Exception as exc:
        db.rollback()
        # Its text may quote anything, mail included: neither the record nor the log carries it.
        logger.error("A run of rule set %s failed: %s.", rule_set_id, type(exc).__name__)
        error = f"run_failed: {type(exc).__name__}"
    recorded = _finish(db, run_id, started_at, result, error)
    outcome = "ok" if error is None else error.split(":", 1)[0]
    logger.info(
        "rule_set_run",
        extra={
            "event": "rule_set_run",
            "user_id": user_id,
            "rule_set_id": rule_set_id,
            "run_id": run_id,
            "trigger": trigger,
            "outcome": outcome if recorded else "gone",
        },
    )


def _finish(
    db: Session,
    run_id: int,
    started_at: datetime,
    result: dict[str, Any] | None,
    error: str | None,
) -> bool:
    """Record the run's end, report and error; whether its row was still there. A report the
    database refuses is dropped, the run then recording why."""
    statement = (
        update(FilterRun)
        .where(FilterRun.id == run_id)
        .values(
            started_at=func.coalesce(FilterRun.started_at, started_at),
            finished_at=datetime.now(UTC),
        )
        .returning(FilterRun.id)
    )
    try:
        recorded = db.scalar(statement.values(result=result, error=error))
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Run %s's report was refused: %s.", run_id, type(exc).__name__)
        recorded = db.scalar(
            statement.values(result=None, error=f"run_failed: {type(exc).__name__}")
        )
        db.commit()
    return recorded is not None


def close_interrupted_runs(db: Session) -> list[int]:
    """Close every unfinished run whose job is neither waiting nor running (its worker stopped
    during it) with the error ``INTERRUPTED``; returns their ids. For the worker's start, after
    ``mailroom.worker.startup.fail_interrupted_jobs``. Commits ``db``."""
    jobs = text(
        f"SELECT (args->>'run_id')::bigint FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs"
        " WHERE task_name = 'run_rule_set' AND status IN ('todo', 'doing')"
        " AND args ? 'run_id'"
    ).columns(column("run_id", BigInteger))
    now = datetime.now(UTC)
    closed = db.scalars(
        update(FilterRun)
        .where(
            FilterRun.finished_at.is_(None),
            FilterRun.dry_run.is_(False),
            FilterRun.id.not_in(jobs),
        )
        .values(
            started_at=func.coalesce(FilterRun.started_at, now),
            finished_at=now,
            error=INTERRUPTED,
        )
        .returning(FilterRun.id)
    ).all()
    db.commit()
    if closed:
        logger.warning(
            "Closed the runs a stopped worker left unfinished: %s.",
            ", ".join(map(str, closed)),
            extra={"event": "runs_interrupted"},
        )
    return list(closed)


def _run(
    rule_set: RuleSet, filters: Sequence[Filter], mailboxes: Mailboxes
) -> tuple[dict[str, Any] | None, str | None]:
    """The run's ``result`` and ``error``. Raises ``RunRefused`` when it cannot start."""
    try:
        account = mailboxes.account(rule_set.user_id, rule_set.account_id)
    except AccountNotFound:
        raise RunRefused("account_not_found", "No such account") from None
    except MailFailure as unusable:
        raise RunRefused(unusable.code, unusable.message) from None
    folder = RuleSetFolder(account, rule_set.folder)
    try:
        loaded = thingy_filters(rule_set, folder, filters)
    except InvalidSpecError as exc:
        raise RunRefused("invalid_spec", str(exc)) from None
    report = folder.new_report(dry_run=False)
    error = None
    try:
        folder.run_into(report, loaded, dry_run=False)
    except MatchTimeoutError as exc:
        # Its text quotes the user's pattern: the log names the kind only.
        logger.warning("A run of rule set %s stopped: %s.", rule_set.id, type(exc).__name__)
        error = f"match_timeout: {MATCH_TIMEOUT_MESSAGE}"
        report["error"] = error
    except Exception as exc:
        failure = mail_failure(exc, account.password)
        if failure is None:
            # Not about reaching the mailbox: its text may quote anything, mail included, so
            # neither the record nor the log carries it.
            logger.error("A run of rule set %s failed: %s.", rule_set.id, type(exc).__name__)
            error = f"run_failed: {type(exc).__name__}"
        else:
            error = f"{failure.code}: {failure.message}"
        report["error"] = error
    return _bounded(labelled_report(report, filters, loaded)), error


def _bounded(result: dict[str, Any]) -> dict[str, Any]:
    """``result`` with each filter's ``matched`` cut to ``MATCHED_KEPT``."""
    entries = [_bounded_entry(entry) for entry in result["filters"]]
    return {**result, "filters": entries}


def _bounded_entry(entry: dict[str, Any]) -> dict[str, Any]:
    matched = entry["matched"]
    if len(matched) <= MATCHED_KEPT:
        return entry
    return {**entry, "matched": matched[:MATCHED_KEPT], "matched_total": len(matched)}
