"""Account presets (e.g. GMailAccount) with default host/port/delimiter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from imap_thingy.accounts.account import Account

if TYPE_CHECKING:
    import ssl


class GMailAccount(Account):
    """Gmail-specific email account with preconfigured settings."""

    def __init__(
        self,
        name: str,
        username: str,
        password: str,
        address: str | None = None,
        host: str = "imap.gmail.com",
        port: int = 993,
        subdir_delimiter: str = "/",
        ssl_context: ssl.SSLContext | None = None,
        timeout: float | None = None,
    ) -> None:
        """Initialize a Gmail account.

        Args:
            name: Display name for the account.
            username: Gmail username (email address).
            password: Gmail app password.
            address: Email address (defaults to username if not provided).
            host: IMAP server hostname (default: "imap.gmail.com").
            port: IMAP server port (default: 993).
            subdir_delimiter: Character used to delimit folder subdirectories
                (default: "/" for Gmail).
            ssl_context: TLS settings for the connection (default: imapclient's).
            timeout: Seconds to wait for the connection and each socket operation
                (default: wait forever).

        """
        super().__init__(
            name,
            host,
            port,
            username,
            password,
            address,
            subdir_delimiter,
            ssl_context=ssl_context,
            timeout=timeout,
        )
