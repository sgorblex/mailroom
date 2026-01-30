"""Pre-built filter classes for common use cases."""

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.actions.base import Action
from imap_thingy.filters.actions.imap import MarkAsRead, MoveTo, Unstar
from imap_thingy.filters.criteria.address import BccIs, CcIs, FromIs, ToIs
from imap_thingy.filters.criteria.base import Criterion
from imap_thingy.filters.criteria.flags import IsStarred
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

    def __init__(self, account: EMailAccount, correspondent: str, folder: str, include_cc: bool = True, include_bcc: bool = True, mark_read: bool = True, base_folder: str = "INBOX") -> None:
        """Initialize a move-if-to filter.

        Args:
            account: Email account to filter.
            correspondent: Email address to match in To, CC, or BCC fields.
            folder: Destination folder name.
            include_cc: Whether to check CC field (default: True).
            include_bcc: Whether to check BCC field (default: True).
            mark_read: Whether to mark emails as read before moving (default: True).
            base_folder: Source folder to search in (default: "INBOX").

        """
        criterion: Criterion = ToIs(correspondent)
        if include_cc:
            criterion |= CcIs(correspondent)
        if include_bcc:
            criterion |= BccIs(correspondent)
        action = MarkAsRead() & MoveTo(folder) if mark_read else MoveTo(folder)
        super().__init__(account, criterion, action, base_folder=base_folder)


class ProcessHandledFilter(CriterionFilter):
    """Filter that processes handled (starred) mail and unstars it first.

    This filter is designed for workflows where emails are starred to mark them as
    handled, then unstarred (while still in the source folder) and processed (e.g., moved).
    """

    def __init__(self, account: EMailAccount, criterion: Criterion, action: Action, base_folder: str = "INBOX") -> None:
        """Initialize a process handled filter.

        Args:
            account: Email account to filter.
            criterion: Criterion (condition) to match messages.
            action: Action to perform on matching handled (starred) messages.
            base_folder: Folder to search in (default: "INBOX").

        """
        combined_criterion = criterion & IsStarred()
        combined_action = Unstar() & action
        super().__init__(account, combined_criterion, combined_action, base_folder=base_folder)
