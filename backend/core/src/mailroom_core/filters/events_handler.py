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

    def start(self):
        self._conn = self.account.extra_connection(self.folder, readonly = True)
        self._stop_event = threading.Event()
        self._thread.start()

    def _reconnect(self):
        if self._conn:
            self._conn.logout()
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
                self.handle(responses)
                self._conn.noop()
            except (ssl.SSLEOFError, imapclient.exceptions.ProtocolError) as e:
                if self._stop_event.is_set():
                    break
                logging.warning(f"EventsHandler for {self.account.name}: Connection error: {e}")
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
