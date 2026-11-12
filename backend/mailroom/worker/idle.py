"""New mail triggers: the worker watches, through IMAP IDLE, each folder that enabled ``new_mail``
rule sets run on, and queues their runs when it gets new mail.

``IdleSupervisor`` runs on a thread of the worker process. Every ``RECONCILE_SECONDS`` it works out
which folders to watch — the runnable ``new_mail`` rule sets'
(``mailroom.worker.tasks.runnable_rule_sets``): enabled, on an account whose Run filters is on —
and starts, stops or restarts one watch per (user, account, folder) to match: a watch whose
account changed (server, port, TLS mode, login, password) is restarted, and one that died (its
mailbox unreachable) is started again, backing off up to ``MAX_RETRY_SECONDS`` while it keeps
failing.

Each watch is mailroom-core's ``IdleWatch`` on the user's connections from the worker's pool
(``Mailboxes.lease``, held only to look the folder's name up and build the watch), on a
connection of its own outside the pool's. Accounts on STARTTLS are not watched yet: their runs
still go through the filter engine, which refuses them. New mail does not run the filters on the
IDLE connection: it queues ``run_rule_set`` jobs, which run one at a time per mailbox and record
their reports like any other run. So does every (re)connection of a watch, for the mail that
arrived while the folder was not watched.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from mailroom.accounts.store import AccountNotFound, account_password, load_accounts, user_store
from mailroom.errors import SECRETS_KEY_MISSING
from mailroom.mail.connections import PASSWORD_MISSING, PASSWORD_MISSING_TEXT, PasswordMissing
from mailroom.models import RuleSet
from mailroom.worker.tasks import queue_new_mail_runs, runnable_rule_sets
from mailroom_core.mail import IdleWatch, MailError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.worker.context import Mailboxes
    from mailroom_core.config import AccountConfig
    from mailroom_core.mail import IdleChange

logger = logging.getLogger(__name__)

RECONCILE_SECONDS: Final = 30.0
"""How often the supervisor compares the watches with the rule sets."""
MAX_RETRY_SECONDS: Final = 30 * 60.0
"""The longest wait before starting again a watch that keeps failing: a dead watch starts again
at once, then after ``RECONCILE_SECONDS``, doubling each time it fails again."""
STOP_SECONDS: Final = 10.0
"""How long the supervisor waits, in all, for the watches it stops at once. A watch stops within
about a second of idling, and logs out within 5; one still opening its connection (bounded by
the connect and read timeouts) is left to end on its own: it acts on nothing once stopped."""


@dataclass(frozen=True, slots=True, order=True)
class WatchedFolder:
    """One folder of one user's account, watched for new mail."""

    user_id: int
    account_id: str
    folder: tuple[str, ...]


class Watch(Protocol):
    """A running watch, as ``IdleWatch`` reports itself, its failure as text."""

    @property
    def alive(self) -> bool: ...

    @property
    def connected(self) -> bool:
        """Whether the watch ever reached its folder."""
        ...

    @property
    def failure(self) -> str | None:
        """Why the watch ended on its own (``code: message`` or an exception's type name)."""
        ...

    def stop(self) -> None:
        """Ask the watch to stop; ``join`` waits for it."""

    def join(self, timeout: float | None = None) -> None: ...


def watched_folders(db: Session) -> set[WatchedFolder]:
    """The folders the runnable ``new_mail`` rule sets run on."""
    rows = db.execute(
        runnable_rule_sets("new_mail")
        .with_only_columns(RuleSet.user_id, RuleSet.account_id, RuleSet.folder)
        .distinct()
    )
    return {WatchedFolder(user, account, tuple(folder)) for user, account, folder in rows}


def account_fingerprint(account: AccountConfig, password: str) -> str:
    """Changes when the account's IMAP server (host, port, TLS mode), login or password does: its
    watch then restarts."""
    imap = account.imap
    parts = (imap.host, str(imap.port), imap.tls, account.login, password)
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


def folder_path(segments: Sequence[str], delimiter: str | None) -> str:
    """A rule set's folder (path segments) as the server names it: joined with its hierarchy
    delimiter, or with ``.`` (the filter engine's default) when it reports none."""
    return (delimiter or ".").join(segments)


def _failure_text(error: MailError) -> str:
    return f"{error.code}: {error.message}"


