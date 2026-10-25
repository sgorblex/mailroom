"""The folder a rule set runs on, through imap-thingy (the account from
``mailroom.accounts.mail.mailbox_account``).

The API's dry run and the worker's runs both reach mail this way.
"""

from __future__ import annotations

import logging
import threading
from contextlib import suppress
from typing import TYPE_CHECKING

from imap_thingy.accounts import Folder
from imap_thingy.core import All, IMAPQuery, Path, Q
from imap_thingy.filters import Criterion

from mailroom.accounts.mail import hierarchy_delimiter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from imap_thingy.accounts import Account
    from imapclient import IMAPClient

logger = logging.getLogger(__name__)


class RuleSetFolder(Folder):
    """imap-thingy's folder at ``segments``, joined with the delimiter the server reports.

    Every connection asks the server for its delimiter before selecting (imap-thingy's default is
    ``.``), and remembers how many messages the folder holds. ``abort()``, from any thread, closes
    the open connections and refuses new ones: the run in progress then fails.
    """

    def __init__(self, account: Account, segments: Sequence[str]) -> None:
        super().__init__(account, Path(list(segments)))
        self.message_count: int | None = None
        """The folder's message count at the last select (``EXISTS``)."""
        self.aborted = False
        self._lock = threading.Lock()
        self._connections: list[IMAPClient] = []

    def connect(self, readonly: bool = False) -> IMAPClient:
        conn = self.account.connect()
        with self._lock:
            if self.aborted:
                _close(conn)
                msg = "the run was stopped"
                raise TimeoutError(msg)
            self._connections.append(conn)
        try:
            delimiter = hierarchy_delimiter(conn)
            if delimiter:
                self.account.delimiter = delimiter
            selected = conn.select_folder(self.imap_name(), readonly=readonly)
        except BaseException:
            with suppress(Exception):
                conn.logout()
            raise
        exists = selected.get(b"EXISTS")
        self.message_count = exists if isinstance(exists, int) else None
        return conn

    def abort(self) -> None:
        with self._lock:
            self.aborted = True
            connections = list(self._connections)
        for conn in connections:
            _close(conn)

    def newest(self, count: int) -> Criterion:
        """Matches the folder's newest ``count`` messages only (by sequence number, as selected
        when the search runs): ``newest(n) & criterion`` bounds what a run looks at."""

        def window() -> IMAPQuery:
            total = self.message_count or 0
            return All if total <= count else Q(f"{total - count + 1}:*")

        return Criterion(lambda _message: True, IMAPQuery(IMAPQuery.Deferred(window)), True)


def _close(conn: IMAPClient) -> None:
    """Close the socket under a connection, even while another thread reads from it."""
    with suppress(Exception):
        conn.shutdown()
