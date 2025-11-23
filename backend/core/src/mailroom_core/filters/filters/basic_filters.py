"""Pre-built filter classes for common use cases."""

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.criterion_filter import CriterionFilter, bcc_contains_is, cc_contains_is, from_is, mark_as_read, move_to, to_contains_is


class MoveIfFromFilter(CriterionFilter):
    """Filter that moves emails from a specific sender to a folder."""

    def __init__(self, account: EMailAccount, sender: str, folder: str, mark_read: bool = True, base_folder: str = "INBOX") -> None:
        """Initialize a move-if-from filter.

        Args:
            account: Email account to filter.
            sender: Email address of the sender to match.
            folder: Destination folder name.
            mark_read: Whether to mark emails as read before moving (default: True).
            base_folder: Source folder to search in (default: "INBOX").

        """
        action = mark_as_read() & move_to(folder) if mark_read else move_to(folder)
        super().__init__(account, from_is(sender), action, base_folder=base_folder)


class MoveIfToFilter(CriterionFilter):
    """Filter that moves emails addressed to a specific recipient to a folder."""

    def __init__(self, account: EMailAccount, correspondant: str, folder: str, include_cc: bool = True, include_bcc: bool = True, mark_read: bool = True) -> None:
        """Initialize a move-if-to filter.

        Args:
            account: Email account to filter.
            correspondant: Email address to match in To, CC, or BCC fields.
            folder: Destination folder name.
            include_cc: Whether to check CC field (default: True).
            include_bcc: Whether to check BCC field (default: True).
            mark_read: Whether to mark emails as read before moving (default: True).

        """
        criterion = to_contains_is(correspondant)
        if include_cc:
            criterion |= cc_contains_is(correspondant)
        if include_bcc:
            criterion |= bcc_contains_is(correspondant)
        action = mark_as_read() & move_to(folder) if mark_read else move_to(folder)
        super().__init__(account, criterion, action)
