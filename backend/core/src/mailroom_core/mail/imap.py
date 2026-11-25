"""IMAP connections per account: the port of `src/imap/connections.ts`, over imapclient.

The API is synchronous, like the store. imapclient (over stdlib imaplib) blocks, and a connection
serves one caller at a time. An async host calls it from a worker thread
(`anyio.to_thread.run_sync`) and first waits on its own semaphore, sized like the pool, so that
callers queued for an account hold no thread. A cancelled await does not stop the thread, so a
host whose own deadline passes calls `interrupt(account)`: the connections in use are shut down,
their blocked calls fail at once with `unreachable` (never retried), and the connections are
discarded. Without that, the deadlines are the socket timeouts here.

Each account has `pool_size` slots (1 by default), each owning one lazily opened connection and
the mailbox selected on it, so a slot is the TypeScript mailbox lock: concurrent callers never read
from each other's folder. A read-only `with_mailbox` of the folder a slot left examined reuses
that selection after a NOOP, as imapflow skips a SELECT of the folder already selected: only when
the server announced no change since (`_announced`) and no `with_connection` ran, else it
examines the folder again, which refreshes UIDVALIDITY, EXISTS, UIDNEXT and HIGHESTMODSEQ. Every
other `with_mailbox` selects.
"""

import imaplib
import logging
import socket
import ssl
import threading
import time
import unicodedata
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, get_args

from imapclient import IMAPClient, SocketTimeout
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError
from imapclient.response_parser import parse_response

from mailroom_core.config import ConfigError, CoreSettings, core_settings_or_issues, issues_text
from mailroom_core.mail.changes import (
    FolderFlags,
    FolderUids,
    changed_flags_of,
    flags_of,
    uids_of,
)
from mailroom_core.mail.errors import (
    MailError,
    MailErrorCode,
    is_connection_error,
    is_temporary,
    mail_error,
)
from mailroom_core.mail.structure_cache import StructureCache

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

    from mailroom_core.config import AccountConfig

_log = logging.getLogger("mailroom_core.mail")

GMAIL: Final = "X-GM-EXT-1"
"""The capability Gmail is told by: its quirks go by it, never by the hostname."""
_HEALTH_WAIT: Final = 1.0
"""Seconds a `reachability` probe waits for a connection of a busy account."""
_ANNOUNCEMENTS: Final = frozenset(
    {
        "BYE",
        "EXISTS",
        "EXPUNGE",
        "FETCH",
        "HIGHESTMODSEQ",
        "RECENT",
        "UIDNEXT",
        "UIDVALIDITY",
        "VANISHED",
    }
)
"""Untagged responses (and response codes, which imaplib files alike) that tell of a change to
the selected mailbox, or of its end: after one, a selection is not reused."""


@dataclass(frozen=True, slots=True)
class Timeouts:
    """Seconds: opening a connection, waiting for any one server reply, waiting for a free
    connection of the account before failing with `busy`, how long a connection may sit
    unused before it is closed rather than reused (imapflow's socket timeout: a connection a
    NAT dropped silently would otherwise hang the next command for `read`), and the bound on
    each connect and reply of a `reachability` probe."""

    connect: float = 15.0
    read: float = 300.0
    lock: float = 30.0
    idle: float = 300.0
    probe: float = 10.0


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What an account's server offers, from its CAPABILITY answer."""

    gmail: bool
    """Gmail's IMAP extensions (X-GM-RAW search, X-GM-THRID threads, labels)."""
    thread: bool
    move: bool
    uidplus: bool
    condstore: bool
    """CONDSTORE (RFC 7162): HIGHESTMODSEQ, and flag changes fetched since a mod-sequence."""
    special_use: bool
    quota: bool
    """QUOTA (RFC 2087, RFC 9208): the account's usage and limits can be asked."""


@dataclass(frozen=True, slots=True)
class QuotaUsage:
    """One resource of a quota: how much is used, out of the limit."""

    used: int
    limit: int


@dataclass(frozen=True, slots=True)
class Quota:
    """The quota INBOX falls under, by resource; None for one the server does not limit."""

    storage: QuotaUsage | None
    """In bytes (the server counts KiB)."""
    messages: QuotaUsage | None


@dataclass(frozen=True, slots=True)
class MailboxContext:
    """What `with_mailbox` hands its function: the connection, with `path` selected."""

    client: IMAPClient
    """Use it inside the function only: once it returns, the connection serves other callers."""
    path: str
    read_only: bool
    uid_validity: int
    exists: int
    uid_next: int | None
    account: int
    """The account's id, as `with_mailbox` was given it."""
    keywords: bool = False
    """Whether keywords can be stored here: PERMANENTFLAGS holds `\\*` (never on EXAMINE, whose
    answer allows no change) and the server is not Gmail, which turns keywords into labels."""
    highest_modseq: int | None = None
    """HIGHESTMODSEQ from the SELECT or EXAMINE answer; None without CONDSTORE, or in a folder
    the server keeps no mod-sequences for (NOMODSEQ). In a reused selection it may lag a flag
    change the server announced inside an earlier call's FETCH: `with_mailbox(reselect=True)`
    for a caller that asks changes since it."""
    structures: StructureCache | None = None
    """The connections' kept message structures (`read.fetch_structure`); None outside them."""

    @property
    def gmail(self) -> bool:
        """Whether the server is Gmail (`GMAIL`)."""
        return bool(self.client.has_capability(GMAIL))


@dataclass(frozen=True, slots=True)
class Reachability:
    """Whether an account's server could be reached, with the error code when not."""

    ok: bool
    code: MailErrorCode | None = None


type FolderRole = Literal["inbox", "flagged", "sent", "drafts", "all", "archive", "junk", "trash"]
FOLDER_ROLES: Final[tuple[FolderRole, ...]] = get_args(FolderRole.__value__)
"""In display order, as imapflow sorts them: special-use folders come first, in this order."""


