from imap_thingy.accounts import EMailAccount, logout_all
from imap_thingy.filters.utils import all_unique_accounts
import threading
from typing import List
from imap_thingy.filters import apply_filters
from imap_thingy.filters import Filter
from logging import getLogger
from time import sleep
import ssl
import imapclient

LOGFILE = "imap_thingy.log"
IDLE_TIMEOUT = 25*60 # seconds, max 29 minutes
# IDLE_TIMEOUT = 30 # seconds, mostly for debugging

class EventsHandler:
    def __init__(self, account: EMailAccount, handler, folder: str = "INBOX"):
        self.account = account
        self.folder = folder
        self.handle = handler
        self._thread = threading.Thread(target=self._watch)
        self.logger = getLogger(f"EventsHandler.{self.account.name}")

    def __add__(self, other):
        def func(responses):
            self.handle(responses)
            other.handle(responses)
        return EventsHandler(self.account, func, self.folder)

    def signal_handler(self, signum, frame):
        self.stop()

    def start(self):
        self._conn = self.account.extra_connection(self.folder, readonly = True)
        self._stop_event = threading.Event()
        self._thread.start()

    def _reconnect(self):
        if self._conn:
            try:
                self._conn.logout()
            except Exception as e:
                self.logger.debug(f"Exception during logout in reconnect: {e}", exc_info=True)
        self._conn = self.account.extra_connection(self.folder, readonly = True)

    def _refresh(self):
        self.logger.info(f"Refreshing IDLE connection...")
        self._conn.idle_done()
        sleep(1)
        self._conn.idle()

    def _watch(self):
        while not self._stop_event.is_set():
            try:
                if not self._conn:
                    self._reconnect()
                    sleep(5)
                    continue
                self._conn.idle()
                responses = self._conn.idle_check(IDLE_TIMEOUT)
                self._conn.idle_done()  # Ensure IDLE is ended before any other command
                self.handle(responses)
                self._conn.noop()
            except (ssl.SSLEOFError, imapclient.exceptions.ProtocolError) as e:
                if self._stop_event.is_set():
                    break
                self.logger.warning(f"Connection error: {e}", exc_info=True)
                if hasattr(self._conn, 'sock') and hasattr(self._conn.sock, 'recv'):
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

    def stop(self):
        self.logger.info("Stopping")
        self._stop_event.set()
        try:
            self._conn.idle_done()
        except ssl.SSLWantReadError:
            pass
        try:
            self._conn.logout()
        except ssl.SSLWantReadError:
            pass

    def join(self):
        self._thread.join()


## some simple handlers
def print_responses():
    def func(responses):
        for r in responses: print(r)
    return func

def filter_when_anything(filters: List[Filter]):
    def func(responses):
        apply_filters(filters)
        logout_all(all_unique_accounts(filters))
    return func

def filter_when_newmail(filters: List[Filter]):
    def func(responses):
        run = False
        for r in responses:
            if r[1] == b'EXISTS': run = True
        if run:
            apply_filters(filters)
            logout_all(all_unique_accounts(filters))
    return func

def filter_when_read(filters: List[Filter]):
    def func(responses):
        run = False
        for r in responses:
            if r[1] == b'FETCH' and r[2] == (b'FLAGS', (b'\\Seen',)): run = True
        if run:
            apply_filters(filters)
            logout_all(all_unique_accounts(filters))
    return func
