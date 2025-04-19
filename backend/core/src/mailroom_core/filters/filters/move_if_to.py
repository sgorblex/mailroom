from .base_filter import BaseFilter
from imap_thingy.account import EMailAccount
import logging
logger = logging.getLogger("imap-thingy")


class MoveIfTo(BaseFilter):
    def __init__(self, account: EMailAccount, sender: str, folder: str, mark_as_read = True):
        super().__init__(account)
        assert isinstance(self.accounts, EMailAccount)
        self.account = self.accounts
        self.sender = sender
        self.folder = folder
        self.mark_as_read = mark_as_read

    def apply(self, dry_run=False):
        logger.info(f"Started MoveIfTo in account {self.account}")
        client = self.account.connection
        messages = client.search(['TO', self.sender])
        if not messages: return
        if dry_run:
            if self.mark_as_read:
                print(f"[Dry-Run] Would mark {messages} as read")
            print(f"[Dry-Run] Would move {messages} to {self.folder}")
        else:
            if self.mark_as_read:
                client.add_flags(messages, [b'\\Seen'])
                logger.info(f"Marked {messages} as read")
            client.move(messages, self.folder)
            logger.info(f"Moved {messages} to {self.folder}")