@dataclass(frozen=True, slots=True)
class Folder:
    """A mailbox as listed, with its role and, when asked for, its STATUS counts."""

    path: str
    delimiter: str | None
    role: FolderRole | None
    """From the special-use flags (RFC 6154); without one, from a well-known name."""
    flags: tuple[str, ...]
    """As listed, e.g. `\\HasNoChildren`; `\\NonExistent` implies `\\Noselect` here."""
    messages: int | None = None
    """This and the rest: only with `with_status`, and None if STATUS failed for this folder."""
    unseen: int | None = None
    uid_validity: int | None = None
    uid_next: int | None = None
    highest_modseq: int | None = None
    """Asked only of a server with CONDSTORE; None too in a folder without mod-sequences."""

    @property
    def selectable(self) -> bool:
        """Whether the folder can be selected: the server did not flag it ``\\Noselect``."""
        return "\\Noselect" not in self.flags


type Stage = Literal["lock", "fn"]


def retriable(stage: Stage, code: MailErrorCode, read_only: bool) -> bool:
    """Whether a failed attempt may run again on a fresh connection, by the stage it failed in
    (`lock`: selecting the folder, or readying a connection, before `fn` started).

    Selecting a folder changes nothing, so a lost connection before `fn` always qualifies. A
    read-only `fn` is idempotent, so it also retries on a server error: a folder deleted by
    another client makes Dovecot answer NO, or drop the connection. A write that may have started
    is never repeated.
    """
    if stage == "lock" and code == "unreachable":
        return True
    return read_only and code in ("unreachable", "server_error")


class _AttemptFailedError(Exception):
    def __init__(self, error: MailError, retry: bool) -> None:
        super().__init__(error.code)
        self.error = error
        self.retry = retry


@dataclass(eq=False)
class _Slot:
    client: IMAPClient | None = None
    generation: int = 0
    busy: bool = False
    last_used: float = 0.0
    thread: int | None = None
    """The thread using it, while busy."""
    interrupted: bool = False
    """Set by `interrupt` while busy: the attempt is not retried and the connection is dropped."""
    examined: MailboxContext | None = None
    """The read-only selection the last `with_mailbox` left on the connection, which the next
    read of that folder may reuse; it counts only while its `client` is the slot's."""

    folders_epoch: int = 0
    """The account's `folders_epoch` when `examined` was selected."""

    def examines(self, path: str, folders_epoch: int) -> bool:
        """Whether the slot's connection holds `path` examined by an earlier read, no folder
        command run since."""
        kept = self.examined
        return (
            kept is not None
            and kept.client is self.client
            and kept.path == path
            and self.folders_epoch == folders_epoch
        )


@dataclass(eq=False)
class _Account:
    slots: list[_Slot]
    free: threading.Condition = field(default_factory=threading.Condition)
    generation: int = 0
    """Bumped by `close`: a connection opened under an older one is dropped when released."""
    folders_epoch: int = 0
    """Bumped by `with_connection`, whose commands may rename or delete a folder another slot
    holds examined (RFC 2180 lets the server keep it open): no older selection is reused."""
    capabilities: Capabilities | None = None
    delimiter: tuple[str | None] | None = None
    """Once asked: a one-tuple, since a server without a hierarchy has None."""
    keywords: bool | None = None
    health: tuple[float, Reachability] | None = None