class FolderWatch:
    """One folder's ``IdleWatch``, on the user's connections from the worker's pool. Its thread
    leases them only to look the server's delimiter up (on a connection of the pool) and build the
    ``IdleWatch``, so the pool can log that connection out once unused; then it starts the watch
    and waits for it to end. The watch keeps the connections, which open its own connection, outside
    the pool, even once the pool has closed them. ``on_new_mail`` runs on every (re)connect and
    whenever the folder announces its size (new mail)."""

    def __init__(
        self,
        mailboxes: Mailboxes,
        folder: WatchedFolder,
        revision: int,
        on_new_mail: Callable[[], None],
    ) -> None:
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._idle: IdleWatch | None = None
        self._failure: str | None = None
        name = f"idle:{folder.user_id}:{folder.account_id}:{'/'.join(folder.folder)}"
        self._name = name
        self._thread = threading.Thread(
            target=self._run,
            args=(mailboxes, folder, revision, on_new_mail),
            name=f"{name}:lease",
            daemon=True,
        )
        self._thread.start()

    def _run(
        self,
        mailboxes: Mailboxes,
        folder: WatchedFolder,
        revision: int,
        on_new_mail: Callable[[], None],
    ) -> None:
        def on_change(change: IdleChange) -> None:
            if change.new_mail:
                on_new_mail()

        try:
            with mailboxes.lease(folder.user_id, revision) as connections:
                path = folder_path(folder.folder, connections.delimiter(folder.account_id))
                idle = IdleWatch(
                    connections,
                    folder.account_id,
                    path,
                    on_change=on_change,
                    on_connect=on_new_mail,
                    name=self._name,
                )
            with self._lock:
                if self._stop.is_set():
                    return
                self._idle = idle.start()
            idle.join()
            if idle.failure is not None:
                self._failure = _failure_text(idle.failure)
            elif not self._stop.is_set():
                self._failure = "ended"
        except MailError as error:
            if not self._stop.is_set():
                self._failure = _failure_text(error)
        except PasswordMissing:
            if not self._stop.is_set():
                self._failure = f"{PASSWORD_MISSING}: {PASSWORD_MISSING_TEXT}"
        except Exception as error:
            # The supervisor logs the reason; the traceback only at debug, as its text may quote
            # anything the server said.
            logger.debug("The watch ended.", exc_info=error)
            if not self._stop.is_set():
                self._failure = type(error).__name__

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def connected(self) -> bool:
        with self._lock:
            idle = self._idle
        return idle is not None and idle.connected

    @property
    def failure(self) -> str | None:
        return self._failure

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            idle = self._idle
        if idle is not None:
            idle.stop()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)


@dataclass(frozen=True, slots=True)
class _Target:
    """What a folder's watch is started with: its account's fingerprint, and the revision of the
    user's settings it was read at (for the lease)."""

    fingerprint: str
    revision: int


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
        start_watch: Callable[[WatchedFolder, int, Callable[[], None]], Watch] | None = None,
        interval: float = RECONCILE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        stop_seconds: float = STOP_SECONDS,
    ) -> None:
        """``start_watch(folder, revision, on_new_mail)`` starts a folder's watch (``FolderWatch``
        on ``mailboxes`` by default); ``revision`` is the user's settings' when the account was
        read."""
        self._sessions = sessions
        self._mailboxes = mailboxes
        self._start_watch = start_watch or self._folder_watch
        self._stop_seconds = stop_seconds
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
        targets = self._targets(folders)
        wanted = {folder: target.fingerprint for folder, target in targets.items()}
        now = self._clock()
        self._note_failures(now)
        running = {
            folder: (fingerprint, watch.alive)
            for folder, (fingerprint, watch) in self._watches.items()
        }
        changes = watch_changes(running, wanted)
        self._stop_watches([self._watches.pop(folder)[1] for folder in sorted(changes.stop)])
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
            revision = targets[folder].revision
            watch = self._start_watch(folder, revision, self._queue_runs_of(folder))
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
            if watch.connected:
                self._retries.pop(folder, None)
            if watch.alive:
                continue
            reason = watch.failure or "ended"
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
        self._stop_watches(watches)

    def _stop_watches(self, watches: Sequence[Watch]) -> None:
        """Stop ``watches`` and wait for them, ``stop_seconds`` at most in all."""
        for watch in watches:
            watch.stop()
        until = time.monotonic() + self._stop_seconds
        for watch in watches:
            watch.join(max(0.0, until - time.monotonic()))
        left = sum(1 for watch in watches if watch.alive)
        if left:
            logger.info("%d stopped watch(es) still ending on their own.", left)

    def _folder_watch(
        self, folder: WatchedFolder, revision: int, on_new_mail: Callable[[], None]
    ) -> Watch:
        return FolderWatch(self._mailboxes, folder, revision, on_new_mail)

    def _targets(self, folders: Iterable[WatchedFolder]) -> dict[WatchedFolder, _Target]:
        """What each folder that can be watched is watched with; the others are logged once per
        reason."""
        accounts: dict[tuple[int, str], _Target | str] = {}
        targets: dict[WatchedFolder, _Target] = {}
        problems: dict[WatchedFolder, str] = {}
        for folder in folders:
            key = (folder.user_id, folder.account_id)
            if key not in accounts:
                accounts[key] = self._target(*key)
            found = accounts[key]
            if isinstance(found, _Target):
                targets[folder] = found
            else:
                problems[folder] = found
        for folder, problem in problems.items():
            if self._problems.get(folder) != problem:
                logger.warning(
                    "Cannot watch %s: %s",
                    "/".join(folder.folder),
                    problem,
                    extra={"user_id": folder.user_id, "account_id": folder.account_id},
                )
        self._problems = problems
        return targets

    def _target(self, user_id: int, account_id: str) -> _Target | str:
        """The account's target, or why it cannot be watched before any connection: the app's
        code (``secrets_key_missing``, ``password_missing``), ``starttls_unsupported`` or
        ``account_not_found``. One read of the store, one opening of the password."""
        store = user_store(self._mailboxes.engine, user_id)
        subkey = self._mailboxes.subkey
        try:
            loaded = load_accounts(store)
            account = loaded.account(account_id)
        except AccountNotFound:
            return "account_not_found"
        if subkey is None:
            return SECRETS_KEY_MISSING
        password = account_password(store, subkey, account_id)
        if password is None:
            return PASSWORD_MISSING
        if account.imap.tls != "implicit":
            # The runs a watch queues still go through the filter engine, which refuses STARTTLS.
            return "starttls_unsupported"
        return _Target(account_fingerprint(account, password), loaded.revision)

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
