"""Running a rule set: its enabled filters as the filter engine's filters, the dry run, and the run
history (``app.filter_run``).

A run's ``result`` is the filter engine's folder report (``mailroom_core.filters.FolderReport``)
with each filter's entry carrying the ``filter_id`` it came from and the spec as stored; a dry run
adds ``limited_to_newest``. The engine runs on the users' mailroom-core connections: the API's
``MailPool`` for a dry run, the worker's for a run (``mailroom.worker.runs``).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import delete, select, text

from mailroom.accounts.store import AccountNotFound, load_accounts
from mailroom.filters.spec import InvalidSpecError, loaded_filter
from mailroom.mail.connections import (
    PASSWORD_MISSING,
    PASSWORD_MISSING_TEXT,
    PasswordMissing,
    unusable,
)
from mailroom.mail.pool import DeadlineExceeded
from mailroom.models import AccountFilterSettings, Filter, FilterRun
from mailroom_core.filters import Account, Folder, MatchTimeoutError, Path, starred_only
from mailroom_core.filters import Filter as EngineFilter
from mailroom_core.mail import MailError

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    from mailroom.context import AppContext
    from mailroom.models import RuleSet
    from mailroom_core.filters import FolderReport
    from mailroom_core.mail import MailConnections

logger = logging.getLogger(__name__)

DRY_RUN_MAX_MESSAGES: Final = 500
"""A dry run looks at the folder's newest messages only, this many at most."""
DRY_RUN_SECONDS: Final = 30.0
"""A dry run still going after this long is stopped: the pool cuts the user's connections to the
account that are in use (``MailPool.call``'s deadline)."""
RUNS_KEPT: Final = 100
"""Run history kept per rule set; older runs are pruned as new ones are recorded."""


def run_filters_on(db: Session, user_id: int, account_id: str) -> bool:
    """Whether the user's Run filters switch is on for ``account_id`` (no row: off)."""
    return bool(
        db.scalar(
            select(AccountFilterSettings.run_filters).where(
                AccountFilterSettings.user_id == user_id,
                AccountFilterSettings.account_id == account_id,
            )
        )
    )


def enabled_filters(db: Session, rule_set_id: int) -> list[Filter]:
    """The rule set's enabled filters, in their order."""
    return list(
        db.scalars(
            select(Filter)
            .where(Filter.rule_set_id == rule_set_id, Filter.enabled)
            .order_by(Filter.position, Filter.id)
        )
    )


def rule_set_folder(connections: MailConnections, rule_set: RuleSet) -> Folder:
    """The filter engine's folder the rule set runs on, on ``connections``: its segments joined
    with the delimiter the server reports when it runs."""
    return Folder(Account(connections, rule_set.account_id), Path(list(rule_set.folder)))


def engine_filters(
    rule_set: RuleSet, folder: Folder, filters: Sequence[Filter]
) -> list[EngineFilter]:
    """``filters`` as the filter engine runs them on ``folder``: limited to starred messages, which
    they unstar, when the rule set is ``starred_only``. Raises ``InvalidSpecError`` for a stored
    spec the filter engine no longer loads."""
    loaded = [loaded_filter(row.spec) for row in filters]
    if rule_set.starred_only:
        [(_folder, loaded)] = starred_only([(folder, loaded)])
    return loaded


def labelled_report(
    report: FolderReport, filters: Sequence[Filter], loaded: Sequence[EngineFilter]
) -> dict[str, Any]:
    """``report`` as a run's ``result``: each filter's entry with its ``filter_id``, the spec as
    stored (the one run may be wrapped, e.g. for starred only), the action's name as ``loaded``
    (what ran) has it, and no NUL character, which ``jsonb`` refuses (a message header may carry
    one).

    The name is set here because the filter engine writes a filter's ``repr`` (``Star()``) when it
    can serialise the filter and its name (``star``) when not: every run names an action the one
    way, whatever the filter it ran."""
    entries = [
        {
            **_without_nul(entry),
            "filter_id": row.id,
            "spec": row.spec,
            "action": _without_nul(str(ran.action)),
        }
        for entry, row, ran in zip(report["filters"], filters, loaded, strict=False)
    ]
    return {**_without_nul(report), "filters": entries}


