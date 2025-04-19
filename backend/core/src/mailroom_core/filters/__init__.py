from .account import EMailAccount
import json
import os

import logging
logger = logging.getLogger("imap-thingy")


BASE_DIR = os.path.dirname(__file__)

def load_json(filename):
    path = os.path.join(BASE_DIR, '..', filename)
    with open(path, 'r') as f:
        return json.load(f)

def load_account_data():
    return load_json('accounts.json')

def load_all_accounts():
    account_data = load_account_data()
    accounts = {}
    for acc in account_data:
        accounts[acc["name"]] = EMailAccount(acc["name"], acc["host"], acc["port"], acc["username"], acc["password"], acc["address"])
    return accounts

ACCOUNTS = load_all_accounts()

def logout_all():
    for account in ACCOUNTS.values():
        account.logout()
