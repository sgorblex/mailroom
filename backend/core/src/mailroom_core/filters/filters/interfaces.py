from imap_thingy.accounts import EMailAccount


class Filter:
    def __init__(self, accounts: list[EMailAccount]) -> None:
        self.accounts = accounts

    def apply(self, dry_run: bool = False) -> None:
        pass


class OneAccountFilter(Filter):
    def __init__(self, account: EMailAccount) -> None:
        super().__init__([account])

    @property
    def account(self) -> EMailAccount:
        return self.accounts[0]

class OneAccountOneFolderFilter(OneAccountFilter):
    def __init__(self, account: EMailAccount, base_folder: str = "INBOX") -> None:
        super().__init__(account)
        self.base_folder = base_folder

    def apply(self, dry_run: bool = False) -> None:
        super().apply(dry_run)
        self.account.connection.select_folder(self.base_folder, readonly=False)