class MailConnections:
    """IMAP connections for a set of accounts, opened on first use and reopened when they die.

    `account` looks an account up by id (None if there is none), `password` its password; both
    are called each time a connection opens, so a host whose accounts change calls `close` for
    the account and the next call logs in afresh. The Mailroom app keeps one instance per user.
    Errors come out as `MailError`s, whose messages are fixed.
    """

    def __init__(
        self,
        account: Callable[[int], AccountConfig | None],
        password: Callable[[int], str | None],
        *,
        ssl_context: ssl.SSLContext | None = None,
        pool_size: int = 1,
        timeouts: Timeouts = Timeouts(),  # noqa: B008  (frozen)
        health_cache: float = 60.0,
    ) -> None:
        """`ssl_context` defaults to `ssl.create_default_context()`, which verifies the server's
        certificate and name; only tests pass one that does not. `health_cache` is how many
        seconds `reachability` keeps an answer."""
        if pool_size < 1:
            raise ValueError("pool_size must be at least 1")
        self._lookup_account = account
        self._lookup_password = password
        self._ssl_context = ssl_context if ssl_context is not None else ssl.create_default_context()
        self._pool_size = pool_size
        self._timeouts = timeouts
        self._health_cache = health_cache
        self._lock = threading.Lock()
        self._accounts: dict[int, _Account] = {}
        self._structures = StructureCache()

    @classmethod
    def from_accounts(
        cls,
        accounts: Iterable[AccountConfig],
        passwords: Mapping[int, str],
        *,
        ssl_context: ssl.SSLContext | None = None,
        pool_size: int = 1,
        timeouts: Timeouts = Timeouts(),  # noqa: B008  (frozen)
        health_cache: float = 60.0,
    ) -> MailConnections:
        """Connections for a fixed set of accounts and their passwords (by account id), both
        copied now; the options are the constructor's. Two accounts with one id are a
        ValueError; an account with no password, or an empty one, fails with a RuntimeError
        when it opens."""
        by_id: dict[int, AccountConfig] = {}
        for account in accounts:
            if account.id in by_id:
                raise ValueError(f"duplicate account id {account.id}")
            by_id[account.id] = account
        # An empty password is a missing one: never an empty-password login.
        secrets = {account_id: secret for account_id, secret in passwords.items() if secret}
        return cls(
            by_id.get,
            secrets.get,
            ssl_context=ssl_context,
            pool_size=pool_size,
            timeouts=timeouts,
            health_cache=health_cache,
        )

    # Public API

    def with_mailbox[T](
        self,
        account_id: int,
        path: str,
        read_only: bool,
        fn: Callable[[MailboxContext], T],
        *,
        wait: float | None = None,
        reselect: bool = False,
    ) -> T:
        """Runs `fn` with `path` selected on a connection of its own.

        Read-only opens use EXAMINE, so nothing in the mailbox (not even `\\Seen`) can change.
        A read of the folder an earlier read left examined reuses that selection after a NOOP
        when the server announced no change (see the module); `reselect` examines it again
        regardless, for a caller that needs HIGHESTMODSEQ as of now. `fn` must not select
        another folder. Errors come out as MailErrors, `busy` when no connection of the account
        is free within `wait` (`Timeouts.lock`). A connection can look usable and be dead, or
        the folder can have been deleted by another client: see `retriable` for when one more
        attempt is made, always on a newly opened connection. An attempt cut short by
        `interrupt` is never retried.
        """
        try:
            return self._attempt(
                account_id, path, read_only, fn, fresh=False, wait=wait, reselect=reselect
            )
        except _AttemptFailedError as failure:
            if not failure.retry:
                raise failure.error from None
        try:
            return self._attempt(
                account_id, path, read_only, fn, fresh=True, wait=wait, reselect=True
            )
        except _AttemptFailedError as failure:
            raise failure.error from None

    def with_connection[T](self, account_id: int, fn: Callable[[IMAPClient], T]) -> T:
        """Runs `fn` on a connection of the account with no mailbox selected: for commands on
        folders themselves (CREATE, RENAME, DELETE), which must not meet a mailbox this
        connection still holds open.

        A connection a `with_mailbox` left selected leaves its mailbox with UNSELECT when the
        server has it (IMAP4rev2 includes it); otherwise it is dropped for a newly opened one,
        since CLOSE would expunge. A reused connection that was not selected answers a NOOP
        first, so that a dead one is found before `fn`. Readying it may be retried once, on a
        newly opened connection; `fn` is never repeated, as it may have written. Errors come
        out as MailErrors, `busy` when no connection is free within `Timeouts.lock`.
        """
        try:
            return self._bare_attempt(account_id, fn, fresh=False)
        except _AttemptFailedError as failure:
            if not failure.retry:
                raise failure.error from None
        try:
            return self._bare_attempt(account_id, fn, fresh=True)
        except _AttemptFailedError as failure:
            raise failure.error from None

    def serves(self, account_id: int) -> bool:
        """Whether `account_id` is one of these connections' accounts; opens nothing."""
        return self._lookup_account(account_id) is not None

    def capabilities(self, account_id: int) -> Capabilities:
        """What the account's server supports, as it announced after the login."""
        state = self._state(account_id)
        known = state.capabilities
        if known is not None:
            return known
        generation = state.generation
        found = self._on_connection(account_id, lambda client: _capabilities(client.capabilities()))
        with state.free:
            if state.generation == generation:
                state.capabilities = found
        return found

    def keywords(self, account_id: int, *, wait: float | None = None) -> bool:
        """Whether the account's server keeps keywords such as `TO_SORT`, from a read-write
        SELECT of INBOX (`MailboxContext.keywords`: never on Gmail); asked once, then cached
        until the account's connections are closed. `wait` as for `with_mailbox`."""
        state = self._state(account_id)
        known = state.keywords
        if known is not None:
            return known
        generation = state.generation
        found = self.with_mailbox(
            account_id, "INBOX", False, lambda context: context.keywords, wait=wait
        )
        with state.free:
            if state.generation == generation:
                state.keywords = found
        return found

    def delimiter(self, account_id: int) -> str | None:
        """The account's hierarchy delimiter (`LIST "" ""`), None when it has no hierarchy;
        asked once, then cached until the account's connections are closed."""
        state = self._state(account_id)
        known = state.delimiter
        if known is not None:
            return known[0]
        generation = state.generation
        found = self._on_connection(account_id, _delimiter)
        with state.free:
            if state.generation == generation:
                state.delimiter = (found,)
        return found

    def folders(
        self, account_id: int, *, with_status: bool | Callable[[Folder], bool] = False
    ) -> list[Folder]:
        """The account's folders: special-use ones first in `FOLDER_ROLES` order, then the rest
        by path. `with_status` adds the counts (one STATUS per selectable folder); a predicate
        instead adds them to the selectable folders it holds true of only."""

        def run(client: IMAPClient) -> list[Folder]:
            folders = folders_from(client.list_folders())
            if with_status is False:
                return folders
            return [
                _with_status(client, f)
                if f.selectable and (with_status is True or with_status(f))
                else f
                for f in folders
            ]

        return self._on_connection(account_id, run)

    def folder_uids(self, account_id: int, path: str) -> FolderUids:
        """Every UID in the folder (`changes.uids_of`), read under EXAMINE."""
        return self.with_mailbox(account_id, path, True, uids_of, reselect=True)

    def folder_flags(self, account_id: int, path: str) -> FolderFlags:
        """Every message's flags in the folder (`changes.flags_of`), read under EXAMINE."""
        return self.with_mailbox(account_id, path, True, flags_of, reselect=True)

    def changed_flags(self, account_id: int, path: str, since: int) -> FolderFlags:
        """The flags changed in the folder since the mod-sequence `since`
        (`changes.changed_flags_of`), read under EXAMINE; `unsupported` without CONDSTORE."""
        return self.with_mailbox(
            account_id, path, True, lambda context: changed_flags_of(context, since), reselect=True
        )

    def quota(self, account_id: int) -> Quota | None:
        """The quota INBOX falls under (GETQUOTAROOT); None when the server has no QUOTA, answers
        NO to GETQUOTAROOT (a temporary refusal aside) or puts no limit on storage or message
        count. An answer that names no quota root counts every QUOTA line it carries."""
        if not self.capabilities(account_id).quota:
            return None
        return self._on_connection(account_id, _quota)

    def special_folder(self, account_id: int, role: FolderRole) -> Folder:
        """The account's folder with `role`; `unsupported` if it has none."""
        for folder in self.folders(account_id):
            if folder.role == role:
                return folder
        raise MailError("unsupported", f"Account {account_id} has no {role.capitalize()} folder.")

    def reachability(self, account_id: int) -> Reachability:
        """Whether the account answers a login and a NOOP; cached for `health_cache` seconds,
        so that health probes cost nothing. Never raises: any failure is an answer.

        A probe waits at most a second for a connection, `Timeouts.probe` for each connect and
        reply, and is not retried. When all connections are in use, the account is busy, not
        down: the last answer (or ok) is returned, and not cached.
        """
        try:
            state = self._state(account_id)
        except MailError as error:
            return Reachability(ok=False, code=error.code)
        except Exception:
            _log.exception("imap_health_failed", extra={"account": account_id})
            return Reachability(ok=False, code="server_error")
        cached = state.health
        if cached is not None and time.monotonic() - cached[0] < self._health_cache:
            return cached[1]
        generation = state.generation
        try:
            self._probe(account_id)
            result = Reachability(ok=True)
        except MailError as error:
            if error.code == "busy":
                return cached[1] if cached is not None else Reachability(ok=True)
            result = Reachability(ok=False, code=error.code)
        except Exception:
            _log.exception("imap_health_failed", extra={"account": account_id})
            result = Reachability(ok=False, code="server_error")
        with state.free:
            if state.generation == generation:
                state.health = (time.monotonic(), result)
        return result

    def test_login(self, account_id: int) -> None:
        """Logs in to the account and out again on a connection of its own, outside the pool,
        so a busy account is still tested; every connect and reply is bounded by
        `Timeouts.probe`, with no retry. A failure is a MailError (`auth_failed`,
        `unreachable`, `timeout`…); a missing password is the host's RuntimeError, as for
        every connection."""
        _log_out(self._open_unpooled(account_id, read_timeout=self._timeouts.probe))

    def interrupt(self, account_id: int, *, thread: int | None = None) -> int:
        """Cuts the account's connections that are in use, or only those the thread `thread`
        (its `threading.get_ident`) uses, and returns how many.

        For a host whose own deadline passed while a call still runs in its worker thread:
        shutting the socket down makes the blocked command fail at once with `unreachable`,
        the call is not retried, and the connection is discarded, so the slot frees up for the
        next caller. A call still opening its connection fails once the login is done. Idle
        connections are left alone. Never waits.
        """
        with self._lock:
            state = self._accounts.get(account_id)
        if state is None:
            return 0
        cut = []
        with state.free:
            for slot in state.slots:
                if slot.busy and thread in (None, slot.thread):
                    slot.interrupted = True
                    if slot.client is not None:
                        cut.append(slot.client)
        for client in cut:
            with suppress(Exception):
                # Below TLS: SSLSocket.shutdown first detaches the TLS layer, and the owner's
                # next write would go out in clear text.
                socket.socket.shutdown(client.socket(), socket.SHUT_RDWR)
        return len(cut)

    def close(self, account_id: int) -> None:
        """Logs out the account's connections and forgets what was learnt from them, the kept
        message structures included.

        Never waits: a connection in use is dropped when its caller is done with it. The next
        call opens a new connection, looking the account and its password up again.
        """
        self._structures.forget(account_id)
        with self._lock:
            state = self._accounts.get(account_id)
        if state is not None:
            self._close(state)

    def close_all(self) -> None:
        """Logs out every account's connections, as ``close`` does for one."""
        with self._lock:
            states = list(self._accounts.items())
        for account_id, state in states:
            self._structures.forget(account_id)
            self._close(state)

    # Connections

    def _state(self, account_id: int) -> _Account:
        with self._lock:
            state = self._accounts.get(account_id)
        if state is not None:
            return state
        # The host's lookup may be a database query: never under the instance-wide lock. Ids
        # come from tool input, so an unknown one must not leave state behind.
        if self._lookup_account(account_id) is None:
            raise MailError("invalid_argument", f"Unknown account {account_id}.")
        with self._lock:
            return self._accounts.setdefault(
                account_id, _Account([_Slot() for _ in range(self._pool_size)])
            )

    @contextmanager
    def _slot(
        self,
        account_id: int,
        *,
        fresh: bool = False,
        wait: float | None = None,
        examining: str | None = None,
    ) -> Iterator[tuple[_Account, _Slot]]:
        """A free slot of the account, waiting up to `wait` (`Timeouts.lock`) for one; one that
        holds `examining` examined first, if any.

        `fresh` (a retry) wants a connection opened for it: an idle one is no better than the
        one that just failed. A connection unused for `Timeouts.idle` is not reused either.
        """
        state = self._state(account_id)
        deadline = time.monotonic() + (self._timeouts.lock if wait is None else wait)
        old = None
        with state.free:
            while True:
                idle = [slot for slot in state.slots if not slot.busy]
                if idle:
                    # An open connection before a closed one, so the pool grows only under
                    # load; for a retry, the other way round.
                    slot = next(
                        (
                            s
                            for s in idle
                            if examining is not None and s.examines(examining, state.folders_epoch)
                        ),
                        None,
                    ) or next((s for s in idle if (s.client is None) == fresh), idle[0])
                    slot.busy = True
                    slot.thread = threading.get_ident()
                    slot.interrupted = False
                    expired = time.monotonic() - slot.last_used > self._timeouts.idle
                    if slot.client is not None and (fresh or expired):
                        old, slot.client = slot.client, None
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not state.free.wait(remaining):
                    raise MailError("busy")
        if old is not None:
            _drop(old)  # possibly dead: no LOGOUT to wait on
        try:
            yield state, slot
        finally:
            stale = None
            with state.free:
                cut = slot.interrupted
                if slot.client is not None and (slot.generation != state.generation or cut):
                    stale, slot.client = slot.client, None
                slot.busy = False
                slot.thread = None
                slot.interrupted = False
                slot.last_used = time.monotonic()
                state.free.notify()
            if stale is not None:
                if cut:
                    _drop(stale)  # its socket is shut down: a LOGOUT could only wait
                else:
                    _log_out(stale)

    def _client(
        self, account_id: int, state: _Account, slot: _Slot, *, read: float | None = None
    ) -> IMAPClient:
        """The slot's connection, opened if it has none; `read` bounds the opening's replies
        instead of `Timeouts.read`, and stays set on the new connection."""
        if slot.client is None:
            generation = state.generation
            slot.client = self._open(account_id, state, generation, read)
            slot.generation = generation
            if slot.interrupted:
                self._discard(slot)
                raise MailError("unreachable")
        return slot.client

    def _open(
        self, account_id: int, state: _Account, generation: int, read: float | None
    ) -> IMAPClient:
        client = self._open_unpooled(account_id, read_timeout=read)
        try:
            # Capabilities from the login's response; ENABLE CONDSTORE is one round trip more
            # where offered.
            capabilities = _capabilities(client.capabilities())
            if capabilities.condstore:
                _enable_condstore(client)
        except Exception as error:
            _drop(client)
            raise mail_error(error, account_id) from None
        with state.free:
            if state.generation == generation:
                state.capabilities = capabilities
        return client

    def _open_unpooled(self, account_id: int, *, read_timeout: float | None = None) -> IMAPClient:
        """A logged-in connection to the account, owned by the caller and outside the slots:
        implicit TLS or STARTTLS, then the login `_log_in` chooses. `read_timeout` bounds the
        opening's replies instead of `Timeouts.read`, and stays set on the connection."""
        account = self._lookup_account(account_id)
        if account is None:
            raise MailError("invalid_argument", f"Unknown account {account_id}.")
        password = self._lookup_password(account_id)
        if password is None:
            # Never an empty-password login: a host that knows the account knows its secret.
            raise RuntimeError(f"no password for account {account_id}")
        implicit = account.imap.tls == "implicit"
        timeout = (
            SocketTimeout(connect=self._timeouts.connect, read=self._timeouts.read)
            if read_timeout is None
            else SocketTimeout(connect=min(self._timeouts.connect, read_timeout), read=read_timeout)
        )
        try:
            client = IMAPClient(
                account.imap.host,
                account.imap.port,
                ssl=implicit,
                ssl_context=self._ssl_context,
                timeout=timeout,
            )
        except Exception as error:
            raise mail_error(error, account_id) from None
        # Dates as the server gave them, with their offset, not converted to naive local time.
        client.normalise_times = False
        try:
            if not implicit:
                client.starttls(self._ssl_context)
        except Exception as error:
            _drop(client)
            raise mail_error(error, account_id) from None
        try:
            _log_in(client, account.login, password)
        except Exception as error:
            _drop(client)
            # Its text can quote the credentials: only the error's type is logged.
            raise mail_error(error, account_id, secret=True) from None
        _log.info("imap_connected", extra={"event": "imap_connected", "account": account_id})
        return client

    def _attempt[T](
        self,
        account_id: int,
        path: str,
        read_only: bool,
        fn: Callable[[MailboxContext], T],
        *,
        fresh: bool,
        wait: float | None,
        reselect: bool,
    ) -> T:
        reusable = read_only and not reselect
        examining = path if reusable and not fresh else None
        with self._slot(account_id, fresh=fresh, wait=wait, examining=examining) as (state, slot):
            client = self._client(account_id, state, slot)
            epoch = state.folders_epoch
            current = slot.folders_epoch == epoch
            kept, slot.examined = slot.examined, None
            try:
                context = (
                    kept
                    if reusable
                    and current
                    and kept is not None
                    and _still_examined(kept, client, path)
                    else self._select(account_id, client, path, read_only)
                )
            except Exception as error:
                raise self._failure(account_id, state, slot, error, "lock", read_only) from None
            except BaseException:
                self._discard(slot)
                raise
            try:
                result = fn(context)
                if read_only:
                    slot.examined, slot.folders_epoch = context, epoch
                return result
            except Exception as error:
                raise self._failure(account_id, state, slot, error, "fn", read_only) from None
            except BaseException:
                # Interrupted mid-command, the connection's state is unknown.
                self._discard(slot)
                raise

    def _select(
        self, account_id: int, client: IMAPClient, path: str, read_only: bool
    ) -> MailboxContext:
        """SELECT or EXAMINE `path`, its answer as a context; what it announced is spent."""
        selected = client.select_folder(path, readonly=read_only)
        _announced(client)
        return MailboxContext(
            client=client,
            path=path,
            read_only=read_only,
            uid_validity=int(selected[b"UIDVALIDITY"]),
            exists=int(selected[b"EXISTS"]),
            uid_next=int(selected[b"UIDNEXT"]) if b"UIDNEXT" in selected else None,
            account=account_id,
            keywords=_keywords(client, selected),
            highest_modseq=_highest_modseq(selected),
            structures=self._structures,
        )

    def _bare_attempt[T](self, account_id: int, fn: Callable[[IMAPClient], T], *, fresh: bool) -> T:
        with self._slot(account_id, fresh=fresh) as (state, slot):
            slot.examined = None
            reused = slot.client is not None
            client = self._client(account_id, state, slot)
            try:
                client = self._unselected(account_id, state, slot, client, reused=reused)
            except Exception as error:
                raise self._failure(account_id, state, slot, error, "lock", False) from None
            except BaseException:
                self._discard(slot)
                raise
            try:
                return fn(client)
            except Exception as error:
                raise self._failure(account_id, state, slot, error, "fn", False) from None
            except BaseException:
                # Interrupted mid-command, the connection's state is unknown.
                self._discard(slot)
                raise
            finally:
                with state.free:
                    state.folders_epoch += 1

    def _unselected(
        self, account_id: int, state: _Account, slot: _Slot, client: IMAPClient, *, reused: bool
    ) -> IMAPClient:
        """The slot's connection with no mailbox selected: UNSELECT, or a new connection when
        the server has no UNSELECT (never CLOSE, which expunges `\\Deleted` messages)."""
        if client._imap.state != "SELECTED":  # noqa: SLF001
            if reused:
                client.noop()
            return client
        if _unselect(client):
            return client
        self._discard(slot)
        return self._client(account_id, state, slot)

    def _failure(
        self,
        account_id: int,
        state: _Account,
        slot: _Slot,
        error: Exception,
        stage: Stage,
        read_only: bool,
    ) -> _AttemptFailedError:
        mapped = self._mapped(account_id, state, slot, error)
        # A bug in `fn` (not a server or connection error) would only fail again.
        retry = (
            not slot.interrupted
            and (isinstance(error, MailError) or is_connection_error(error))
            and retriable(stage, mapped.code, read_only)
        )
        if retry:
            # Dropped while this attempt still holds the slot, so no other caller starts on it.
            self._discard(slot)
        return _AttemptFailedError(mapped, retry)

    def _mapped(self, account_id: int, state: _Account, slot: _Slot, error: Exception) -> MailError:
        """`error` as a MailError, dropping the connections it shows to be unusable."""
        mapped = mail_error(error, account_id)
        if mapped.code == "unreachable" and not slot.interrupted:
            # The other idle connections most likely share the cause (a restart, a network
            # change): none of them is worth a command that would fail the same way. Not so
            # for a connection `interrupt` cut, which only the host's deadline condemned.
            self._discard_idle(state)
        # A connection that timed out is never usable again. After a read-write SELECT the
        # server opened READ-ONLY, imaplib refuses every command but the next SELECT.
        if mapped.code in ("unreachable", "timeout") or isinstance(error, imaplib.IMAP4.readonly):
            self._discard(slot)
        return mapped

    def _on_connection[T](
        self, account_id: int, fn: Callable[[IMAPClient], T], *, wait: float | None = None
    ) -> T:
        """`fn` on a connection of the account, with any folder selected; for commands that
        change nothing, so a connection found dead is replaced once, by a newly opened one
        (imapflow notices a closed socket before use, imaplib only on it)."""
        for last in (False, True):
            with self._slot(account_id, fresh=last, wait=wait) as (state, slot):
                client = self._client(account_id, state, slot)
                try:
                    return fn(client)
                except Exception as error:
                    mapped = self._mapped(account_id, state, slot, error)
                    if last or mapped.code != "unreachable" or slot.interrupted:
                        raise mapped from None
                except BaseException:
                    self._discard(slot)
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _probe(self, account_id: int) -> None:
        """A NOOP on a connection of the account, opening one if need be, with every connect
        and reply bounded by `Timeouts.probe` and no retry: a health check answers quickly."""
        bound = self._timeouts.probe
        with self._slot(account_id, wait=_HEALTH_WAIT) as (state, slot):
            client = self._client(account_id, state, slot, read=bound)
            try:
                client.socket().settimeout(bound)
                client.noop()
            except Exception as error:
                raise self._mapped(account_id, state, slot, error) from None
            except BaseException:
                self._discard(slot)
                raise
            finally:
                kept = slot.client
                if kept is not None:
                    with suppress(Exception):
                        kept.socket().settimeout(self._timeouts.read)

    @staticmethod
    def _discard(slot: _Slot) -> None:
        if slot.client is not None:
            _drop(slot.client)
            slot.client = None

    @staticmethod
    def _discard_idle(state: _Account) -> None:
        dropped = []
        with state.free:
            for slot in state.slots:
                if not slot.busy and slot.client is not None:
                    dropped.append(slot.client)
                    slot.client = None
        for client in dropped:
            _drop(client)

    @staticmethod
    def _close(state: _Account) -> None:
        idle = []
        with state.free:
            state.generation += 1
            state.capabilities = None
            state.delimiter = None
            state.keywords = None
            state.health = None
            for slot in state.slots:
                if not slot.busy and slot.client is not None:
                    idle.append(slot.client)
                    slot.client = None
        for client in idle:
            _log_out(client)


