"""Each user's IMAP connections, kept per API process: one mailroom-core ``MailConnections`` per
user, built on first use from the user's ``AccountStore`` (settings and sealed passwords).

A user's connections are dropped when idle for ``IDLE_SECONDS``, when one of their accounts is
changed or removed (``mailroom.accounts.service`` calls ``invalidate``), and when their stored
settings are at a newer revision than the one the connections were built under: a change made by
another process (a second API worker, mailroom-mcp on the same database) is noticed on the next
call. mailroom-core looks an account and its password up again whenever it opens a connection.

Every call runs on the pool's shared threads under a deadline (``DEADLINE_SECONDS``). Before it
gets one, it passes gates in the caller's own thread. The process's (``PROCESS_CALLS``, holding or
waiting) bounds how many request threads mail calls take in all; past it a call is ``busy`` at
once. Then the user's (``USER_CALLS`` at once) and, for a call on one account, the account's
(``CONNECTIONS_PER_ACCOUNT``): at each only ``WAITING_CALLS`` may wait, and ``GATE_SECONDS`` is
the wait at both together, beyond which the call is ``busy``. So one user's slow server, or one
user's burst, holds neither the shared threads nor more than a few request threads, though many
users at once can still fill the process's gate. A gate is let go when the work ends, not when
its caller stops waiting. At the deadline the caller gets ``DeadlineExceeded`` and the account's
connections in use are cut (``MailConnections.interrupt``), which ends the work at once.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from mailroom_core.mail import MailConnections, MailError, Timeouts

from mailroom.accounts.store import AccountNotFound, account_password, load_accounts, user_store

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from mailroom_core.config import AccountConfig

    from mailroom.context import AppContext

logger = logging.getLogger(__name__)

IDLE_SECONDS: Final = 300.0
DEADLINE_SECONDS: Final = 30.0
GATE_SECONDS: Final = 10.0
TIMEOUTS: Final = Timeouts(connect=10.0, read=DEADLINE_SECONDS, lock=GATE_SECONDS)
"""A reply slower than the deadline drops its connection; a call waits 10 s at most for one of
the account's connections (then ``busy``)."""
CONNECTIONS_PER_ACCOUNT: Final = 2
"""The web opens a message list, a message and its HTML view at once."""
USER_CALLS: Final = 4
"""A user's calls running at once in this process: folders, a list, a message and its HTML."""
WAITING_CALLS: Final = 4
"""Callers that may wait at a user's or an account's gate; one more is ``busy`` at once."""
PROCESS_CALLS: Final = 24
"""Mail calls holding or waiting in this process, so that they leave request threads (anyio's
40) to every other route."""
WORKERS: Final = 16


class DeadlineExceeded(Exception):  # noqa: N818 -- the outcome of a call, not a bug
    """The call was still running at its deadline; its connections were cut."""


class PasswordMissing(Exception):  # noqa: N818 -- a state of the account, not a bug
    """No stored password opens for the account any more (changed since the call began)."""


class _Gate:
    """At most ``limit`` holders; at most ``waiting`` callers waiting for a turn. ``callers``
    counts those between getting the gate from the pool and being done with it, under the pool's
    lock, so that the pool forgets a gate only when nobody can still use it."""

    def __init__(self, limit: int, waiting: int = WAITING_CALLS) -> None:
        self.limit = limit
        self.waiting_limit = waiting
        self.callers = 0
        self._held = 0
        self._waiting = 0
        self._turn = threading.Condition()

    def enter(self, wait: float) -> bool:
        with self._turn:
            if self._held < self.limit:
                self._held += 1
                return True
            if self._waiting >= self.waiting_limit:
                return False
            self._waiting += 1
            try:
                if not self._turn.wait_for(lambda: self._held < self.limit, timeout=wait):
                    return False
                self._held += 1
                return True
            finally:
                self._waiting -= 1

    def leave(self) -> None:
        with self._turn:
            self._held -= 1
            self._turn.notify()


@dataclass(eq=False)
class _UserMail:
    connections: MailConnections
    revision: int
    leases: int = 0
    last_used: float = field(default_factory=time.monotonic)


