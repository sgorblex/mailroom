"""Base filter interfaces and abstract classes."""

from abc import ABC, abstractmethod

from imap_thingy.accounts import EMailAccount


class Filter(ABC):
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

    @abstractmethod
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

    @abstractmethod
    def apply(self, dry_run: bool = False) -> None:
        """Apply the filter to its account.

        Args:
            dry_run: If True, log actions without executing them (default: False).

        """
        pass