def connections_from(
    settings: Mapping[str, object] | CoreSettings,
    passwords: Mapping[int, str],
    *,
    ssl_context: ssl.SSLContext | None = None,
    pool_size: int = 1,
    timeouts: Timeouts = Timeouts(),  # noqa: B008  (frozen)
) -> MailConnections:
    """Connections for the accounts of plain config: the settings the app stores (`[[account]]`,
    optionally `[limits]`), as `tomllib` reads them, and each account's password by id.

    A mapping is checked by `core_settings_or_issues`, every problem listed in the `ConfigError`.
    An account with no password, or an empty one, fails with a RuntimeError when it opens.
    """
    if not isinstance(settings, CoreSettings):
        checked = core_settings_or_issues(settings)
        if not isinstance(checked, CoreSettings):
            raise ConfigError(f"invalid settings:\n{issues_text(checked)}")
        settings = checked
    return MailConnections.from_accounts(
        settings.account, passwords, ssl_context=ssl_context, pool_size=pool_size, timeouts=timeouts
    )


def _log_in(client: IMAPClient, login: str, password: str) -> None:
    """AUTHENTICATE PLAIN when offered, which carries UTF-8 as imapflow does; else LOGIN, whose
    arguments imaplib can send only in ASCII."""
    offered = {bytes(cap).upper() for cap in client.capabilities()}
    if b"AUTH=PLAIN" in offered:
        client.plain_login(login, password)
    elif login.isascii() and password.isascii():
        client.login(login, password)
    else:
        raise MailError(
            "auth_failed", "The server accepts only ASCII logins and passwords (no AUTH=PLAIN)."
        )