def _without_nul(value: Any) -> Any:  # noqa: ANN401 -- any JSON value
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {key: _without_nul(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_nul(item) for item in value]
    return value


MATCH_TIMEOUT_MESSAGE: Final = (
    "A filter's regular expression took too long on a message: simplify the pattern."
)
"""A run stopped by the filter engine's ``MatchTimeoutError`` (whose text quotes the pattern)."""


class DryRunBusy(Exception):  # noqa: N818 -- the outcome of a dry run, not a bug
    """Another dry run of the same user is still going."""


@contextmanager
def dry_run_slot(engine: Engine, user_id: int) -> Iterator[None]:
    """Hold the user's one dry run slot for the block: a session advisory lock, on a connection
    of its own and outside any transaction, so it holds across API processes. Raises
    ``DryRunBusy`` while another dry run of theirs holds it."""
    key = {"key": f"mailroom.dry_run:{user_id}"}
    with engine.connect() as connection:
        held = connection.scalar(
            text("select pg_try_advisory_lock(hashtextextended(:key, 0))"), key
        )
        connection.commit()
        if not held:
            raise DryRunBusy
        try:
            yield
        finally:
            connection.execute(text("select pg_advisory_unlock(hashtextextended(:key, 0))"), key)
            connection.commit()


class RunRefused(Exception):  # noqa: N818 -- the outcome of a run, not a bug
    """Why a run did not run, or stopped: ``code`` names the kind, ``message`` words it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def error(self) -> str:
        """As a run's ``error`` records it."""
        return f"{self.code}: {self.message}"


def dry_run_result(
    ctx: AppContext, user_id: int, rule_set: RuleSet, filters: Sequence[Filter]
) -> dict[str, Any]:
    """The ``result`` of a dry run of ``rule_set``'s ``filters``: selected read-only, on the
    folder's newest ``DRY_RUN_MAX_MESSAGES`` messages, on a connection of the API's mail pool for
    ``DRY_RUN_SECONDS`` at most. Raises ``RunRefused``: ``account_not_found``, ``invalid_spec``,
    ``dry_run_timeout``, ``match_timeout`` (a pattern took longer than the filter engine's match
    timeout), the app's ``secrets_key_missing`` and ``password_missing``, mailroom-core's mail
    error codes, or ``run_failed`` for anything else, whose text is neither kept nor logged (it
    may quote anything, mail included)."""
    try:
        return _dry_run_result(ctx, user_id, rule_set, filters)
    except RunRefused:
        raise
    except Exception as exc:
        name = type(exc).__name__
        logger.error("A dry run of rule set %s failed: %s.", rule_set.id, name)
        raise RunRefused("run_failed", name) from None


def _dry_run_result(
    ctx: AppContext, user_id: int, rule_set: RuleSet, filters: Sequence[Filter]
) -> dict[str, Any]:
    store = ctx.store(user_id)
    account_id = rule_set.account_id
    try:
        refused = unusable(store, ctx.subkey, account_id)
    except AccountNotFound:
        raise RunRefused("account_not_found", "No such account") from None
    if refused is not None:
        raise RunRefused(*refused)
    pool = ctx.mail_pool
    try:
        with pool.lease(ctx, user_id, load_accounts(store).revision) as connections:
            folder = rule_set_folder(connections, rule_set)
            loaded = engine_filters(rule_set, folder, filters)
            report = pool.call(
                connections,
                user_id,
                [account_id],
                lambda: folder.run(loaded, dry_run=True, newest=DRY_RUN_MAX_MESSAGES),
                deadline=DRY_RUN_SECONDS,
            )
    except InvalidSpecError as exc:
        raise RunRefused("invalid_spec", str(exc)) from None
    except DeadlineExceeded:
        msg = "The dry run took too long: try a narrower filter or a smaller folder."
        raise RunRefused("dry_run_timeout", msg) from None
    except MatchTimeoutError:
        raise RunRefused("match_timeout", MATCH_TIMEOUT_MESSAGE) from None
    except MailError as error:
        raise RunRefused(error.code, error.message) from None
    except PasswordMissing:
        raise RunRefused(PASSWORD_MISSING, PASSWORD_MISSING_TEXT) from None
    return {
        **labelled_report(report, filters, loaded),
        "limited_to_newest": report.get("limited_to_newest"),
    }


def runs_of(db: Session, rule_set_id: int, limit: int) -> list[FilterRun]:
    """The rule set's newest runs, newest first."""
    return list(
        db.scalars(
            select(FilterRun)
            .where(FilterRun.rule_set_id == rule_set_id)
            .order_by(FilterRun.id.desc())
            .limit(limit)
        )
    )


def prune_runs(db: Session, rule_set_id: int, keep: int = RUNS_KEPT) -> None:
    """Forget all but the rule set's newest ``keep`` runs; a run queued or in progress stays."""
    newest = (
        select(FilterRun.id)
        .where(FilterRun.rule_set_id == rule_set_id)
        .order_by(FilterRun.id.desc())
        .limit(keep)
    )
    db.execute(
        delete(FilterRun).where(
            FilterRun.rule_set_id == rule_set_id,
            FilterRun.finished_at.is_not(None),
            FilterRun.id.not_in(newest),
        )
    )
