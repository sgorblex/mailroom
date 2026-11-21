"""The worker's IMAP IDLE watches: new mail runs the ``new_mail`` rule sets, and any change to an
indexed folder (new mail, a flag, an expunge) syncs its index.

``IdleSupervisor`` runs on a thread of the worker process. It works out which folders to watch
(``wanted_folders``): with filters, each folder the runnable ``new_mail`` rule sets run on
(``mailroom.worker.tasks.runnable_rule_sets``: enabled, on an account whose Run filters is on);
with the mail client, at most ``INDEX_WATCHES_PER_ACCOUNT`` indexed folders per account: INBOX
when it is on the index (on Gmail, where All Mail is the account's only row, All Mail), and the
indexed folder a browser viewed last (``app.mail_view_interest``, until its expiry). A folder
wanted for both gets one watch, which does both. It reconciles every ``RECONCILE_SECONDS``, and at
once when the API announces a viewing interest on ``INTEREST_CHANNEL`` (a connection of its own
LISTENs): it starts, stops or restarts one watch per (user, account, folder) to match. A watch
whose account changed (server, port, TLS mode, login, password) is restarted, and one that died
(its mailbox unreachable) is started again, backing off up to ``MAX_RETRY_SECONDS`` while it
keeps failing.

Each watch is mailroom-core's ``IdleWatch`` on the user's connections from the worker's pool
(``Mailboxes.lease``, held only to look the server's delimiter up and build the watch), on a
connection of its own outside the pool's, implicit TLS or STARTTLS. A rule set names its folder by
segments: the supervisor learns each account's delimiter from its watches, so a folder of several
segments shares the index's watch once it is known. The IDLE connection never acts: new mail
queues ``run_rule_set`` jobs, and any change a ``sync_folder`` job (one waiting per folder), which
run one at a time per mailbox. So does every (re)connection of a watch, for what changed while the
folder was not watched.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Protocol

from sqlalchemy import and_, select

from mailroom.accounts.store import AccountNotFound, account_password, load_accounts, user_store
from mailroom.errors import SECRETS_KEY_MISSING
from mailroom.mail.connections import PASSWORD_MISSING, PASSWORD_MISSING_TEXT, PasswordMissing
from mailroom.mail.index import INTEREST_CHANNEL, prune_interest
from mailroom.models import AccountFilterSettings, MailIndexFolder, MailViewInterest, RuleSet
from mailroom.worker.tasks import queue_folder_sync, queue_new_mail_runs, runnable_rule_sets
from mailroom_core.mail import IdleWatch, MailError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    import psycopg
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.worker.context import Mailboxes
    from mailroom_core.config import AccountConfig
    from mailroom_core.mail import IdleChange

logger = logging.getLogger(__name__)

RECONCILE_SECONDS: Final = 30.0
"""How often the supervisor compares the watches with what is wanted, besides each viewing
interest announced."""
MAX_RETRY_SECONDS: Final = 30 * 60.0
"""The longest wait before starting again a watch that keeps failing: a dead watch starts again
at once, then after ``RECONCILE_SECONDS``, doubling each time it fails again."""
STOP_SECONDS: Final = 10.0
"""How long the supervisor waits, in all, for the watches it stops at once. A watch stops within
about a second of idling, and logs out within 5; one still opening its connection (bounded by
the connect and read timeouts) is left to end on its own: it acts on nothing once stopped."""
INDEX_WATCHES_PER_ACCOUNT: Final = 2
"""The index's watches on one account: its INBOX (or Gmail's All Mail) and the folder viewed last.
Each is a connection of its own, besides the worker's pool and the rule sets' watches."""
LISTEN_POLL: Final = 1.0
"""Seconds between the interest listener's checks of the stop request, and the supervisor's
while it waits."""


@dataclass(frozen=True, slots=True, order=True)
class WatchedFolder:
    """One folder of one user's account, watched through IDLE."""

    user_id: int
    account_id: int
    path: str
    """As the server names it; empty while ``segments`` waits for the account's delimiter."""
    segments: tuple[str, ...] = ()
    """A rule set's folder of several segments, on an account whose delimiter the supervisor has
    not learnt yet: the watch joins them with the one the server reports."""

    @property
    def name(self) -> str:
        return self.path or "/".join(self.segments)