def _announced(client: IMAPClient) -> bool:
    """Whether the server announced a change to the selected mailbox (`_ANNOUNCEMENTS`) since
    this was last asked; the announcements are spent. imaplib files every untagged response
    by its name until a command takes it, and a FETCH takes only FETCH responses: an EXISTS or
    an EXPUNGE that came in the middle of one waits here."""
    pending = client._imap.untagged_responses  # noqa: SLF001
    found = [name for name in pending if str(name).upper() in _ANNOUNCEMENTS]
    for name in found:
        del pending[name]
    return bool(found)


def _still_examined(kept: MailboxContext, client: IMAPClient, path: str) -> bool:
    """Whether `kept`, a read-only selection of `path` on `client`, still stands: nothing was
    announced since, through to a NOOP, which also finds a dead connection before `fn` runs."""
    if kept.client is not client or kept.path != path or not kept.read_only:
        return False
    if client._imap.state != "SELECTED" or _announced(client):  # noqa: SLF001
        return False
    client.noop()
    return not _announced(client)


def _drop(client: IMAPClient) -> None:
    """Closes the socket without a LOGOUT: for a connection that is dead or in an unknown state."""
    with suppress(Exception):
        client.shutdown()


def _unselect(client: IMAPClient) -> bool:
    """Leaves the selected mailbox with UNSELECT, which expunges nothing, when the server offers
    it (IMAP4rev2 includes it); False when it does not, or refused it."""
    if client.has_capability("UNSELECT"):
        client.unselect_folder()
    elif client.has_capability("IMAP4rev2"):
        # imapclient's unselect_folder asks for UNSELECT by name; this is what it sends.
        typ, _data = client._imap._simple_command("UNSELECT")  # noqa: SLF001
        if typ == "OK":
            client._imap.state = "AUTH"  # noqa: SLF001
    # imapclient answers a NO without raising, leaving the state as it was.
    return bool(client._imap.state != "SELECTED")  # noqa: SLF001


