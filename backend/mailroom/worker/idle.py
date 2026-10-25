"""New mail triggers: the worker watches, through IMAP IDLE, each folder that enabled ``new_mail``
rule sets run on, and queues their runs when it gets new mail.

``IdleSupervisor`` runs on a thread of the worker process. Every ``RECONCILE_SECONDS`` it works out
which folders to watch — the runnable ``new_mail`` rule sets'
(``mailroom.worker.tasks.runnable_rule_sets``): enabled, on an account whose Run filters is on —
and starts, stops or restarts one watch per (user, account, folder) to match: a watch whose
account changed (server, login, password) is restarted, and one that died (its mailbox
unreachable) is started again, backing off up to ``MAX_RETRY_SECONDS`` while it keeps failing.
Each watch is imap-thingy's stoppable ``monitor`` on its own thread. New mail does not run the
filters on the IDLE connection: it queues ``run_rule_set`` jobs, which run one at a time per
mailbox and record their reports like any other run. So does every (re)connection of a watch, for
the mail that arrived while the folder was not watched.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from imap_thingy.runner import monitor

from mailroom.accounts.mail import MailFailure, mail_failure
from mailroom.accounts.store import AccountNotFound
from mailroom.filters.imap import RuleSetFolder
from mailroom.models import RuleSet
from mailroom.worker.tasks import queue_new_mail_runs, runnable_rule_sets

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from imap_thingy.accounts import Account
    from imap_thingy.filters import Filter
    from imap_thingy.report import FolderReport
    from imap_thingy.runner import Rules
    from imapclient import IMAPClient
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.worker.context import Mailboxes

logger = logging.getLogger(__name__)

RECONCILE_SECONDS: Final = 30.0
"""How often the supervisor compares the watches with the rule sets."""
MAX_RETRY_SECONDS: Final = 30 * 60.0
"""The longest wait before starting again a watch that keeps failing: a dead watch starts again
at once, then after ``RECONCILE_SECONDS``, doubling each time it fails again."""


@dataclass(frozen=True, slots=True, order=True)
class WatchedFolder:
    """One folder of one user's account, watched for new mail."""

    user_id: int
    account_id: str
    folder: tuple[str, ...]


class Watch(Protocol):
    def alive(self) -> bool: ...

    def connected(self) -> bool:
        """Whether the watch ever reached its folder."""
        ...

    def failure(self) -> str | None:
        """Why the watch ended on its own (``code: message`` or an exception's type name)."""
        ...

    def stop(self) -> None:
        """Ask the watch to stop; ``join`` waits for it."""

    def join(self) -> None: ...


def watched_folders(db: Session) -> set[WatchedFolder]:
    """The folders the runnable ``new_mail`` rule sets run on."""
    rows = db.execute(
        runnable_rule_sets("new_mail")
        .with_only_columns(RuleSet.user_id, RuleSet.account_id, RuleSet.folder)
        .distinct()
    )
    return {WatchedFolder(user, account, tuple(folder)) for user, account, folder in rows}


def account_fingerprint(account: Account) -> str:
    """Changes when the account's server, login or password does: its watch then restarts."""
    parts = (account.host, str(account.port), account.username, account.password)
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Changes:
    stop: frozenset[WatchedFolder]
    start: frozenset[WatchedFolder]


def watch_changes(
    running: Mapping[WatchedFolder, tuple[str, bool]], wanted: Mapping[WatchedFolder, str]
) -> Changes:
    """What to stop and start so the running watches (fingerprint, alive) become the wanted ones
    (fingerprint): stop the unwanted, the dead and the changed; start the wanted that are then not
    running."""
    stop = frozenset(
        folder
        for folder, (fingerprint, alive) in running.items()
        if not alive or wanted.get(folder) != fingerprint
    )
    start = frozenset(folder for folder in wanted if folder not in running or folder in stop)
    return Changes(stop=stop, start=start)


