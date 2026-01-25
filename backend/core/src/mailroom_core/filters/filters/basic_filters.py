"""Pre-built filter classes for common use cases."""

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.actions.imap import MarkAsRead, MoveTo
from imap_thingy.filters.criteria.address import BccIs, CcIs, FromIs, ToIs
from imap_thingy.filters.criteria.base import Criterion
from imap_thingy.filters.criterion import CriterionFilter


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
        action = MarkAsRead() & MoveTo(folder) if mark_read else MoveTo(folder)
        super().__init__(account, FromIs(sender), action, base_folder=base_folder)


class MoveIfToFilter(CriterionFilter):
    """Filter that moves emails addressed to a specific recipient to a folder."""

    def __init__(self, account: EMailAccount, correspondent: str, folder: str, include_cc: bool = True, include_bcc: bool = True, mark_read: bool = True) -> None:
        """Initialize a move-if-to filter.

        Args:
            account: Email account to filter.
            correspondent: Email address to match in To, CC, or BCC fields.
            folder: Destination folder name.
            include_cc: Whether to check CC field (default: True).
            include_bcc: Whether to check BCC field (default: True).
            mark_read: Whether to mark emails as read before moving (default: True).

        """
        criterion: Criterion = ToIs(correspondent)
        if include_cc:
            criterion |= CcIs(correspondent)
        if include_bcc:
            criterion |= BccIs(correspondent)
        action = MarkAsRead() & MoveTo(folder) if mark_read else MoveTo(folder)
        super().__init__(account, criterion, action)
