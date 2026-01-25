"""Utility functions for working with filters."""

from collections.abc import Iterable

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.interfaces import Filter


def all_unique_accounts(filters: Iterable[Filter]) -> set[EMailAccount]:
    """Extract all unique email accounts from a collection of filters.

    Args:
        filters: Iterable of filter objects.

    Returns:
        Set of unique email accounts used by the filters.

    """
    return set(account for f in filters for account in f.accounts)


def apply_filters(filters: list[Filter], dry_run: bool = False) -> None:
    """Apply a list of filters to their respective accounts.

    Args:
        filters: List of filter objects to apply.
        dry_run: If True, log actions without executing them (default: False).

    """
    for filt in filters:
        filt.apply(dry_run=dry_run)