class _NewMailFolder(RuleSetFolder):
    """The watched folder as imap-thingy's ``monitor`` takes it. ``monitor`` runs a folder's
    filters on new mail: here that run is ``on_new_mail``, which queues the real runs."""

    def __init__(
        self, account: Account, segments: Sequence[str], on_new_mail: Callable[[], None]
    ) -> None:
        super().__init__(account, segments)
        self._on_new_mail = on_new_mail

    def connect(self, readonly: bool = False) -> IMAPClient:
        conn = super().connect(readonly=readonly)
        # Never aborted: a watch lives for months and reconnects many times.
        self.untrack(conn)
        # Mail may have arrived while the folder was not watched.
        self._on_new_mail()
        return conn

    def run(
        self,
        filter_or_filters: Filter | Iterable[Filter],
        dry_run: bool = False,
    ) -> FolderReport:
        self._on_new_mail()
        return self.new_report(dry_run)


class MonitorWatch:
    """imap-thingy's ``monitor`` on one folder, on its own thread, until stopped. The thread ends
    early when the first connection fails; the supervisor then starts the watch again."""

    def __init__(
        self, folder: WatchedFolder, account: Account, on_new_mail: Callable[[], None]
    ) -> None:
        self._password = account.password
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._failure: str | None = None

        def on_connection_or_mail() -> None:
            self._connected.set()
            on_new_mail()

        rules: Rules = [(_NewMailFolder(account, folder.folder, on_connection_or_mail), [])]
        name = f"idle:{folder.user_id}:{folder.account_id}:{'/'.join(folder.folder)}"
        self._thread = threading.Thread(target=self._watch, args=(rules,), name=name)
        self._thread.start()

    def _watch(self, rules: Rules) -> None:
        try:
            monitor(rules, dry_run=False, stop=self._stop, install_signal_handlers=False)
        except Exception as exc:
            failure = mail_failure(exc, self._password)
            # The supervisor logs the reason; the traceback only at debug, as its text may quote
            # anything the server said.
            logger.debug("The watch ended.", exc_info=failure is None)
            self._failure = f"{failure.code}: {failure.message}" if failure else type(exc).__name__
        else:
            if not self._stop.is_set():
                self._failure = "ended"

    def alive(self) -> bool:
        return self._thread.is_alive()

    def connected(self) -> bool:
        return self._connected.is_set()

    def failure(self) -> str | None:
        return self._failure

    def stop(self) -> None:
        self._stop.set()

    def join(self) -> None:
        self._thread.join()


@dataclass(frozen=True, slots=True)
class _Retry:
    """A folder whose watch died: when to start it again, and why it died."""

    fingerprint: str
    failures: int
    after: float
    reason: str


