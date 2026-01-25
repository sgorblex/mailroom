"""Criterion-based filter implementation."""

import logging

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.actions.base import Action
from imap_thingy.filters.criteria.base import Criterion
from imap_thingy.filters.interfaces import OneAccountFilter

logger = logging.getLogger("imap-thingy")


class CriterionFilter(OneAccountFilter):
    """Filter that applies an action to messages matching a criterion."""

    def __init__(self, account: EMailAccount, criterion: Criterion, action: Action, base_folder: str = "INBOX") -> None:
        """Initialize a criterion filter.

        Args:
            account: Email account to filter.
            criterion: Criterion (condition) to match messages.
            action: Action to perform on matching messages.
            base_folder: Folder to search in (default: "INBOX").

        """
        super().__init__(account)
        self.base_folder = base_folder
        self.criterion = criterion
        self.action = action

    def apply(self, dry_run: bool = False) -> None:
        """Apply the filter to matching messages.

        Args:
            dry_run: If True, log actions without executing them (default: False).

        """
        self.account.connection.select_folder(self.base_folder, readonly=False)
        msgs = self.criterion.filter(self.account.connection)
        if msgs:
            if dry_run:
                logger.info(f"[Dry-Run] Would select: {msgs}")
                logger.info(f"[Dry-Run] Would execute: {self.action}")
            else:
                logger.info(f"Selected: {msgs}")
                self.action.execute(self.account, msgs)
                logger.info(f"Executed: {self.action}")
