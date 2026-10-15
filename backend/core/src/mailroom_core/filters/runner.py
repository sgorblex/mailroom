"""Run filter rules: one-shot over each folder, or continuously through IMAP IDLE."""

from __future__ import annotations

import logging
import signal
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from imap_thingy.accounts import Folder
from imap_thingy.filters import Filter, IsStarred, Unstar
from imap_thingy.idle_monitor import IdleHandler, IdleMonitor, on_new_mail

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

    from imap_thingy.idle_monitor import IdleResponse

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


def run_rules(rules: Rules, *, dry_run: bool, parallel: bool = False) -> None:
    """Run filters for each (folder, filters) group.

    Sequential by default. With parallel=True each folder runs on its own thread; every
    folder runs even if others fail, then a single RuntimeError lists the failures.
    """
    if not parallel:
        for folder, filters in rules:
            folder.run(filters, dry_run=dry_run)
        return
    if not rules:
        return
    failures: list[tuple[Folder, BaseException]] = []
    with ThreadPoolExecutor(max_workers=len(rules)) as executor:
        futures = {
            executor.submit(folder.run, filters, dry_run=dry_run): (folder, filters)
            for folder, filters in rules
        }
        for future in as_completed(futures):
            folder, _ = futures[future]
            try:
                future.result()
            except BaseException as exc:
                failures.append((folder, exc))
                log.exception("%s: rule run failed", folder)
    if failures:
        raise RuntimeError(
            f"{len(failures)} folder(s) failed: " + ", ".join(f"{f!s}: {e!s}" for f, e in failures)
        )


def _new_mail_handler(
    folder: Folder, filters: list[Filter], dry_run: bool
) -> Callable[[IdleResponse], None]:
    def handler(responses: IdleResponse) -> None:
        if on_new_mail.triggers(responses):
            folder.run(filters, dry_run=dry_run)

    return handler


def monitor(rules: Rules, *, dry_run: bool) -> None:
    """Run IDLE: apply each folder's filters on new mail, until SIGTERM or SIGINT."""
    monitors: list[IdleMonitor] = []
    for folder, filters in rules:
        mon = IdleMonitor(folder, IdleHandler(_new_mail_handler(folder, filters, dry_run)))
        mon.start()
        monitors.append(mon)

    def signal_handler(signum: int, frame: FrameType | None) -> None:
        log.info("Exiting gracefully...")
        for m in monitors:
            m.signal_handler(signum, frame)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    for m in monitors:
        m.join()
