"""A queued run of a rule set, carried out: its enabled filters on its folder, for real, and the
report or the error recorded in its ``app.filter_run`` row.

The rule set is checked again when the run starts: one whose account's Run filters was turned off,
or which was disabled, since it was queued records that as its error and touches no mail. A run
whose job is gone (a worker stopped during it) is closed as ``interrupted`` when the next worker
starts (``close_interrupted_runs``). A run whose move copied messages it then could not remove
turns its rule set off (``COPIED_NOT_REMOVED``), so no trigger copies them again.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from email.utils import parseaddr
from functools import partial
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import BigInteger, column, func, text, update
from sqlalchemy.exc import SQLAlchemyError

from mailroom.accounts.store import AccountNotFound, load_accounts, user_store
from mailroom.audit import Entry, write_records_in
from mailroom.filters.runs import (
    MATCH_TIMEOUT_MESSAGE,
    RunRefused,
    enabled_filters,
    engine_filters,
    labelled_report,
    rule_set_folder,
    run_filters_on,
)
from mailroom.filters.spec import InvalidSpecError
from mailroom.mail.connections import (
    PASSWORD_MISSING,
    PASSWORD_MISSING_TEXT,
    PasswordMissing,
    unusable,
)
from mailroom.models import FilterRun, RuleSet
from mailroom.worker.app import PROCRASTINATE_SCHEMA
from mailroom_core.filters import MatchTimeoutError
from mailroom_core.mail import CopiedNotRemovedError, MailError, subject_sha256

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
COPIED_NOT_REMOVED: Final = "copied_not_removed"
"""The error code of a run whose move copied messages but may not have removed them (a server
without MOVE): its rule set is turned off, as a new trigger would copy them again."""
COPIED_NOT_REMOVED_TEXT: Final = (
    "Some messages were copied to the destination but may not have been removed from this "
    "folder, so the rule set was turned off: check both folders before you enable it again."
)


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
    account_id = rule_set.account_id
    started_at = datetime.now(UTC)
    result: dict[str, Any] | None = None
    names: dict[int, str] = {}
    try:
        if not run_filters_on(db, user_id, rule_set.account_id):
            msg = "Run filters is off for this account."
            raise RunRefused("run_filters_off", msg)
        if not rule_set.enabled:
            raise RunRefused("rule_set_disabled", "The rule set is disabled.")
        filters = enabled_filters(db, rule_set_id)
        names = {row.id: row.name for row in filters}
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
    if recorded and error is not None and error.startswith(f"{COPIED_NOT_REMOVED}: "):
        _turn_off(db, user_id, rule_set_id)
    if recorded and result is not None:
        _record_run(db, user_id, result, names, account_id, rule_set_id, run_id, error)
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


def _turn_off(db: Session, user_id: int, rule_set_id: int) -> None:
    """Disable the rule set: its run copied messages it could not remove. Commits ``db``."""
    db.execute(update(RuleSet).where(RuleSet.id == rule_set_id).values(enabled=False))
    db.commit()
    logger.warning(
        "rule_set_turned_off",
        extra={
            "event": "rule_set_turned_off",
            "user_id": user_id,
            "rule_set_id": rule_set_id,
            "reason": COPIED_NOT_REMOVED,
        },
    )


def _record_run(
    db: Session,
    user_id: int,
    result: dict[str, Any],
    names: dict[int, str],
    account_id: str,
    rule_set_id: int,
    run_id: int,
    error: str | None,
) -> None:
    """The run's audit records; a report this cannot read is logged, never fails the run."""
    try:
        entries = run_records(
            result,
            names,
            account_id=account_id,
            rule_set_id=rule_set_id,
            run_id=run_id,
            error=error,
        )
    except Exception as exc:
        logger.warning(
            "audit_write_failed",
            extra={"event": "audit_write_failed", "user_id": user_id, "error": type(exc).__name__},
        )
        return
    write_records_in(db, user_id, entries)


def run_records(
    result: dict[str, Any],
    names: dict[int, str],
    *,
    account_id: str,
    rule_set_id: int,
    run_id: int,
    error: str | None,
) -> list[Entry]:
    """What a real run did, for the audit trail: one record per message each filter acted on, as
    the report lists them (``MATCHED_KEPT`` a filter), one for those it did not list, and, when
    the run failed partway, one ``run_failed`` record for the filter it failed on (the next one
    after those reported, ``names`` being every filter in order): it may have acted on some
    messages. The sender as its bare address and the subject only as its hash, as mailroom-core's
    items carry them; a dry run, nothing."""
    if result.get("dry_run"):
        return []
    folder = "/".join(result.get("folder") or [])
    record = partial(
        Entry, actor="filter", account_id=account_id, rule_set_id=rule_set_id, run_id=run_id
    )
    reports = result.get("filters", [])
    entries: list[Entry] = []
    for report in reports:
        matched = report.get("matched", [])
        filter_id = report.get("filter_id")
        about = {"folder": folder, "filter_id": filter_id, "filter": names.get(filter_id)}
        entry = partial(record, action=str(report.get("action", "")), result="ok")
        entries.extend(entry(detail={**about, **_message(message)}) for message in matched)
        unlisted = report.get("matched_total", len(matched)) - len(matched)
        if unlisted > 0:
            entries.append(entry(detail={**about, "not_listed": unlisted}))
    ordered = list(names.items())
    if error is not None and len(reports) < len(ordered):
        filter_id, name = ordered[len(reports)]
        about = {"folder": folder, "filter_id": filter_id, "filter": name, "error": error}
        entries.append(record(action="run_failed", result="failed", detail=about))
    return entries


def _message(summary: dict[str, Any]) -> dict[str, Any]:
    """A matched message's audit fields: never its subject, only the subject's hash."""
    subject = summary.get("subject")
    sender = parseaddr(str(summary.get("from") or ""))[1]
    fields = {"uid": summary.get("uid"), "from": sender or None, "date": summary.get("date")}
    if isinstance(subject, str):
        fields["subject_sha256"] = subject_sha256(subject)
    return {key: value for key, value in fields.items() if value is not None}


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
    """The run's ``result`` and ``error``: the rule set's filters on its folder, on the user's
    connections from the worker's pool. Raises ``RunRefused`` when it cannot start."""
    store = user_store(mailboxes.engine, rule_set.user_id)
    try:
        refused = unusable(store, mailboxes.subkey, rule_set.account_id)
    except AccountNotFound:
        raise RunRefused("account_not_found", "No such account") from None
    if refused is not None:
        raise RunRefused(*refused)
    with mailboxes.lease(rule_set.user_id, load_accounts(store).revision) as connections:
        folder = rule_set_folder(connections, rule_set)
        try:
            loaded = engine_filters(rule_set, folder, filters)
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
        except CopiedNotRemovedError:
            # Its originals may still match: every search skips \Deleted ones, but a server
            # may not have marked them, so the rule set is turned off rather than run again.
            error = f"{COPIED_NOT_REMOVED}: {COPIED_NOT_REMOVED_TEXT}"
        except MailError as exc:
            error = f"{exc.code}: {exc.message}"
        except PasswordMissing:
            error = f"{PASSWORD_MISSING}: {PASSWORD_MISSING_TEXT}"
        except Exception as exc:
            # Not about reaching the mailbox: its text may quote anything, mail included, so
            # neither the record nor the log carries it.
            logger.error("A run of rule set %s failed: %s.", rule_set.id, type(exc).__name__)
            error = f"run_failed: {type(exc).__name__}"
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