def _log_out(client: IMAPClient) -> None:
    try:
        client.socket().settimeout(5)
        client.logout()
    except Exception:
        _drop(client)


def _keywords(client: IMAPClient, selected: Mapping[bytes, object]) -> bool:
    permanent = selected.get(b"PERMANENTFLAGS")
    return isinstance(permanent, tuple) and b"\\*" in permanent and not client.has_capability(GMAIL)


def _enable_condstore(client: IMAPClient) -> None:
    """ENABLE CONDSTORE (RFC 5161), without which Dovecot leaves HIGHESTMODSEQ out of a SELECT's
    answer; a refusal leaves the folders without one, as on a server without CONDSTORE."""
    if not client.has_capability("ENABLE"):
        return
    try:
        client.enable("CONDSTORE")
    except IMAPClientAbortError:
        raise
    except IMAPClientError:
        _log.info("condstore_not_enabled", extra={"event": "condstore_not_enabled"})


def _highest_modseq(selected: Mapping[bytes, object]) -> int | None:
    # Absent without CONDSTORE and under NOMODSEQ; 0 would be no mod-sequence either.
    value = selected.get(b"HIGHESTMODSEQ")
    return value if isinstance(value, int) and value > 0 else None


def _capabilities(raw: tuple[bytes, ...]) -> Capabilities:
    caps = {bytes(cap).upper() for cap in raw}
    return Capabilities(
        gmail=GMAIL.encode() in caps,
        thread=b"THREAD=REFERENCES" in caps,
        move=b"MOVE" in caps,
        uidplus=b"UIDPLUS" in caps,
        condstore=b"CONDSTORE" in caps,
        special_use=b"SPECIAL-USE" in caps,
        quota=b"QUOTA" in caps,
    )