@dataclass(frozen=True, slots=True)
class Reactions:
    """What a watched folder's events do."""

    rule_folder: tuple[str, ...] | None = None
    """The folder of runnable ``new_mail`` rule sets, as they name it: new mail queues their
    runs."""
    index_folder: int | None = None
    """The indexed folder's id (``app.mail_index_folder``): any change queues its sync."""


type _Account = tuple[int, int]
"""A user's account: (user id, account id)."""


@dataclass(frozen=True, slots=True)
class Wanted:
    """The folders to watch, before a rule set's folder is named as the server names it."""

    rules: frozenset[tuple[int, int, tuple[str, ...]]] = frozenset()
    """(user, account, segments) of the runnable ``new_mail`` rule sets."""
    index: Mapping[tuple[int, int, str], int] = field(default_factory=dict)
    """(user, account, path) of the indexed folders to watch, with their ids."""

    def accounts(self) -> set[_Account]:
        return {(user, account) for user, account, _ in self.rules} | {
            (user, account) for user, account, _ in self.index
        }

    def folders(self, delimiters: Mapping[_Account, str | None]) -> dict[WatchedFolder, Reactions]:
        """One entry per watch, a rule set's folder named with its account's delimiter when
        ``delimiters`` has it (a folder of one segment needs none): the same folder wanted by the
        index and a rule set is one watch, doing both."""
        wanted = {
            WatchedFolder(user, account, path): Reactions(index_folder=folder_id)
            for (user, account, path), folder_id in self.index.items()
        }
        for user, account, segments in sorted(self.rules):
            if len(segments) == 1:
                folder = WatchedFolder(user, account, segments[0])
            elif (user, account) in delimiters:
                path = folder_path(segments, delimiters[user, account])
                folder = WatchedFolder(user, account, path)
            else:
                folder = WatchedFolder(user, account, "", segments)
            wanted[folder] = replace(wanted.get(folder, Reactions()), rule_folder=segments)
        return wanted


def rule_set_folders(db: Session) -> frozenset[tuple[int, int, tuple[str, ...]]]:
    """The folders the runnable ``new_mail`` rule sets run on, an all-accounts one's INBOX on each
    account it runs on: (user, account, segments)."""
    rows = db.execute(
        runnable_rule_sets("new_mail")
        .with_only_columns(RuleSet.user_id, AccountFilterSettings.account_id, RuleSet.folder)
        .distinct()
    )
    return frozenset((user, account, tuple(folder)) for user, account, folder in rows)


def index_folders(db: Session, now: datetime) -> dict[tuple[int, int, str], int]:
    """The indexed folders to watch, at most ``INDEX_WATCHES_PER_ACCOUNT`` per account, with their
    ids: INBOX when it is on the index (an account with no INBOX row is Gmail's, whose only row is
    All Mail: that one), and the indexed folder a browser viewed last whose interest lasts past
    ``now`` (a Gmail label's is All Mail's; a folder off the index has nothing to keep fresh)."""
    rows: dict[_Account, list[tuple[str, int, bool]]] = defaultdict(list)
    query = select(
        MailIndexFolder.user_id,
        MailIndexFolder.account_id,
        MailIndexFolder.path,
        MailIndexFolder.id,
        MailIndexFolder.enabled,
    ).order_by(MailIndexFolder.id)
    for user, account, path, folder_id, enabled in db.execute(query):
        rows[user, account].append((path, folder_id, enabled))
    wanted: dict[tuple[int, int, str], int] = {}
    for (user, account), folders in rows.items():
        inbox = [folder for folder in folders if folder[0] == "INBOX"]
        main = inbox or (folders if len(folders) == 1 else [])
        for path, folder_id, enabled in main:
            if enabled:
                wanted[user, account, path] = folder_id
    viewed = db.execute(
        select(
            MailIndexFolder.user_id,
            MailIndexFolder.account_id,
            MailIndexFolder.path,
            MailIndexFolder.id,
        )
        .join(
            MailViewInterest,
            and_(
                MailViewInterest.user_id == MailIndexFolder.user_id,
                MailViewInterest.account_id == MailIndexFolder.account_id,
                MailViewInterest.folder == MailIndexFolder.path,
            ),
        )
        .where(MailIndexFolder.enabled, MailViewInterest.expires_at > now)
        .order_by(MailViewInterest.expires_at.desc(), MailIndexFolder.path)
    )
    added: set[_Account] = set()
    for user, account, path, folder_id in viewed:
        if (user, account) in added:
            continue
        added.add((user, account))
        wanted[user, account, path] = folder_id
    return wanted


