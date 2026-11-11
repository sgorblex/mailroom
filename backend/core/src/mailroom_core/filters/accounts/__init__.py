"""Email accounts, folders, and account-folder run API."""

from imap_thingy.accounts.account import (
    Account,
    Folder,
)
from imap_thingy.accounts.utils import accounts_from_json
from imap_thingy.core import Path

__all__ = [
    "Account",
    "Folder",
    "Path",
    "accounts_from_json",
]
