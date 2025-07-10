from imap_thingy.accounts import EMailAccount


class Filter:
    def __init__(self, accounts: list[EMailAccount]):
        self.accounts = accounts

    def apply(self, dry_run=False):
        pass


class OneAccountFilter(Filter):
    def __init__(self, account: EMailAccount):
        super().__init__([account])

    @property
    def account(self):
        return self.accounts[0]

class OneAccountOneFolderFilter(OneAccountFilter):
    def __init__(self, account: EMailAccount, base_folder="INBOX"):
        super().__init__(account)
        self.base_folder = base_folder

    def apply(self, dry_run=False):
        super().apply(dry_run)
        self.account.connection.select_folder(self.base_folder, readonly=False)