def wanted_folders(db: Session, now: datetime, *, rules: bool, index: bool) -> Wanted:
    """What to watch at ``now``: the rule sets' folders with ``rules`` (filters), the index's with
    ``index`` (the mail client)."""
    return Wanted(
        rules=rule_set_folders(db) if rules else frozenset(),
        index=index_folders(db, now) if index else {},
    )


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


class Reactor(Protocol):
    """What a watch reports, on its own thread."""

    def connected(self) -> None:
        """The watch (re)connected: anything may have changed while it was away."""

    def changed(self, change: IdleChange) -> None:
        """The server announced something about the folder."""

    def delimiter(self, delimiter: str | None) -> None:
        """The account's hierarchy delimiter, as the watch looked it up."""


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
    the pool, even once the pool has closed them. ``reactor`` hears the delimiter, every
    (re)connect and every change the folder announces."""

    def __init__(
        self, mailboxes: Mailboxes, folder: WatchedFolder, revision: int, reactor: Reactor
    ) -> None:
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._idle: IdleWatch | None = None
        self._failure: str | None = None
        name = f"idle:{folder.user_id}:{folder.account_id}:{folder.name}"
        self._name = name
        self._thread = threading.Thread(
            target=self._run,
            args=(mailboxes, folder, revision, reactor),
            name=f"{name}:lease",
            daemon=True,
        )
        self._thread.start()

    def _run(
        self, mailboxes: Mailboxes, folder: WatchedFolder, revision: int, reactor: Reactor
    ) -> None:
        try:
            with mailboxes.lease(folder.user_id, revision) as connections:
                delimiter = connections.delimiter(folder.account_id)
                reactor.delimiter(delimiter)
                path = folder.path or folder_path(folder.segments, delimiter)
                idle = IdleWatch(
                    connections,
                    folder.account_id,
                    path,
                    on_change=reactor.changed,
                    on_connect=reactor.connected,
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
    """What an account's watches are started with: its fingerprint, and the revision of the
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


class _FolderReactor:
    """A watch's ``Reactor``: queues what the folder's reactions say, as the supervisor last set
    them (a rule set added to a watched folder needs no restart)."""

    def __init__(
        self,
        supervisor: IdleSupervisor,
        folder: WatchedFolder,
        fingerprint: str,
        reactions: Reactions,
    ) -> None:
        self._supervisor = supervisor
        self._folder = folder
        self._fingerprint = fingerprint
        self.reactions = reactions

    def connected(self) -> None:
        self._react(new_mail=True, changed=True)

    def changed(self, change: IdleChange) -> None:
        moved = change.new_mail or bool(change.expunged) or _fetched(change)
        self._react(new_mail=change.new_mail, changed=moved)

    def delimiter(self, delimiter: str | None) -> None:
        self._supervisor.learn_delimiter(self._folder, self._fingerprint, delimiter)

    def _react(self, *, new_mail: bool, changed: bool) -> None:
        reactions, folder = self.reactions, self._folder
        rule_folder = reactions.rule_folder if new_mail else None
        index_folder = reactions.index_folder if changed else None
        if rule_folder is None and index_folder is None:
            return
        try:
            with self._supervisor.sessions() as db:
                if rule_folder is not None:
                    queue_new_mail_runs(db, folder.user_id, folder.account_id, rule_folder)
                if index_folder is not None:
                    queue_folder_sync(db, folder.user_id, folder.account_id, index_folder)
                db.commit()
        except Exception:
            logger.exception(
                "Could not queue what the folder's change asks.",
                extra={"user_id": folder.user_id, "account_id": folder.account_id},
            )


def _fetched(change: IdleChange) -> bool:
    """Whether a FETCH came, whatever it carried: FLAGS, or only a MODSEQ or Gmail's labels."""
    return bool(change.flags) or any(
        len(response) > 1
        and isinstance(response[0], int)
        and isinstance(response[1], bytes)
        and response[1].upper() == b"FETCH"
        for response in change.responses
    )


@dataclass(slots=True)
class _Running:
    fingerprint: str
    watch: Watch
    reactor: _FolderReactor


