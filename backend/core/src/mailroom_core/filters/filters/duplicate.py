"""Filter for detecting and removing duplicate emails."""

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.actions.imap import Trash
from imap_thingy.filters.criteria.duplicate import DuplicateCriterion
from imap_thingy.filters.criterion import CriterionFilter


class DuplicateFilter(CriterionFilter):
    """Filter that removes duplicate emails, keeping only one copy.

    Duplicates are identified by:
    1. Message-ID header (primary method, most reliable)
    2. Subject + From + Date (fallback if Message-ID is missing)

    For each group of duplicates, the first email encountered is kept,
    and the rest are moved to trash.
    """

    def __init__(self, account: EMailAccount, base_folder: str = "INBOX") -> None:
        """Initialize a duplicate filter.

        Args:
            account: Email account to filter.
            base_folder: Source folder to search in (default: "INBOX").

        """
        super().__init__(account, DuplicateCriterion(), Trash(), base_folder=base_folder)
