from collections.abc import Iterable

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.interfaces import Filter


def all_unique_accounts(filters: Iterable[Filter]) -> set[EMailAccount]:
    return set(account for f in filters for account in f.accounts)
