from typing import Iterable
from imapclient import IMAPClient
import json

import logging

class EMailAccount:
    def __init__(self, name: str, host: str, port: int, username: str, password: str, address=None, subdir_delimiter="."):
        self.name = name
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self.address = address if address is not None else username
        self.subdir_delimiter = subdir_delimiter
        self._connection: IMAPClient | None = None
        self.logger: logging.Logger = logging.getLogger(f"EMailAccount.{self.name}")

    @property
    def connection(self):
        if not self._connection or self._connection._imap.state == 'LOGOUT':
            self._connection = self._create_connection()
        return self._connection

    def _create_connection(self, base_folder="INBOX", readonly = False):
        conn = IMAPClient(self._host, self._port, ssl=True)
        conn.login(self._username, self._password)
        self.logger.info(f"Connected")
        conn.select_folder(base_folder, readonly=readonly)
        return conn

    def extra_connection(self, base_folder="INBOX", readonly = False):
        return self._create_connection(base_folder, readonly)

    def logout(self):
        if self._connection is not None:
            self._connection.logout()
            self.logger.info(f"Disconnected")

    def __str__(self) -> str:
        return self.name


class GMailAccount(EMailAccount):
    def __init__(self, name: str, username: str, password: str, address=None, host: str = "imap.gmail.com", port: int = 993, subdir_delimiter="/"):
        super().__init__(name, host, port, username, password, address, subdir_delimiter)


def accounts_from_json(json_path: str):
    with open(json_path, 'r') as f:
        account_data = json.load(f)
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

def logout_all(accounts: Iterable):
    for account in accounts:
        account.logout()
