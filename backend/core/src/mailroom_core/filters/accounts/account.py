"""Email account management and IMAP connection handling."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from imapclient import IMAPClient

from imap_thingy.core import Path
from imap_thingy.filters.filter import Filter
from imap_thingy.get_mail import fetch_mail, search_mail

_logger = logging.getLogger("imap-thingy")


class Folder:
    """An account plus a folder path; supports run(filter) and path navigation."""

    path: Path
    account: Account

    def __init__(self, account: Account, path: Path) -> None:
        """Build a Folder from an account and a path."""
        self.path = path
        self.account = account

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
        try:
            for f in filters:
                self._run_one(f, dry_run, conn)
        finally:
            try:
                conn.logout()
            except Exception as exc:
                self.account.logger.debug("Error during IMAP logout for %s: %s", self, exc, exc_info=True)

    def _run_one(self, f: Filter, dry_run: bool, conn: IMAPClient) -> None:
        selected_msg_ids = search_mail(conn, f.criterion.imap_query)
        if not f.criterion.is_efficient:
            messages = fetch_mail(conn, selected_msg_ids)
            selected_msg_ids = list(f.criterion.select(messages).keys())
        if selected_msg_ids:
            if dry_run:
                _logger.info(f"[Dry-Run] Would select: {selected_msg_ids}")
                _logger.info(f"[Dry-Run] Would execute: {f.action}")
            else:
                _logger.info(f"Selected: {selected_msg_ids}")
                f.action.execute(
                    conn,
                    selected_msg_ids,
                    delimiter=self.account.delimiter,
                )
                _logger.info(f"Executed: {f.action}")

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
        self.logger: logging.Logger = logging.getLogger(f"Account.{self.name}")
        self.inbox = Folder(self, Path([]))

    def connect(self) -> IMAPClient:
        """Create a new IMAP connection. Caller must call conn.logout() when done."""
        conn = IMAPClient(self.host, self.port, ssl=True)
        conn.login(self.username, self.password)
        self.logger.info("Connected")
        return conn

    def __truediv__(self, path: str | Path) -> Folder:
        return Folder(self, path if isinstance(path, Path) else Path(path))

    def __str__(self) -> str:
        return self.name
