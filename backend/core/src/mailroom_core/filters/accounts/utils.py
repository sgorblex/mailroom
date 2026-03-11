"""Utilities for managing email accounts."""

import json

from imap_thingy.accounts.account import Account
from imap_thingy.accounts.presets import GMailAccount


def accounts_from_json(json_path: str) -> dict[str, Account]:
    """Load email accounts from a JSON configuration file.

    The JSON file should be an array of account objects. Each account object must have:
    - "name": Account name (string, required)
    - "type": Account type, either "gmail" or "custom" (string, optional, defaults to "custom")
    - "username": Email username/address (string, required)
    - "password": Email password (string, required)

    For "gmail" type accounts, only the above fields are needed.

    For "custom" type accounts, additional fields are required:
    - "host": IMAP server hostname (string, required)
    - "port": IMAP server port (integer, required)
    - "address": Email address, if different from username (string, optional)

    Example JSON format:
        [
          {
            "name": "my gmail account",
            "type": "gmail",
            "username": "user@gmail.com",
            "password": "app_password"
          },
          {
            "name": "custom account",
            "type": "custom",
            "host": "mail.example.com",
            "port": 993,
            "username": "user@example.com",
            "password": "password"
          }
        ]

    Args:
        json_path: Path to JSON file containing account configurations.

    Returns:
        Dictionary mapping account names to Account instances.

    Raises:
        NotImplementedError: If an unrecognized email type is specified.

    """
    with open(json_path) as f:
        account_data = json.load(f)
        accounts: dict[str, Account] = {}
        for acc in account_data:
            email_type = acc["type"] if "type" in acc else "custom"
            if email_type == "gmail":
                accounts[acc["name"]] = GMailAccount(acc["name"], acc["username"], acc["password"])
            elif email_type == "custom":
                address = acc["address"] if "address" in acc else acc["username"]
                accounts[acc["name"]] = Account(acc["name"], acc["host"], acc["port"], acc["username"], acc["password"], address)
            else:
                raise NotImplementedError("Unrecognized email preset")
        return accounts
