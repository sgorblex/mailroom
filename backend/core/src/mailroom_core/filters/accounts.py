from imapclient import IMAPClient
import json

import logging
logger = logging.getLogger("imap-thingy")

class EMailAccount:
    def __init__(self, name: str, host: str, port: int, username: str, password: str, address=None, subdir_delimiter="."):
        self.name = name
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self.address = address if address is not None else username
        self.subdir_delimiter = subdir_delimiter
        self._connection = None

    @property
    def connection(self):
        if self._connection is None:
            self._connection = self._create_connection()
        return self._connection

    def _create_connection(self):
        conn = IMAPClient(self._host, self._port, ssl=True)
        conn.login(self._username, self._password)
        logger.info(f"Connected to {self}")
        conn.select_folder('INBOX', readonly=False)
        return conn

    def logout(self):
        if self._connection is not None:
            self._connection.logout()
            logger.info(f"Disconnected from {self}")

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

def logout_all(accounts: dict[str,EMailAccount]):
    for account in accounts.values():
        account.logout()