def _quota(client: IMAPClient) -> Quota | None:
    # Sent through imaplib, not imapclient's get_quota_root: that one fails with an IndexError on
    # an answer without a QUOTAROOT line (Mailu's Dovecot gives one), and drops the QUOTA lines.
    # A BAD raises imaplib's error, which is imapclient's IMAPClientError.
    typ, (root_lines, quota_lines) = client._imap.getquotaroot("INBOX")  # noqa: SLF001
    if typ != "OK":
        # On a NO, imaplib gives the tagged answer's text in place of each list.
        text = " ".join(_text(line) for line in root_lines if line is not None)
        # A NO: a server that offers QUOTA but keeps none for INBOX, as no QUOTA at all. A
        # temporary refusal ([UNAVAILABLE]) is a failure, and so not kept.
        if not is_temporary(text):
            return None
        raise IMAPClientError(f"GETQUOTAROOT failed: {text}")
    roots = _quota_roots(root_lines)
    found: dict[str, QuotaUsage] = {}
    for root, resources in _quota_lines(quota_lines):
        if roots is not None and root not in roots:
            continue
        for resource, used, limit in resources:
            found.setdefault(resource, QuotaUsage(used=used, limit=limit))
    storage = found.get("STORAGE")
    if storage is not None:
        storage = QuotaUsage(used=storage.used * 1024, limit=storage.limit * 1024)
    messages = found.get("MESSAGE")
    if storage is None and messages is None:
        return None
    return Quota(storage=storage, messages=messages)


def _quota_roots(lines: list[bytes | None]) -> set[str] | None:
    """INBOX's quota roots from the QUOTAROOT lines; None when the server sent none, and then
    every QUOTA line it sent counts."""
    roots: set[str] | None = None
    for line in lines:
        if line is None:
            continue
        parsed = parse_response([line])
        roots = (roots or set()) | {_text(root) for root in parsed[1:]}
    return roots


def _quota_lines(lines: list[bytes | None]) -> list[tuple[str, list[tuple[str, int, int]]]]:
    """Each QUOTA line as its root and its (resource, usage, limit) triples."""
    found: list[tuple[str, list[tuple[str, int, int]]]] = []
    for line in lines:
        if line is None:
            continue
        parsed = parse_response([line])
        if len(parsed) < 2 or not isinstance(parsed[1], tuple):
            continue
        items = parsed[1]
        triples = [
            (_text(items[i]).upper(), int(items[i + 1]), int(items[i + 2]))
            for i in range(0, len(items) - 2, 3)
        ]
        found.append((_text(parsed[0]), triples))
    return found


