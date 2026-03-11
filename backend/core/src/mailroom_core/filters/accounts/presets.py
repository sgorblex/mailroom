"""Account presets (e.g. GMailAccount) with default host/port/delimiter."""

from imap_thingy.accounts.account import Account


class GMailAccount(Account):
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
