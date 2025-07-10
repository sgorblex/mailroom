from typing import Iterable


def all_unique_accounts(filters: Iterable):
    return set(account for f in filters for account in f.accounts)
