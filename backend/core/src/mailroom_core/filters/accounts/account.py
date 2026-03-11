"""Email account management and IMAP connection handling."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from imapclient import IMAPClient

from imap_thingy.core import Path
from imap_thingy.filters.filter import Filter
from imap_thingy.get_mail import fetch_mail, search_mail


class Folder:
    """An account plus a folder path; supports run(filter) and path navigation."""

    path: Path
    account: Account

    def __init__(self, account: Account, path: Path) -> None:
        """Build a Folder from an account and a path."""
        self.path = path
        self.account = account
        self.log = account.log.getChild(self.imap_name())

    def imap_name(self) -> str:
        """Return the IMAP folder name (e.g. INBOX or delimiter-joined path)."""
        return self.path.as_string(self.account.delimiter) or "INBOX"

    def __str__(self) -> str:
        return f"{self.account.name}.{self.imap_name()}"

    def connect(self, readonly: bool = False) -> IMAPClient:
        """Open IMAP connection and select this folder."""
        conn = self.account.connect()
        conn.select_folder(self.imap_name(), readonly=readonly)
        return conn

    def run(
        self,
        filter_or_filters: Filter | Iterable[Filter],
        dry_run: bool = False,
    ) -> None:
        """Run one or more filters on this folder using a shared IMAP connection.

        A single IMAP connection is opened and this folder is selected once per
        call to run(). All filters passed in filter_or_filters are then executed
        sequentially using that same connection and session state, before the
        connection is logged out.

        Because the connection is shared, actions executed by earlier filters
        can affect subsequent filters via changes to server or session state
        (for example, moving messages to other folders).
        """
        filters = [filter_or_filters] if isinstance(filter_or_filters, Filter) else list(filter_or_filters)
        if not filters:
            return
        conn = self.connect()
        self.log.info("running %s filter(s)", len(filters))
        try:
            for f in filters:
                self._run_one(f, dry_run, conn)
        finally:
            try:
                conn.logout()
                self.log.debug("Disconnected from %s:%s", self.account.host, self.account.port)
            except Exception as exc:
                self.log.debug("Logout failed: %s", exc, exc_info=True)

    def _run_one(self, f: Filter, dry_run: bool, conn: IMAPClient) -> None:
        selected_msg_ids = search_mail(conn, f.criterion.imap_query)
        if not f.criterion.is_efficient:
            messages = fetch_mail(conn, selected_msg_ids)
            selected_msg_ids = list(f.criterion.select(messages).keys())
        if selected_msg_ids:
            _sample_size = 10
            _sample = selected_msg_ids[:_sample_size]
            _truncated = f" (first {len(_sample)} shown)" if len(selected_msg_ids) > _sample_size else ""
            if dry_run:
                self.log.info(
                    "[Dry-Run] would select %s message(s)%s and execute %s: %s",
                    len(selected_msg_ids),
                    _truncated,
                    f.action,
                    _sample,
                )
            else:
                self.log.info(
                    "selected %s message(s)%s: %s",
                    len(selected_msg_ids),
                    _truncated,
                    _sample,
                )
                f.action.execute(
                    conn,
                    selected_msg_ids,
                    delimiter=self.account.delimiter,
                )
                self.log.info("executed %s", f.action)
        else:
            self.log.debug("no messages matched for action %s", f.action)

    def __truediv__(self, path: str) -> Folder:
        return Folder(self.account, (self.path / path))


class Account:
    """Represents an email account with IMAP connection management.

    Handles connection creation, reuse, and cleanup for IMAP operations.
    """

    def __init__(self, name: str, host: str, port: int, username: str, password: str, address: str | None = None, delimiter: str = ".") -> None:
        """Initialize an email account.

        Args:
            name: Display name for the account.
            host: IMAP server hostname.
            port: IMAP server port.
            username: IMAP username.
            password: IMAP password.
            address: Email address (defaults to username if not provided).
            delimiter: Character used to delimit folder subdirectories (default: ".").

        """
        self.name = name
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.address = address if address is not None else username
        self.delimiter = delimiter
        self.log: logging.Logger = logging.getLogger(__name__).getChild(self.name)
        self.inbox = Folder(self, Path([]))

    def connect(self) -> IMAPClient:
        """Create a new IMAP connection. Caller must call conn.logout() when done."""
        conn = IMAPClient(self.host, self.port, ssl=True)
        conn.login(self.username, self.password)
        self.log.debug("Connected to %s:%s", self.host, self.port)
        return conn

    def __truediv__(self, path: str | Path) -> Folder:
        return Folder(self, path if isinstance(path, Path) else Path(path))

    def __str__(self) -> str:
        return self.name
