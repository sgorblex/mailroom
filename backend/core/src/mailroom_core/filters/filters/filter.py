from imap_thingy.account import EMailAccount


class Filter:
    def __init__(self, accounts: EMailAccount | list[EMailAccount]):
        self.accounts = accounts

    def apply(self, dry_run=False):
        raise NotImplementedError
