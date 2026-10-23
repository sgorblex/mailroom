"""IMAP IDLE monitoring: run a handler on each event, or folder filters when a trigger fires."""

from __future__ import annotations

import logging
import ssl
import threading
import time
from typing import TYPE_CHECKING, Any

import imapclient

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import FrameType

    from imap_thingy.accounts import Folder
    from imap_thingy.filters import Filter

IdleResponse = list[tuple[Any, ...]]
IDLE_TIMEOUT = 25 * 60
IDLE_POLL = 1.0
"""Seconds between checks of the stop request while idling; bounds how long stop() takes."""
RECONNECT_MIN_BACKOFF = 5.0
RECONNECT_MAX_BACKOFF = 300.0


class IdleTrigger:
    """Wraps a callable (IdleResponse) -> bool to decide when to run filters."""

    def __init__(self, func: Callable[[IdleResponse], bool]) -> None:
        """Wrap func; triggers(responses) will call func(responses)."""
        self._func = func

    def triggers(self, responses: IdleResponse) -> bool:
        """Return True if filters should run for this IDLE response."""
        return self._func(responses)


on_any_event = IdleTrigger(lambda r: True)
on_new_mail = IdleTrigger(lambda r: any(x[1] == b"EXISTS" for x in r))


def _flags_contain_seen(item: tuple[Any, ...]) -> bool:
    if len(item) < 3 or item[1] != b"FETCH":
        return False
    part = item[2]
    if not isinstance(part, (list, tuple)) or len(part) < 2 or part[0] != b"FLAGS":
        return False
    flags = part[1]
    flags = (flags,) if not isinstance(flags, (list, tuple)) else flags
    return b"\\Seen" in flags


on_read = IdleTrigger(lambda r: any(_flags_contain_seen(x) for x in r))


class IdleHandler:
    """Wraps a callable (IdleResponse) -> None to handle IDLE responses."""

    def __init__(self, func: Callable[[IdleResponse], None]) -> None:
        """Wrap func; handle(responses) will call func(responses)."""
        self._func = func

    def handle(self, responses: IdleResponse) -> None:
        """Invoke the wrapped handler with the IDLE response."""
        self._func(responses)


class IdleMonitor:
    """Watches an IMAP folder for IDLE events and runs a handler on each."""

    def __init__(
        self,
        folder: Folder,
        handler: IdleHandler,
    ) -> None:
        """Watch folder and call handler on each IDLE response."""
        self.folder = folder
        self.account = folder.account
        self.handler = handler
        self._thread: threading.Thread | None = None
        self._started = False
        self.log = logging.getLogger(__name__).getChild(str(self.folder))
        self._conn_lock = threading.Lock()
        self._conn: imapclient.IMAPClient | None = None
        self._stop_event = threading.Event()
        # Delay before the next reconnect attempt; 0 until something fails,
        # reset after a healthy IDLE cycle.
        self._backoff = 0.0

    def start(self) -> IdleMonitor:
        """Start watching.

        Returns self. One-shot: do not call start() again after stop()/join().
        """
        if self._started or self._stop_event.is_set():
            raise RuntimeError("monitor is one-shot; cannot start again after stop/join")
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("monitor already started")
        self._started = True
        self._conn = self.folder.connect(readonly=True)
        self.log.info("Started watching")
        self._thread = threading.Thread(target=self._watch)
        self._thread.start()
        return self

    def _reconnect(self) -> None:
        with self._conn_lock:
            if self._conn:
                try:
                    self._conn.logout()
                except Exception as e:
                    self.log.debug("Logout during reconnect failed: %s", e, exc_info=True)
                self._conn = None
        while not self._stop_event.is_set():
            if self._backoff:
                self.log.info("Reconnecting in %.0fs", self._backoff)
                if self._stop_event.wait(self._backoff):
                    return
            else:
                self.log.info("Reconnecting")
            self._backoff = min(
                max(self._backoff * 2, RECONNECT_MIN_BACKOFF), RECONNECT_MAX_BACKOFF
            )
            try:
                conn = self.folder.connect(readonly=True)
            except Exception as e:
                self.log.warning("Reconnect attempt failed: %s", e, exc_info=True)
                continue
            with self._conn_lock:
                self._conn = conn
            return

    def _idle(self, conn: imapclient.IMAPClient) -> IdleResponse:
        """IDLE until a response, the IDLE timeout, or a stop request; return the responses."""
        conn.idle()
        deadline = time.monotonic() + IDLE_TIMEOUT
        responses: IdleResponse = []
        while not responses and not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            responses = conn.idle_check(min(IDLE_POLL, remaining))
        conn.idle_done()
        return responses

    def _watch(self) -> None:
        try:
            self._watch_loop()
        finally:
            self._close()

    def _watch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                with self._conn_lock:
                    conn = self._conn
                if not conn:
                    self._reconnect()
                    continue
                responses = self._idle(conn)
                if self._stop_event.is_set():
                    break
                self.handler.handle(responses)
                conn.noop()
                self._backoff = 0.0
            except (ssl.SSLEOFError, imapclient.exceptions.ProtocolError) as e:
                if self._stop_event.is_set():
                    break
                self.log.warning("IDLE connection error: %s", e, exc_info=True)
                conn = self._conn
                if conn is not None and hasattr(conn, "sock") and hasattr(conn.sock, "recv"):
                    try:
                        raw = conn.sock.recv(4096, 0)
                        self.log.debug("Last raw socket data: %s", raw)
                    except Exception as sock_exc:
                        self.log.debug("Could not read raw socket data: %s", sock_exc)
                self._reconnect()
            except Exception as e:
                self.log.error("IDLE unexpected error: %s", e, exc_info=True)
                self._reconnect()

    def stop(self) -> None:
        """Ask the monitor to stop; join() waits for it.

        The watching thread notices within IDLE_POLL seconds (or once a running handler
        returns), ends IDLE and logs out itself, so the connection is only used by one thread.
        """
        self.log.info("Stopping")
        self._stop_event.set()
        if self._thread is None or not self._thread.is_alive():
            self._close()

    def _close(self) -> None:
        with self._conn_lock:
            conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.logout()
        except Exception as e:
            self.log.debug("Logout failed: %s", e, exc_info=True)
        self.log.debug("Disconnected from %s:%s", self.account.host, self.account.port)

    def join(self) -> None:
        """Wait for the monitoring thread to finish."""
        if self._thread is not None:
            self._thread.join()

    def signal_handler(self, signum: int, frame: FrameType | None) -> None:
        """Ask the monitor to stop, like stop(); join() waits for it to close its connection.

        For signal.signal(signal.SIGINT, mon.signal_handler).
        """
        self.stop()


class IdleFilterer(IdleMonitor):
    """IdleMonitor that runs account_folder.run(filters) when a Trigger fires."""

    def __init__(
        self,
        account_folder: Folder,
        trigger: IdleTrigger,
        filter_or_filters: Filter | Iterable[Filter],
    ) -> None:
        """Run filter(s) on account_folder whenever trigger.triggers(responses) is True."""

        def handler(responses: IdleResponse) -> None:
            if trigger.triggers(responses):
                account_folder.run(filter_or_filters)

        super().__init__(account_folder, IdleHandler(handler))
