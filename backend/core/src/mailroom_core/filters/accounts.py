"""Email account management and IMAP connection handling."""

import json
import logging
from collections.abc import Iterable

from imapclient import IMAPClient


class EMailAccount:
    """Represents an email account with IMAP connection management.

    Handles connection creation, reuse, and cleanup for IMAP operations.
    """

    def __init__(self, name: str, host: str, port: int, username: str, password: str, address: str | None = None, subdir_delimiter: str = ".") -> None:
        """Initialize an email account.

        Args:
            name: Display name for the account.
            host: IMAP server hostname.
            port: IMAP server port.
            username: IMAP username.
            password: IMAP password.
            address: Email address (defaults to username if not provided).
            subdir_delimiter: Character used to delimit folder subdirectories (default: ".").

        """
        self.name = name
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self.address = address if address is not None else username
        self.subdir_delimiter = subdir_delimiter
        self._connection: IMAPClient | None = None
        self.logger: logging.Logger = logging.getLogger(f"EMailAccount.{self.name}")

    @property
    def connection(self) -> IMAPClient:
        """Get or create an IMAP connection.

        Returns a cached connection if available and active, otherwise creates a new one.
        """
        if not self._connection or self._connection._imap.state == "LOGOUT":
            self._connection = self._create_connection()
        return self._connection

    def _create_connection(self, base_folder: str = "INBOX", readonly: bool = False) -> IMAPClient:
        conn = IMAPClient(self._host, self._port, ssl=True)
        conn.login(self._username, self._password)
        self.logger.info("Connected")
        conn.select_folder(base_folder, readonly=readonly)
        return conn

    def extra_connection(self, base_folder: str = "INBOX", readonly: bool = False) -> IMAPClient:
        """Create an additional IMAP connection.

        Useful when you need multiple concurrent connections (e.g., for IDLE operations).

        Args:
            base_folder: Folder to select after connection (default: "INBOX").
            readonly: Whether to open folder in readonly mode (default: False).

        Returns:
            A new IMAPClient connection.

        """
        return self._create_connection(base_folder, readonly)

    def logout(self) -> None:
        """Close the main IMAP connection if it exists."""
        if self._connection is not None:
            self._connection.logout()
            self.logger.info("Disconnected")

    def __str__(self) -> str:
        return self.name


class GMailAccount(EMailAccount):
    """Gmail-specific email account with preconfigured settings."""

    def __init__(self, name: str, username: str, password: str, address: str | None = None, host: str = "imap.gmail.com", port: int = 993, subdir_delimiter: str = "/") -> None:
        """Initialize a Gmail account.

        Args:
            name: Display name for the account.
            username: Gmail username (email address).
            password: Gmail app password.
            address: Email address (defaults to username if not provided).
            host: IMAP server hostname (default: "imap.gmail.com").
            port: IMAP server port (default: 993).
            subdir_delimiter: Character used to delimit folder subdirectories (default: "/" for Gmail).

        """
        super().__init__(name, host, port, username, password, address, subdir_delimiter)


def accounts_from_json(json_path: str) -> dict[str, EMailAccount]:
    """Load email accounts from a JSON configuration file.

    The JSON file should be an array of account objects. Each account object must have:
    - "name": Account name (string, required)
    - "type": Account type, either "gmail" or "custom" (string, optional, defaults to "custom")
    - "username": Email username/address (string, required)
    - "password": Email password (string, required)

    For "gmail" type accounts, only the above fields are needed.

    For "custom" type accounts, additional fields are required:
    - "host": IMAP server hostname (string, required)
    - "port": IMAP server port (integer, required)
    - "address": Email address, if different from username (string, optional)

    Example JSON format:
        [
          {
            "name": "my gmail account",
            "type": "gmail",
            "username": "user@gmail.com",
            "password": "app_password"
          },
          {
            "name": "custom account",
            "type": "custom",
            "host": "mail.example.com",
            "port": 993,
            "username": "user@example.com",
            "password": "password"
          }
        ]

    Args:
        json_path: Path to JSON file containing account configurations.

    Returns:
        Dictionary mapping account names to EMailAccount instances.

    Raises:
        NotImplementedError: If an unrecognized email type is specified.

    """
    with open(json_path) as f:
        account_data = json.load(f)
        accounts: dict[str, EMailAccount] = {}
        for acc in account_data:
            email_type = acc["type"] if "type" in acc else "custom"
            if email_type == "gmail":
                accounts[acc["name"]] = GMailAccount(acc["name"], acc["username"], acc["password"])
            elif email_type == "custom":
                address = acc["address"] if "address" in acc else acc["username"]
                accounts[acc["name"]] = EMailAccount(acc["name"], acc["host"], acc["port"], acc["username"], acc["password"], address)
            else:
                raise NotImplementedError("Unrecognized email preset")
        return accounts


def logout_all(accounts: Iterable[EMailAccount]) -> None:
    """Log out from all provided email accounts."""
    for account in accounts:
        account.logout()