class IdleSupervisor:
    """Keeps one watch per folder of ``watched_folders``, until ``run``'s ``stop`` is set."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        mailboxes: Mailboxes,
        *,
        start_watch: Callable[[WatchedFolder, Account, Callable[[], None]], Watch] = MonitorWatch,
        interval: float = RECONCILE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessions = sessions
        self._mailboxes = mailboxes
        self._start_watch = start_watch
        self._interval = interval
        self._watches: dict[WatchedFolder, tuple[str, Watch]] = {}
        self._problems: dict[WatchedFolder, str] = {}
        self._retries: dict[WatchedFolder, _Retry] = {}
        self._clock = clock

    @property
    def watched(self) -> frozenset[WatchedFolder]:
        return frozenset(self._watches)

    def run(self, stop: threading.Event) -> None:
        """Reconcile now and every ``interval`` until ``stop`` is set; then stop every watch."""
        logger.info("Watching folders for new mail.")
        try:
            while not stop.is_set():
                try:
                    self.reconcile()
                except Exception:
                    logger.exception("Could not compare the watched folders with the rule sets.")
                stop.wait(self._interval)
        finally:
            self.stop_all()
            logger.info("Stopped watching folders.")

    def reconcile(self) -> None:
        """Start, stop and restart watches to match the rule sets."""
        with self._sessions() as db:
            folders = watched_folders(db)
        accounts = self._accounts(folders)
        wanted = {folder: account_fingerprint(account) for folder, account in accounts.items()}
        now = self._clock()
        self._note_failures(now)
        running = {
            folder: (fingerprint, watch.alive())
            for folder, (fingerprint, watch) in self._watches.items()
        }
        changes = watch_changes(running, wanted)
        stopped = [self._watches.pop(folder)[1] for folder in sorted(changes.stop)]
        for watch in stopped:
            watch.stop()
        for watch in stopped:
            watch.join()
        self._retries = {
            folder: retry
            for folder, retry in self._retries.items()
            if wanted.get(folder) == retry.fingerprint
        }
        started = 0
        for folder in sorted(changes.start):
            retry = self._retries.get(folder)
            if retry is not None and now < retry.after:
                continue
            watch = self._start_watch(folder, accounts[folder], self._queue_runs_of(folder))
            self._watches[folder] = (wanted[folder], watch)
            started += 1
        if changes.stop or started:
            logger.info(
                "Watching %d folder(s): %d stopped, %d started.",
                len(self._watches),
                len(changes.stop),
                started,
            )

    def _note_failures(self, now: float) -> None:
        """Remember each dead watch's reason and when to try again; forget a folder whose watch
        reached it. A reason is logged when it first comes, not on every retry."""
        for folder, (fingerprint, watch) in self._watches.items():
            if watch.connected():
                self._retries.pop(folder, None)
            if watch.alive():
                continue
            reason = watch.failure() or "ended"
            previous = self._retries.get(folder)
            if previous is not None and previous.fingerprint != fingerprint:
                previous = None
            failures = 1 if previous is None else previous.failures + 1
            # Started again at once the first time, then after 1, 2, 4… intervals.
            backoff = self._interval * 2 ** (failures - 2)
            wait = 0 if failures == 1 else min(backoff, MAX_RETRY_SECONDS)
            self._retries[folder] = _Retry(fingerprint, failures, now + wait, reason)
            if previous is None or previous.reason != reason:
                logger.warning(
                    "Could not watch %s: %s",
                    "/".join(folder.folder),
                    reason,
                    extra={"user_id": folder.user_id, "account_id": folder.account_id},
                )

    def stop_all(self) -> None:
        watches = [watch for _fingerprint, watch in self._watches.values()]
        self._watches.clear()
        for watch in watches:
            watch.stop()
        for watch in watches:
            watch.join()

    def _accounts(self, folders: Iterable[WatchedFolder]) -> dict[WatchedFolder, Account]:
        """The account of each folder that can be watched; the others are logged once per
        reason."""
        accounts: dict[WatchedFolder, Account] = {}
        problems: dict[WatchedFolder, str] = {}
        for folder in folders:
            try:
                accounts[folder] = self._mailboxes.account(folder.user_id, folder.account_id)
            except AccountNotFound:
                problems[folder] = "account_not_found"
            except MailFailure as failure:
                problems[folder] = failure.code
        for folder, problem in problems.items():
            if self._problems.get(folder) != problem:
                logger.warning(
                    "Cannot watch %s: %s",
                    "/".join(folder.folder),
                    problem,
                    extra={"user_id": folder.user_id, "account_id": folder.account_id},
                )
        self._problems = problems
        return accounts

    def _queue_runs_of(self, folder: WatchedFolder) -> Callable[[], None]:
        def queue_runs() -> None:
            try:
                with self._sessions() as db:
                    queue_new_mail_runs(db, folder.user_id, folder.account_id, folder.folder)
                    db.commit()
            except Exception:
                logger.exception(
                    "Could not queue the runs of new mail.",
                    extra={"user_id": folder.user_id, "account_id": folder.account_id},
                )

        return queue_runs
