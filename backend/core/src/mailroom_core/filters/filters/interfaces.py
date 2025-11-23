"""Base filter interfaces and abstract classes."""

from imap_thingy.accounts import EMailAccount


class Filter:
    """Base class for all email filters.

    Filters operate on one or more email accounts and can be applied to perform
    actions like moving, deleting, or marking emails.
    """

    def __init__(self, accounts: list[EMailAccount]) -> None:
        """Initialize a filter.

        Args:
            accounts: List of email accounts this filter operates on.

        """
        self.accounts = accounts

    def apply(self, dry_run: bool = False) -> None:
        """Apply the filter to its accounts.

        Args:
            dry_run: If True, log actions without executing them (default: False).

        """
        pass


class OneAccountFilter(Filter):
    """Base class for filters that operate on a single email account."""

    def __init__(self, account: EMailAccount) -> None:
        """Initialize a single-account filter.

        Args:
            account: Email account this filter operates on.

        """
        super().__init__([account])

    @property
    def account(self) -> EMailAccount:
        """Get the email account this filter operates on."""
        return self.accounts[0]


class OneAccountOneFolderFilter(OneAccountFilter):
    """Base class for filters that operate on a single account and folder."""

    def __init__(self, account: EMailAccount, base_folder: str = "INBOX") -> None:
        """Initialize a single-account, single-folder filter.

        Args:
            account: Email account this filter operates on.
            base_folder: IMAP folder to operate on (default: "INBOX").

        """
        super().__init__(account)
        self.base_folder = base_folder

    def apply(self, dry_run: bool = False) -> None:
        """Apply the filter, selecting the base folder first.

        Args:
            dry_run: If True, log actions without executing them (default: False).

        """
        super().apply(dry_run)
        self.account.connection.select_folder(self.base_folder, readonly=False)
