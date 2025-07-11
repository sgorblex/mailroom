from imap_thingy.accounts import EMailAccount, logout_all
from imap_thingy.filters.utils import all_unique_accounts
import threading
from typing import List
from imap_thingy.filters import apply_filters
from imap_thingy.filters import Filter
import logging
from time import sleep
import ssl
import imapclient
import sys

LOGFILE = "imap_thingy.log"
IDLE_TIMEOUT = 29*60 # seconds

class EventsHandler:
    def __init__(self, account: EMailAccount, handler, folder: str = "INBOX"):
        self.account = account
        self.folder = folder
        self.handle = handler
        self._thread = threading.Thread(target=self._watch)

    def __add__(self, other):
        def func(responses):
            self.handle(responses)
            other.handle(responses)
        return EventsHandler(self.account, func, self.folder)

    def signal_handler(self, signum, frame):
        self.stop()

    def _configure_loggers(self, logger_names):
        """
        Configures the given loggers so that debug-level logs go to the logfile, and info-level and above go to stdout.
        """
        # Create handlers
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
        file_handler = logging.FileHandler(LOGFILE)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
        for name in logger_names:
            logger = logging.getLogger(name)
            logger.setLevel(logging.DEBUG)
            logger.handlers.clear()
            logger.addHandler(stream_handler)
            logger.addHandler(file_handler)

    def start(self):
        self._conn = self.account.extra_connection(self.folder, readonly = True)
        self._stop_event = threading.Event()
        # Set up logging to both stdout and file for root and imapclient loggers
        self._configure_loggers(['', 'imapclient'])
        self._thread.start()

    def _reconnect(self):
        if self._conn:
            try:
                self._conn.logout()
            except Exception as e:
                logging.debug(f"EventsHandler for {self.account.name}: Exception during logout in reconnect: {e}", exc_info=True)
        self._conn = self.account.extra_connection(self.folder, readonly = True)

    def _refresh(self):
        logging.info(f"EventsHandler for {self.account.name}: Refreshing IDLE connection...")
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
                logging.warning(f"EventsHandler for {self.account.name}: Connection error: {e}", exc_info=True)
                if hasattr(self._conn, 'sock') and hasattr(self._conn.sock, 'recv'):
                    try:
                        raw = self._conn.sock.recv(4096, 0)
                        logging.debug(f"EventsHandler for {self.account.name}: Last raw socket data: {raw}")
                    except Exception as sock_exc:
                        logging.debug(f"EventsHandler for {self.account.name}: Could not read raw socket data: {sock_exc}")
                self._reconnect()
                sleep(5)
            except Exception as e:
                logging.error(f"EventsHandler for {self.account.name}: Unexpected error: {e}", exc_info=True)
                self._reconnect()
                sleep(5)

    def stop(self):
        logging.info(f"Stopping EventsHandler for {self.account.name}")
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