class IdleSupervisor:
    """Keeps one watch per folder of ``wanted_folders``, until ``run``'s ``stop`` is set."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        mailboxes: Mailboxes,
        *,
        rules: bool = True,
        index: bool = True,
        listen: Callable[[], psycopg.Connection] | None = None,
        start_watch: Callable[[WatchedFolder, int, Reactor], Watch] | None = None,
        interval: float = RECONCILE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        stop_seconds: float = STOP_SECONDS,
    ) -> None:
        """``rules``: watch the ``new_mail`` rule sets' folders (filters); ``index``: the index's
        (the mail client). ``listen`` opens the autocommit connection that LISTENs on
        ``INTEREST_CHANNEL``; without it only the interval reconciles.
        ``start_watch(folder, revision, reactor)`` starts a folder's watch (``FolderWatch`` on
        ``mailboxes`` by default); ``revision`` is the user's settings' when the account was
        read."""
        self.sessions = sessions
        self._mailboxes = mailboxes
        self._rules = rules
        self._index = index
        self._listen = listen
        self._start_watch = start_watch or self._folder_watch
        self._stop_seconds = stop_seconds
        self._interval = interval
        self._watches: dict[WatchedFolder, _Running] = {}
        self._problems: dict[WatchedFolder, str] = {}
        self._retries: dict[WatchedFolder, _Retry] = {}
        self._delimiters: dict[_Account, tuple[str, str | None]] = {}
        """Each account's delimiter, as a watch started with the fingerprint beside it learnt."""
        self._delimiters_lock = threading.Lock()
        self._wake = threading.Event()
        self._clock = clock

    @property
    def watched(self) -> frozenset[WatchedFolder]:
        return frozenset(self._watches)

    def reactions(self, folder: WatchedFolder) -> Reactions:
        """What the running watch of ``folder`` does."""
        return self._watches[folder].reactor.reactions

    def wake(self) -> None:
        """Reconcile now rather than at the end of the interval."""
        self._wake.set()

    def run(self, stop: threading.Event) -> None:
        """Reconcile now, every ``interval`` and whenever woken, until ``stop`` is set; then stop
        every watch."""
        logger.info("Watching folders.")
        listener = None
        if self._listen is not None and self._index:
            listener = threading.Thread(
                target=self._listen_for_interest, args=(stop,), name="idle-interest", daemon=True
            )
            listener.start()
        try:
            while not stop.is_set():
                self._wake.clear()
                try:
                    self.reconcile()
                except Exception:
                    logger.exception("Could not compare the watched folders with the wanted ones.")
                self._sleep(stop)
        finally:
            self.stop_all()
            if listener is not None:
                listener.join(self._stop_seconds)
            logger.info("Stopped watching folders.")

    def _sleep(self, stop: threading.Event) -> None:
        until = time.monotonic() + self._interval
        while not stop.is_set() and not self._wake.is_set():
            remaining = until - time.monotonic()
            if remaining <= 0:
                return
            self._wake.wait(min(LISTEN_POLL, remaining))

    def _listen_for_interest(self, stop: threading.Event) -> None:
        """LISTEN on ``INTEREST_CHANNEL`` until ``stop``, waking the supervisor on each
        notification; a lost connection is opened again after ``interval``, the interval's
        reconcile standing in meanwhile."""
        assert self._listen is not None
        failed = False
        while not stop.is_set():
            try:
                with self._listen() as connection:
                    connection.execute(f"LISTEN {INTEREST_CHANNEL}")
                    # What was announced while nobody listened.
                    self.wake()
                    failed = False
                    while not stop.is_set():
                        for _notify in connection.notifies(timeout=LISTEN_POLL):
                            self.wake()
            except Exception as error:
                if stop.is_set():
                    return
                if not failed:
                    logger.warning("Could not listen for viewed folders: %s", type(error).__name__)
                failed = True
                stop.wait(self._interval)

    def reconcile(self) -> None:
        """Start, stop and restart watches to match what is wanted; delete the viewing interests
        expired."""
        with self.sessions() as db:
            now = datetime.now(UTC)
            if self._index:
                prune_interest(db, now)
                db.commit()
            wanted = wanted_folders(db, now, rules=self._rules, index=self._index)
        accounts = {account: self._target(*account) for account in sorted(wanted.accounts())}
        delimiters = self._known_delimiters(accounts)
        self._rename_resolved(delimiters)
        folders = wanted.folders(delimiters)
        targets = self._targets(folders, accounts)
        fingerprints = {folder: target.fingerprint for folder, target in targets.items()}
        now_tick = self._clock()
        self._note_failures(now_tick)
        running = {
            folder: (entry.fingerprint, entry.watch.alive)
            for folder, entry in self._watches.items()
        }
        changes = watch_changes(running, fingerprints)
        self._stop_watches([self._watches.pop(folder).watch for folder in sorted(changes.stop)])
        self._retries = {
            folder: retry
            for folder, retry in self._retries.items()
            if fingerprints.get(folder) == retry.fingerprint
        }
        for folder, entry in self._watches.items():
            entry.reactor.reactions = folders[folder]
        started = 0
        for folder in sorted(changes.start):
            retry = self._retries.get(folder)
            if retry is not None and now_tick < retry.after:
                continue
            target = targets[folder]
            reactor = _FolderReactor(self, folder, target.fingerprint, folders[folder])
            watch = self._start_watch(folder, target.revision, reactor)
            self._watches[folder] = _Running(target.fingerprint, watch, reactor)
            started += 1
        if changes.stop or started:
            logger.info(
                "Watching %d folder(s): %d stopped, %d started.",
                len(self._watches),
                len(changes.stop),
                started,
            )

    def learn_delimiter(
        self, folder: WatchedFolder, fingerprint: str, delimiter: str | None
    ) -> None:
        """Remember the delimiter a watch of ``folder``'s account, started with ``fingerprint``,
        looked up: the account's rule set folders can then be named as the server names them."""
        with self._delimiters_lock:
            self._delimiters[folder.user_id, folder.account_id] = (fingerprint, delimiter)

    def _known_delimiters(
        self, accounts: Mapping[_Account, _Target | str]
    ) -> dict[_Account, str | None]:
        """The delimiters learnt under each account's current fingerprint; the others are
        forgotten."""
        with self._delimiters_lock:
            self._delimiters = {
                account: learnt
                for account, learnt in self._delimiters.items()
                if isinstance(target := accounts.get(account), _Target)
                and target.fingerprint == learnt[0]
            }
            return {account: delimiter for account, (_, delimiter) in self._delimiters.items()}

    def _rename_resolved(self, delimiters: Mapping[_Account, str | None]) -> None:
        """Name a running watch of a rule set's folder as the server does, once its account's
        delimiter is known, so it goes on rather than restarting (unless that name already has a
        watch, which then does both)."""
        for folder in [folder for folder in self._watches if folder.segments]:
            account = (folder.user_id, folder.account_id)
            if account not in delimiters:
                continue
            path = folder_path(folder.segments, delimiters[account])
            named = WatchedFolder(folder.user_id, folder.account_id, path)
            if named in self._watches:
                continue
            self._watches[named] = self._watches.pop(folder)
            retry = self._retries.pop(folder, None)
            if retry is not None:
                self._retries[named] = retry

    def _note_failures(self, now: float) -> None:
        """Remember each dead watch's reason and when to try again; forget a folder whose watch
        reached it. A reason is logged when it first comes, not on every retry."""
        for folder, entry in self._watches.items():
            fingerprint, watch = entry.fingerprint, entry.watch
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
                    folder.name,
                    reason,
                    extra={"user_id": folder.user_id, "account_id": folder.account_id},
                )

    def stop_all(self) -> None:
        watches = [entry.watch for entry in self._watches.values()]
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

    def _folder_watch(self, folder: WatchedFolder, revision: int, reactor: Reactor) -> Watch:
        return FolderWatch(self._mailboxes, folder, revision, reactor)

    def _targets(
        self, folders: Iterable[WatchedFolder], accounts: Mapping[_Account, _Target | str]
    ) -> dict[WatchedFolder, _Target]:
        """What each folder that can be watched is watched with; the others are logged once per
        reason."""
        targets: dict[WatchedFolder, _Target] = {}
        problems: dict[WatchedFolder, str] = {}
        for folder in folders:
            found = accounts[folder.user_id, folder.account_id]
            if isinstance(found, _Target):
                targets[folder] = found
            else:
                problems[folder] = found
        for folder, problem in problems.items():
            if self._problems.get(folder) != problem:
                logger.warning(
                    "Cannot watch %s: %s",
                    folder.name,
                    problem,
                    extra={"user_id": folder.user_id, "account_id": folder.account_id},
                )
        self._problems = problems
        return targets

    def _target(self, user_id: int, account_id: int) -> _Target | str:
        """The account's target, or why it cannot be watched before any connection: the app's
        code (``secrets_key_missing``, ``password_missing``) or ``account_not_found``. One read
        of the store, one opening of the password."""
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
        return _Target(account_fingerprint(account, password), loaded.revision)
