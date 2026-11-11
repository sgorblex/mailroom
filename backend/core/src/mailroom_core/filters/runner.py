"""Run filter rules: one-shot over each folder, or continuously through IMAP IDLE."""

from __future__ import annotations

import logging
import signal
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

from imap_thingy.accounts import Folder
from imap_thingy.filters import Filter, IsStarred, Unstar
from imap_thingy.idle_monitor import IdleHandler, IdleMonitor, on_new_mail

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

    from imap_thingy.idle_monitor import IdleResponse
    from imap_thingy.report import FolderReport

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


def _new_mail_handler(
    folder: Folder, filters: list[Filter], dry_run: bool
) -> Callable[[IdleResponse], None]:
    def handler(responses: IdleResponse) -> None:
        if on_new_mail.triggers(responses):
            folder.run(filters, dry_run=dry_run)

    return handler


def monitor(
    rules: Rules,
    *,
    dry_run: bool,
    stop: threading.Event | None = None,
    install_signal_handlers: bool = True,
) -> None:
    """Run IDLE: apply each folder's filters on new mail, until stopped.

    Returns once ``stop`` is set (when given) or, with ``install_signal_handlers``, on
    SIGTERM or SIGINT; the monitors are then stopped and joined. Signal handlers can only be
    installed from the main thread, and the previous ones are restored on return. With
    ``install_signal_handlers=False`` it runs in any thread, stopped by setting ``stop``.
    """
    stop = stop if stop is not None else threading.Event()
    previous: dict[signal.Signals, Any] = {}
    monitors: list[IdleMonitor] = []
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
            mon = IdleMonitor(folder, IdleHandler(_new_mail_handler(folder, filters, dry_run)))
            mon.start()
            monitors.append(mon)
        stop.wait()
    finally:
        for m in monitors:
            m.stop()
        for m in monitors:
            m.join()
        for signum, handler in previous.items():
            # None: the previous handler was not installed from Python; fall back to the default.
            signal.signal(signum, handler if handler is not None else signal.SIG_DFL)
