from .account import EMailAccount, GMailAccount
import json
import os

import logging
logger = logging.getLogger("imap-thingy")

BASE_DIR = os.path.dirname(__file__)
ACCOUNTS_JSON = "accounts.json"

def load_json(filename):
    path = os.path.join(BASE_DIR, '..', filename)
    with open(path, 'r') as f:
        return json.load(f)

def load_account_data():
    return load_json(ACCOUNTS_JSON)

def load_all_accounts():
    account_data = load_account_data()
    accounts = {}
    for acc in account_data:
        email_type = acc["type"] if "type" in acc else "custom"
        if email_type == "gmail":
            accounts[acc["name"]] = GMailAccount(acc["name"], acc["username"], acc["password"])
        elif email_type == "custom":
            address = acc["address"] if "address" in acc else acc["username"]
            accounts[acc["name"]] = EMailAccount(acc["name"], acc["host"], acc["port"], acc["username"], acc["password"], address)
        else: raise NotImplementedError("Unrecognized email preset")
    return accounts

ACCOUNTS = load_all_accounts()

def logout_all():
    for account in ACCOUNTS.values():
        account.logout()
