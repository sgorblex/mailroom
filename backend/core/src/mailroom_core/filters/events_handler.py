"""IMAP IDLE event handling for real-time email monitoring."""

import ssl
import threading
from collections.abc import Callable
from logging import getLogger
from time import sleep
from types import FrameType

import imapclient

from imap_thingy.accounts import EMailAccount, logout_all
from imap_thingy.filters import Filter, apply_filters
from imap_thingy.filters.utils import all_unique_accounts

LOGFILE = "imap_thingy.log"
IDLE_TIMEOUT = 25 * 60  # seconds, max 29 minutes

# Type alias for IMAP idle responses
IdleResponse = list[tuple[int, bytes, tuple[bytes, ...] | None]]


class EventsHandler:
    """Handles IMAP IDLE events for real-time email monitoring.

    Monitors an IMAP folder for changes and calls a handler function when events occur.
    Runs in a separate thread to avoid blocking.
    """

    def __init__(self, account: EMailAccount, handler: Callable[[IdleResponse], None], folder: str = "INBOX") -> None:
        """Initialize an events handler.

        Args:
            account: Email account to monitor.
            handler: Function to call when IMAP events occur.
            folder: IMAP folder to monitor (default: "INBOX").

        """
        self.account = account
        self.folder = folder
        self.handle = handler
        self._thread = threading.Thread(target=self._watch)
        self.logger = getLogger(f"EventsHandler.{self.account.name}")

    def __add__(self, other: "EventsHandler") -> "EventsHandler":
        def func(responses: IdleResponse) -> None:
            self.handle(responses)
            other.handle(responses)

        return EventsHandler(self.account, func, self.folder)

    def signal_handler(self, signum: int, frame: FrameType | None) -> None:
        """Handle system signals by stopping the event handler."""
        self.stop()

    def start(self) -> None:
        """Start monitoring for IMAP events in a background thread."""
        self._conn = self.account.extra_connection(self.folder, readonly=True)
        self._stop_event = threading.Event()
        self._thread.start()

    def _reconnect(self) -> None:
        if self._conn:
            try:
                self._conn.logout()
            except Exception as e:
                self.logger.debug(f"Exception during logout in reconnect: {e}", exc_info=True)
        self._conn = self.account.extra_connection(self.folder, readonly=True)

    def _refresh(self) -> None:
        self.logger.info("Refreshing IDLE connection...")
        self._conn.idle_done()
        sleep(1)
        self._conn.idle()

    def _watch(self) -> None:
        while not self._stop_event.is_set():
            try:
                if not self._conn:
                    self._reconnect()
                    sleep(5)
                    continue
                self._conn.idle()
                responses = self._conn.idle_check(IDLE_TIMEOUT)
                self._conn.idle_done()
                self.handle(responses)
                self._conn.noop()
            except (ssl.SSLEOFError, imapclient.exceptions.ProtocolError) as e:
                if self._stop_event.is_set():
                    break
                self.logger.warning(f"Connection error: {e}", exc_info=True)
                if hasattr(self._conn, "sock") and hasattr(self._conn.sock, "recv"):
                    try:
                        raw = self._conn.sock.recv(4096, 0)
                        self.logger.debug(f"Last raw socket data: {raw}")
                    except Exception as sock_exc:
                        self.logger.debug(f"Could not read raw socket data: {sock_exc}")
                self._reconnect()
                sleep(5)
            except Exception as e:
                self.logger.error(f"Unexpected error: {e}", exc_info=True)
                self._reconnect()
                sleep(5)

    def stop(self) -> None:
        """Stop monitoring and close the IMAP connection."""
        self.logger.info("Stopping")
        self._stop_event.set()
        try:
            self._conn.idle_done()
        except ssl.SSLWantReadError:
            # Safe to ignore: occurs if connection is already closed or in non-blocking state.
            pass
        try:
            self._conn.logout()
        except ssl.SSLWantReadError:
            # Safe to ignore: occurs if connection is already closed or in non-blocking state.
            pass

    def join(self) -> None:
        """Wait for the monitoring thread to finish."""
        self._thread.join()


# Handler functions for IMAP IDLE events
def print_responses() -> Callable[[IdleResponse], None]:
    """Create a handler that prints all IMAP IDLE responses to stdout."""

    def func(responses: IdleResponse) -> None:
        for r in responses:
            print(r)

    return func


def filter_when_anything(filters: list[Filter]) -> Callable[[IdleResponse], None]:
    """Create a handler that applies filters on any IMAP event."""

    def func(responses: IdleResponse) -> None:
        apply_filters(filters)
        logout_all(all_unique_accounts(filters))

    return func


def filter_when_newmail(filters: list[Filter]) -> Callable[[IdleResponse], None]:
    """Create a handler that applies filters only when new mail arrives."""

    def func(responses: IdleResponse) -> None:
        run = False
        for r in responses:
            if r[1] == b"EXISTS":
                run = True
        if run:
            apply_filters(filters)
            logout_all(all_unique_accounts(filters))

    return func


def filter_when_read(filters: list[Filter]) -> Callable[[IdleResponse], None]:
    """Create a handler that applies filters when mail is marked as read."""

    def func(responses: IdleResponse) -> None:
        run = False
        for r in responses:
            if r[1] == b"FETCH" and r[2] == (b"FLAGS", (b"\\Seen",)):
                run = True
        if run:
            apply_filters(filters)
            logout_all(all_unique_accounts(filters))

    return func
