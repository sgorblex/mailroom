from imapclient import IMAPClient
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
