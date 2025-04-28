from imap_thingy.accounts import EMailAccount


class Filter:
    def __init__(self, accounts: EMailAccount | list[EMailAccount]):
        self.accounts = accounts

    def apply(self, dry_run=False):
        raise NotImplementedError


class OneAccountFilter(Filter):
    def __init__(self, account: EMailAccount):
        super().__init__(account)
        assert isinstance(account, EMailAccount)
        self.account = self.accounts


class OneAccountOneFolderFilter(OneAccountFilter):
    def __init__(self, account: EMailAccount, base_folder="INBOX"):
        super().__init__(account)
        self.base_folder = base_folder

    def apply(self, dry_run=False):
        self.account.connection.select_folder(self.base_folder, readonly=False)
        super().apply(dry_run)
