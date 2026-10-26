"""Running a rule set: its enabled filters as imap-thingy filters, the dry run, and the run
history (``app.filter_run``).

A run's ``result`` is imap-thingy's folder report (``imap_thingy.report.FolderReport``) with each
filter's entry carrying the ``filter_id`` it came from and the spec as stored; a dry run adds
``limited_to_newest``.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from imap_thingy.filters import Filter as ImapFilter
from imap_thingy.filters import MatchTimeoutError
from imap_thingy.runner import starred_only
from sqlalchemy import delete, select, text

from mailroom.accounts.mail import MailFailure, mail_failure, mailbox_account
from mailroom.accounts.store import AccountNotFound
from mailroom.filters.imap import RuleSetFolder
from mailroom.filters.spec import InvalidSpecError, loaded_filter
from mailroom.models import AccountFilterSettings, Filter, FilterRun

if TYPE_CHECKING:
    import ssl
    from collections.abc import Iterator, Sequence

    from imap_thingy.report import FolderReport
    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.store import AccountStore
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    from mailroom.models import RuleSet

logger = logging.getLogger(__name__)

DRY_RUN_MAX_MESSAGES: Final = 500
"""A dry run looks at the folder's newest messages only, this many at most."""
DRY_RUN_SECONDS: Final = 30.0
"""A dry run still going after this long is stopped (its connection closed)."""
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


def thingy_filters(
    rule_set: RuleSet, folder: RuleSetFolder, filters: Sequence[Filter]
) -> list[ImapFilter]:
    """``filters`` as imap-thingy runs them on ``folder``: limited to starred messages, which
    they unstar, when the rule set is ``starred_only``. Raises ``InvalidSpecError`` for a stored
    spec this imap-thingy no longer loads."""
    loaded = [loaded_filter(row.spec) for row in filters]
    if rule_set.starred_only:
        [(_folder, loaded)] = starred_only([(folder, loaded)])
    return loaded


def labelled_report(
    report: FolderReport, filters: Sequence[Filter], loaded: Sequence[ImapFilter]
) -> dict[str, Any]:
    """``report`` as a run's ``result``: each filter's entry with its ``filter_id``, the spec as
    stored (the one run may be wrapped, e.g. for starred only), the action's name as ``loaded``
    (what ran) has it, and no NUL character, which ``jsonb`` refuses (a message header may carry
    one).

    The name is set here because imap-thingy writes a filter's ``repr`` (``Star()``) when it can
    serialise the filter and its name (``Star``) when not, and a dry run's filters, bounded to the
    newest messages, cannot be: a dry run and a real run would name one action two ways."""
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
"""A run stopped by imap-thingy's ``MatchTimeoutError`` (whose text quotes the pattern)."""


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


class DryRunTimeout(Exception):  # noqa: N818 -- the outcome of a dry run, not a bug
    """The dry run took longer than it may."""


@dataclass(frozen=True, slots=True)
class DryRun:
    report: FolderReport
    limited_to_newest: int | None
    """The number of newest messages looked at, when the folder holds more; None when all were."""


def dry_run(
    folder: RuleSetFolder,
    filters: Sequence[ImapFilter],
    *,
    max_messages: int = DRY_RUN_MAX_MESSAGES,
    seconds: float = DRY_RUN_SECONDS,
) -> DryRun:
    """Run ``filters`` on ``folder`` without acting (selected read-only), on its newest
    ``max_messages`` messages, for ``seconds`` at most. Raises ``DryRunTimeout``, or what the
    mailbox raises (``mailroom.accounts.mail.mail_failure`` names it)."""
    newest = folder.newest(max_messages)
    bounded = [ImapFilter(newest & f.criterion, f.action) for f in filters]
    timer = threading.Timer(seconds, folder.abort)
    timer.daemon = True
    timer.start()
    try:
        report = folder.run(bounded, dry_run=True)
    except Exception:
        if folder.aborted:
            raise DryRunTimeout from None
        raise
    finally:
        timer.cancel()
    total = folder.message_count or 0
    return DryRun(report, max_messages if total > max_messages else None)


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
    store: AccountStore,
    subkey: SecretsSubkey | None,
    ssl_context: ssl.SSLContext,
    rule_set: RuleSet,
    filters: Sequence[Filter],
) -> dict[str, Any]:
    """The ``result`` of a dry run of ``rule_set``'s ``filters`` (``dry_run``). Raises
    ``RunRefused``: ``account_not_found``, ``invalid_spec``, ``dry_run_timeout``,
    ``match_timeout`` (a pattern took longer than imap-thingy's match timeout), a mailbox
    failure's code (``mailroom.accounts.mail.MailFailure``), or ``run_failed`` for anything else,
    whose text is neither kept nor logged (it may quote anything, mail included)."""
    try:
        return _dry_run_result(store, subkey, ssl_context, rule_set, filters)
    except RunRefused:
        raise
    except Exception as exc:
        name = type(exc).__name__
        logger.error("A dry run of rule set %s failed: %s.", rule_set.id, name)
        raise RunRefused("run_failed", name) from None


def _dry_run_result(
    store: AccountStore,
    subkey: SecretsSubkey | None,
    ssl_context: ssl.SSLContext,
    rule_set: RuleSet,
    filters: Sequence[Filter],
) -> dict[str, Any]:
    try:
        account = mailbox_account(store, subkey, ssl_context, rule_set.account_id)
    except AccountNotFound:
        raise RunRefused("account_not_found", "No such account") from None
    except MailFailure as failure:
        raise RunRefused(failure.code, failure.message) from None
    folder = RuleSetFolder(account, rule_set.folder)
    try:
        loaded = thingy_filters(rule_set, folder, filters)
    except InvalidSpecError as exc:
        raise RunRefused("invalid_spec", str(exc)) from None
    try:
        outcome = dry_run(folder, loaded)
    except DryRunTimeout:
        msg = "The dry run took too long: try a narrower filter or a smaller folder."
        raise RunRefused("dry_run_timeout", msg) from None
    except MatchTimeoutError:
        raise RunRefused("match_timeout", MATCH_TIMEOUT_MESSAGE) from None
    except Exception as exc:
        mailbox = mail_failure(exc, account.password)
        if mailbox is None:
            raise
        raise RunRefused(mailbox.code, mailbox.message) from None
    return {
        **labelled_report(outcome.report, filters, loaded),
        "limited_to_newest": outcome.limited_to_newest,
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