class MailPool:
    """The process's users' connections. Thread-safe; ``close`` once, at shutdown."""

    def __init__(
        self,
        *,
        idle_seconds: float = IDLE_SECONDS,
        deadline_seconds: float = DEADLINE_SECONDS,
        gate_seconds: float = GATE_SECONDS,
        timeouts: Timeouts = TIMEOUTS,
        workers: int = WORKERS,
        process_calls: int = PROCESS_CALLS,
    ) -> None:
        self.idle_seconds = idle_seconds
        self.deadline_seconds = deadline_seconds
        self.gate_seconds = gate_seconds
        self._timeouts = timeouts
        self._lock = threading.Lock()
        self._users: dict[int, _UserMail] = {}
        self._process = _Gate(process_calls, waiting=0)
        self._gates: dict[tuple[int, str | None], _Gate] = {}
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mail")
        self._stop = threading.Event()
        self._sweeper: threading.Thread | None = None
        self._closed = False

    @contextmanager
    def lease(self, ctx: AppContext, user_id: int, revision: int) -> Iterator[MailConnections]:
        """The user's connections for the block. ``revision`` is their settings' current one:
        connections built under an older one are dropped first; a caller that read the settings
        before a newer revision was seen reuses the newer connections."""
        stale: _UserMail | None = None
        with self._lock:
            if self._closed:
                msg = "the mail pool is closed"
                raise RuntimeError(msg)
            self._start_sweeper()
            entry = self._users.get(user_id)
            if entry is not None and entry.revision < revision:
                stale, entry = entry, None
            if entry is None:
                entry = _UserMail(self._connections(ctx, user_id), revision)
                self._users[user_id] = entry
            entry.leases += 1
            entry.last_used = time.monotonic()
        if stale is not None:
            stale.connections.close_all()
        try:
            yield entry.connections
        finally:
            with self._lock:
                entry.leases -= 1
                entry.last_used = time.monotonic()

    def call[T](
        self,
        connections: MailConnections,
        user_id: int,
        accounts: Sequence[str],
        work: Callable[[], T],
    ) -> T:
        """``work`` on ``accounts`` of the user, on the pool's threads once the gates let it
        through (``MailError("busy")`` otherwise); ``DeadlineExceeded`` after
        ``deadline_seconds``, the accounts' connections in use then cut."""
        keys: list[tuple[int, str | None, int]] = [(user_id, None, USER_CALLS)]
        if len(accounts) == 1:
            keys.append((user_id, accounts[0], CONNECTIONS_PER_ACCOUNT))
        with ExitStack() as entered:
            if not self._process.enter(0):
                raise MailError("busy")
            entered.callback(self._process.leave)
            gates = self._take_gates(keys)
            entered.callback(self._drop_gates, gates)
            waited_until = time.monotonic() + self.gate_seconds
            for gate in gates:
                if not gate.enter(max(0.0, waited_until - time.monotonic())):
                    raise MailError("busy")
                entered.callback(gate.leave)
            future = self._executor.submit(work)
            held = entered.pop_all()
        future.add_done_callback(lambda _: held.close())
        try:
            return future.result(timeout=self.deadline_seconds)
        except FutureTimeout:
            if not future.cancel():
                for account_id in accounts:
                    connections.interrupt(account_id)
            raise DeadlineExceeded from None

    def invalidate(self, user_id: int, account_id: str | None = None) -> None:
        """Drop the connections of one of the user's accounts, or of all of them."""
        with self._lock:
            entry = self._users.get(user_id)
            if account_id is None:
                if entry is not None:
                    del self._users[user_id]
                self._forget_gates(user_id)
        if entry is None:
            return
        if account_id is None:
            entry.connections.close_all()
        else:
            entry.connections.close(account_id)

    def close_idle(self, now: float | None = None) -> int:
        """Close the users' connections unused for ``idle_seconds``; how many users'."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            idle = [
                user_id
                for user_id, entry in self._users.items()
                if entry.leases == 0 and moment - entry.last_used >= self.idle_seconds
            ]
            entries = [self._users.pop(user_id) for user_id in idle]
            for user_id in idle:
                self._forget_gates(user_id)
        for entry in entries:
            entry.connections.close_all()
        return len(entries)

    def close(self) -> None:
        """Log every connection out and stop the threads; calls still running are abandoned."""
        with self._lock:
            self._closed = True
            entries = list(self._users.values())
            self._users.clear()
        self._stop.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
        for entry in entries:
            entry.connections.close_all()

    def _take_gates(self, keys: Sequence[tuple[int, str | None, int]]) -> list[_Gate]:
        with self._lock:
            gates = [
                self._gates.setdefault((user_id, account_id), _Gate(limit))
                for user_id, account_id, limit in keys
            ]
            for gate in gates:
                gate.callers += 1
            return gates

    def _drop_gates(self, gates: Sequence[_Gate]) -> None:
        with self._lock:
            for gate in gates:
                gate.callers -= 1

    def _forget_gates(self, user_id: int) -> None:
        """Under ``_lock``: the user's gates nobody is using."""
        for key in [k for k, gate in self._gates.items() if k[0] == user_id and gate.callers == 0]:
            del self._gates[key]

    def gate_count(self) -> int:
        """How many user and account gates the pool keeps."""
        with self._lock:
            return len(self._gates)

    def _start_sweeper(self) -> None:
        if self._sweeper is not None:
            return
        self._sweeper = threading.Thread(target=self._sweep, name="mail-idle", daemon=True)
        self._sweeper.start()

    def _sweep(self) -> None:
        interval = max(1.0, min(60.0, self.idle_seconds / 2))
        while not self._stop.wait(interval):
            try:
                self.close_idle()
            except Exception:
                logger.exception("mail_idle_close_failed")

    def _connections(self, ctx: AppContext, user_id: int) -> MailConnections:
        store = user_store(ctx.engine, user_id)
        subkey = ctx.subkey

        def account(account_id: str) -> AccountConfig | None:
            try:
                return load_accounts(store).account(account_id)
            except AccountNotFound:
                return None

        def password(account_id: str) -> str:
            found = account_password(store, subkey, account_id)
            if found is None:
                raise PasswordMissing
            return found

        return MailConnections(
            account,
            password,
            ssl_context=ctx.ssl_context,
            pool_size=CONNECTIONS_PER_ACCOUNT,
            timeouts=self._timeouts,
        )
