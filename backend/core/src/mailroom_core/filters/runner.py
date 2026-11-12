"""Run filter rules: one-shot over each folder, or continuously through IMAP IDLE."""

from __future__ import annotations

import logging
import signal
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

from mailroom_core.filters.account import Folder
from mailroom_core.filters.actions.flags import Unstar
from mailroom_core.filters.criteria.flags import IsStarred
from mailroom_core.filters.filter import Filter
from mailroom_core.mail import IdleWatch, MailError

if TYPE_CHECKING:
    from types import FrameType

    from mailroom_core.filters.report import FolderReport
    from mailroom_core.mail import IdleChange

Rules = list[tuple[Folder, list[Filter]]]
"""Filters grouped by the folder they run on, in order."""

log = logging.getLogger(__name__)


def starred_only(rules: Rules) -> Rules:
    """Wrap sort rules: only starred messages, unstar before applying each action."""
    out: Rules = []
    for folder, filters in rules:
        wrapped = [Filter(IsStarred() & f.criterion, Unstar() + f.action) for f in filters]
        out.append((folder, wrapped))
    return out


class RunRulesError(RuntimeError):
    """Raised by ``run_rules(parallel=True)`` when folders failed; carries every folder's report.

    ``reports`` has one entry per rule group, in order: failed folders have ``error`` set and
    the reports of the filters that finished before the failure.
    """

    def __init__(self, message: str, reports: list[FolderReport]) -> None:
        """Build the error from its message and the run's reports."""
        super().__init__(message)
        self.reports = reports


def run_rules(rules: Rules, *, dry_run: bool, parallel: bool = False) -> list[FolderReport]:
    """Run filters for each (folder, filters) group and return each folder's report, in order.

    Sequential by default: the first failure propagates as raised by ``Folder.run``. With
    parallel=True each folder runs on its own thread; every folder runs even if others fail,
    then a single :class:`RunRulesError` (a RuntimeError) lists the failures and carries all
    the reports, the failed folders' with ``error`` set.

    A run holds one of its account's connections throughout: in parallel, folders of one
    account run only as many at once as the connections' ``pool_size``, the others waiting up
    to ``Timeouts.lock`` and then failing ``busy``. Give the connections a pool as large as the
    most folders of one account in ``rules``.
    """
    if not parallel:
        return [folder.run(filters, dry_run=dry_run) for folder, filters in rules]
    if not rules:
        return []
    reports = [folder.new_report(dry_run) for folder, _ in rules]
    failures: list[tuple[Folder, BaseException]] = []
    with ThreadPoolExecutor(max_workers=len(rules)) as executor:
        futures = {
            executor.submit(folder.run_into, report, filters, dry_run=dry_run): (folder, report)
            for (folder, filters), report in zip(rules, reports, strict=True)
        }
        for future in as_completed(futures):
            folder, report = futures[future]
            try:
                future.result()
            except BaseException as exc:
                failures.append((folder, exc))
                report["error"] = str(exc) or type(exc).__name__
                log.exception("%s: rule run failed", folder)
    if failures:
        raise RunRulesError(
            f"{len(failures)} folder(s) failed: " + ", ".join(f"{f!s}: {e!s}" for f, e in failures),
            reports,
        )
    return reports


def _watch(folder: Folder, filters: list[Filter], dry_run: bool) -> IdleWatch:
    """An IdleWatch on `folder` running its filters on every connect and on new mail, on a
    connection of the pool (never the watch's own); a run's failure is logged by the watch."""
    account = folder.account

    def run() -> None:
        folder.run(filters, dry_run=dry_run)

    def on_change(change: IdleChange) -> None:
        if change.new_mail:
            run()

    return IdleWatch(
        account.connections,
        account.account_id,
        folder.imap_name(),
        on_change=on_change,
        on_connect=run,
        name=f"filters:{folder}",
    )


def monitor(
    rules: Rules,
    *,
    dry_run: bool,
    stop: threading.Event | None = None,
    install_signal_handlers: bool = True,
) -> None:
    """Watch each folder through IDLE (`mailroom_core.mail.IdleWatch`) and run its filters when
    the watch connects and on new mail, until stopped.

    Each watch holds a connection of its own; the runs take one of the account's pooled
    connections, so a watch's runs wait for any other run on that account. A watch that ends
    on its own (its first connect failed, the login was refused, the folder is missing) is
    logged and not started again; so is a folder whose name cannot be looked up (the account
    unreachable at the start, say), and the other folders' watches keep running.

    Returns once ``stop`` is set (when given) or, with ``install_signal_handlers``, on
    SIGTERM or SIGINT; the watches are then stopped and joined. Signal handlers can only be
    installed from the main thread, and the previous ones are restored on return. With
    ``install_signal_handlers=False`` it runs in any thread, stopped by setting ``stop``.
    """
    stop = stop if stop is not None else threading.Event()
    previous: dict[signal.Signals, Any] = {}
    watches: list[IdleWatch] = []
    try:
        if install_signal_handlers:

            def signal_handler(signum: int, frame: FrameType | None) -> None:
                log.info("Exiting gracefully...")
                stop.set()

            for signum in (signal.SIGTERM, signal.SIGINT):
                previous[signum] = signal.signal(signum, signal_handler)
        for folder, filters in rules:
            if stop.is_set():
                break
            try:
                watch = _watch(folder, filters, dry_run)
            except MailError as error:
                log.warning("Not watching %s: %s.", folder, error.code)
                continue
            watches.append(watch.start())
        stop.wait()
    finally:
        for watch in watches:
            watch.stop()
        for watch in watches:
            watch.join()
            if watch.failure is not None:
                log.warning("a watch ended: %s", watch.failure.code)
        for signum, handler in previous.items():
            # None: the previous handler was not installed from Python; fall back to the default.
            signal.signal(signum, handler if handler is not None else signal.SIG_DFL)