# Folder roles, as imapflow assigns them (`commands/list.js`, `special-use.js`): the special-use
# flag wins, then INBOX by name, then a well-known folder name, then such a name decorated with
# generic words ("Sent Mail", "Posta inviata"). Each role goes to one folder and each folder gets
# one role, the stronger source first, then by path. imapflow's name tables cover dozens of
# languages; these cover English and Italian, enough for servers without SPECIAL-USE.

_FLAG_ROLES: Final[dict[str, FolderRole]] = {
    "\\all": "all",
    "\\archive": "archive",
    "\\drafts": "drafts",
    "\\flagged": "flagged",
    "\\junk": "junk",
    "\\sent": "sent",
    "\\trash": "trash",
}
_NAMES: Final[dict[FolderRole, str]] = {
    "sent": "sent|sent items|sent messages|inviata|inviate|inviati",
    "drafts": "drafts|draft|bozze",
    "trash": "trash|bin|deleted|deleted items|deleted messages|cestino|eliminata",
    "junk": "junk|spam|bulk mail|junk mail|junk email|junk e-mail|junk-e-mail|indesiderata",
    "archive": "archive|archives|archivio|archivi",
}
_NAME_ROLES: Final[dict[str, FolderRole]] = {
    name: role for role, names in _NAMES.items() for name in names.split("|")
}
_GENERIC_WORDS: Final = frozenset(
    {
        "e",
        "mail",
        "mails",
        "email",
        "emails",
        "message",
        "messages",
        "item",
        "items",
        "folder",
        "my",
        "posta",
        "elementi",
        "messaggi",
    }
)
_WORD_SPLIT: Final = str.maketrans(dict.fromkeys(" \t-_/.,()[]", " "))
_SOURCE_ORDER: Final = ("extension", "name", "name-guess")


def _name_role(name: str) -> tuple[FolderRole, str] | None:
    bare = name.lower().replace("\N{LEFT-TO-RIGHT MARK}", "").strip()
    folded = unicodedata.normalize("NFKC", bare)
    role = _NAME_ROLES.get(folded)
    if role is not None:
        return role, "name"
    core = [word for word in folded.translate(_WORD_SPLIT).split() if word not in _GENERIC_WORDS]
    if len(core) == 1 and core[0] != folded:
        role = _NAME_ROLES.get(core[0])
        if role is not None:
            return role, "name-guess"
    return None


def _delimiter(client: IMAPClient) -> str | None:
    for _flags, delimiter, _name in client.list_folders("", ""):
        if delimiter:
            return _text(delimiter)
    return None


def _text(value: bytes | str) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def folders_from(listing: list[tuple[tuple[bytes, ...], bytes | None, str]]) -> list[Folder]:
    """LIST results as Folders with roles, in imapflow's order."""
    entries: list[tuple[str, str | None, tuple[str, ...]]] = []
    candidates: list[tuple[int, str, FolderRole, int]] = []
    for index, (raw_flags, raw_delimiter, raw_path) in enumerate(listing):
        flags = [_text(flag) for flag in raw_flags]
        lowered = {flag.lower() for flag in flags}
        phantom = "\\nonexistent" in lowered
        if phantom and "\\noselect" not in lowered:
            flags.append("\\Noselect")
        delimiter = None if raw_delimiter is None else _text(raw_delimiter)
        path = _text(raw_path)
        if delimiter and path.startswith(delimiter):
            path = path[len(delimiter) :]
        entries.append((path, delimiter, tuple(flags)))
        flag_role = next((_FLAG_ROLES[f] for f in lowered if f in _FLAG_ROLES), None)
        if flag_role is not None:
            candidates.append((0, path, flag_role, index))
        if phantom:
            continue  # only flags the server set are trusted for a phantom entry
        if path.upper() == "INBOX":
            candidates.append((1, path, "inbox", index))
        name = path.rsplit(delimiter, 1)[-1] if delimiter else path
        guessed = _name_role(name)
        if guessed is not None:
            candidates.append((_SOURCE_ORDER.index(guessed[1]), path, guessed[0], index))
    roles: dict[int, FolderRole] = {}
    taken: set[FolderRole] = set()
    for _source, _path, role, index in sorted(candidates, key=lambda c: (c[0], c[1])):
        if role not in taken and index not in roles:
            roles[index] = role
            taken.add(role)
    folders = [
        Folder(path=path, delimiter=delimiter, role=roles.get(index), flags=flags)
        for index, (path, delimiter, flags) in enumerate(entries)
    ]

    def order(folder: Folder) -> tuple[int, int, list[str], str]:
        if folder.role is not None:
            return (0, FOLDER_ROLES.index(folder.role), [], folder.path)
        segments = folder.path.split(folder.delimiter) if folder.delimiter else [folder.path]
        return (1, 0, segments, folder.path)

    return sorted(folders, key=order)


def _with_status(client: IMAPClient, folder: Folder) -> Folder:
    items = ["MESSAGES", "UNSEEN", "UIDVALIDITY", "UIDNEXT"]
    if client.has_capability("CONDSTORE"):
        items.append("HIGHESTMODSEQ")
    try:
        status = client.folder_status(folder.path, items)
    except IMAPClientAbortError:
        raise
    except IMAPClientError:
        # Deleted between LIST and STATUS, say: the listing is still worth having.
        return folder

    def count(key: bytes) -> int | None:
        value = status.get(key)
        return None if value is None else int(value)

    return Folder(
        path=folder.path,
        delimiter=folder.delimiter,
        role=folder.role,
        flags=folder.flags,
        messages=count(b"MESSAGES"),
        unseen=count(b"UNSEEN"),
        uid_validity=count(b"UIDVALIDITY"),
        uid_next=count(b"UIDNEXT"),
        highest_modseq=count(b"HIGHESTMODSEQ") or None,
    )
