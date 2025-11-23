"""Filter application and management."""

from .interfaces import Filter


def apply_filters(filters: list[Filter], dry_run: bool = False) -> None:
    """Apply a list of filters to their respective accounts.

    Args:
        filters: List of filter objects to apply.
        dry_run: If True, log actions without executing them (default: False).

    """
    for filter in filters:
        filter.apply(dry_run=dry_run)
