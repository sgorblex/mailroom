from imap_thingy.accounts import EMailAccount
import threading
from typing import List
from imap_thingy.filters import apply_filters
from imap_thingy.filters import Filter

TIMEOUT = 10

class ResponseHandler:
    def __init__(self, func):
        self.func = func

    def handle(self, responses: List):
        self.func(responses)

    def __add__(self, other):
        def func(responses):
            self.func(responses)
            other.func(responses)
        return ResponseHandler(func)

class EventsHandler:
    def __init__(self, account: EMailAccount, handler: ResponseHandler, folder: str = "INBOX"):
        self.account = account
        self.folder = folder
        self.handler = handler
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._watch)

    def start(self):
        self._conn = self.account.extra_connection(self.folder, readonly = True)
        self._thread.start()

    def _watch(self):
        self._conn.idle()
        while not self._stop_event.is_set():
            responses = self._conn.idle_check(TIMEOUT)
            self.handler.handle(responses)
        self._conn.idle_done()

    def stop(self):
        self._stop_event.set()

    def join(self):
        self._thread.join()


## some simple handlers
def print_responses():
    def func(responses):
        for r in responses: print(r)
    return ResponseHandler(func)

def filter_when_anything(filters: List[Filter]):
    def func(responses):
        apply_filters(filters)
    return ResponseHandler(func)

def filter_when_newmail(filters: List[Filter]):
    def func(responses):
        run = False
        for r in responses:
            if r[1] == b'EXISTS': run = True
        if run: apply_filters(filters)
    return ResponseHandler(func)

def filter_when_read(filters: List[Filter]):
    def func(responses):
        run = False
        for r in responses:
            if r[1] == b'FETCH' and r[2] == (b'FLAGS', (b'\\Seen',)): run = True
        if run: apply_filters(filters)
    return ResponseHandler(func)
