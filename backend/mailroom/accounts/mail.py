"""Reaching an account's mailbox over IMAP, through the filter engine.

Every connection verifies TLS: against the system CAs, plus the one ``MAILROOM_MAIL_CA_FILE``
names (the dev mail backends' CA, ``dev/.state/certs/ca.pem``). Failures are reported by kind,
never with the password.
"""

from __future__ import annotations

import imaplib
import logging
import os
import ssl
import threading
from contextlib import suppress
from typing import TYPE_CHECKING, Final

from imapclient.exceptions import LoginError

from mailroom.accounts.store import account_password, load_accounts
from mailroom_core.filters import Account
from mailroom_core.mail import MESSAGES, mail_error

if TYPE_CHECKING:
    from collections.abc import Callable

    from imapclient import IMAPClient

    from mailroom_core.config import AccountConfig
    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.store import AccountStore

logger = logging.getLogger(__name__)

MAIL_CA_FILE_ENV: Final = "MAILROOM_MAIL_CA_FILE"
IMAP_TIMEOUT_SECONDS: Final = 20.0
"""For connecting and for every read: a test or folder listing never hangs a request."""


def mail_ssl_context() -> ssl.SSLContext:
    """The TLS settings of every mail connection: system CAs, plus ``MAILROOM_MAIL_CA_FILE``."""
    context = ssl.create_default_context()
    ca_file = os.environ.get(MAIL_CA_FILE_ENV, "").strip()
    if ca_file:
        try:
            context.load_verify_locations(cafile=ca_file)
        except (OSError, ssl.SSLError) as exc:
            logger.warning(
                "MAILROOM_MAIL_CA_FILE could not be loaded (%s): trusting the system CAs only.",
                type(exc).__name__,
            )
    return context


class MailFailure(Exception):  # noqa: N818 -- the outcome of a mail check, not a bug
    """The mailbox could not be reached or would not let us in. ``code`` is mailroom-core's mail
    error code (``mailroom_core.mail.MailErrorCode``) or one of the app's own
    (``secrets_key_missing``, ``password_missing``, ``starttls_unsupported``); ``message`` is
    fixed wording, never the server's."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def imap_account(account: AccountConfig, password: str, ssl_context: ssl.SSLContext) -> Account:
    """The filter engine's view of ``account``. Raises ``MailFailure`` for an IMAP endpoint it
    cannot reach (STARTTLS: the engine connects with implicit TLS only)."""
    if account.imap.tls != "implicit":
        msg = "Only implicit TLS (IMAPS, usually port 993) is supported for IMAP."
        raise MailFailure("starttls_unsupported", msg)
    return Account(
        account.id,
        account.imap.host,
        account.imap.port,
        account.login,
        password,
        ssl_context=ssl_context,
        timeout=IMAP_TIMEOUT_SECONDS,
    )


def mailbox_account(
    store: AccountStore,
    subkey: SecretsSubkey | None,
    ssl_context: ssl.SSLContext,
    account_id: str,
) -> Account:
    """The filter engine's account for the store user's ``account_id``, its password opened. Raises
    ``AccountNotFound``, or ``MailFailure`` (``secrets_key_missing``, ``password_missing``,
    ``starttls_unsupported``) before any connection."""
    account = load_accounts(store).account(account_id)
    if subkey is None:
        msg = "MAILROOM_SECRETS_KEY is not set: no stored password can be opened."
        raise MailFailure("secrets_key_missing", msg)
    password = account_password(store, subkey, account_id)
    if password is None:
        msg = "No stored password opens for this account: enter it again."
        raise MailFailure("password_missing", msg)
    return imap_account(account, password, ssl_context)


def check_login(account: Account) -> None:
    """Log in and out again. Raises ``MailFailure``."""
    try:
        account.connect().logout()
    except Exception as exc:
        failure = mail_failure(exc, account)
        if failure is None:
            raise
        raise failure from None


def hierarchy_delimiter(conn: IMAPClient) -> str | None:
    """The server's folder delimiter (``LIST "" ""``), or None when it reports none."""
    for _flags, delimiter, _name in conn.list_folders("", ""):
        if isinstance(delimiter, bytes):
            return delimiter.decode()
        if isinstance(delimiter, str) and delimiter:
            return delimiter
    return None


def list_folders(account: Account, *, seconds: float | None = None) -> list[list[str]]:
    """The folders that can hold mail, as path segments split on the server's delimiter, in the
    server's order. With ``seconds``, a listing still going after that long is stopped (its
    connections closed) and fails as ``timeout``: each IMAP read has its own timeout, but a slow
    server answering just in time could otherwise keep it going much longer. Raises
    ``MailFailure``.

    With ``seconds``, ``account`` must be a fresh one of the caller's own (as
    ``mailroom.accounts.service.imap_account`` builds per call), never shared with another
    thread: for the listing, its ``connect`` is replaced on the instance and restored after."""
    if seconds is None:
        return _list_folders(account)
    deadline = _Deadline(account.connect, seconds)
    # The filter engine's Account.list_folders opens its own connection through self.connect and has
    # no other hook, so the instance's connect goes through the deadline while it lists.
    account.connect = deadline.connect  # type: ignore[method-assign]
    deadline.start()
    try:
        return _list_folders(account)
    except Exception:
        if deadline.expired:
            raise MailFailure("timeout", MESSAGES["timeout"]) from None
        raise
    finally:
        deadline.cancel()
        del account.connect


def _list_folders(account: Account) -> list[list[str]]:
    try:
        conn = account.connect()
        try:
            delimiter = hierarchy_delimiter(conn)
        finally:
            with suppress(Exception):
                conn.logout()
        if delimiter is not None:
            account.delimiter = delimiter
        return [list(path.segments) for path in account.list_folders()]
    except Exception as exc:
        failure = mail_failure(exc, account)
        if failure is None:
            raise
        raise failure from None


class _Deadline:
    """Closes the connections ``connect`` opened once ``seconds`` have passed, from a timer
    thread, and refuses any opened after: the work using them then fails."""

    def __init__(self, connect: Callable[[], IMAPClient], seconds: float) -> None:
        self._connect = connect
        self._lock = threading.Lock()
        self._connections: list[IMAPClient] = []
        self.expired = False
        self._timer = threading.Timer(seconds, self._expire)
        self._timer.daemon = True

    def start(self) -> None:
        self._timer.start()

    def cancel(self) -> None:
        self._timer.cancel()

    def connect(self) -> IMAPClient:
        conn = self._connect()
        with self._lock:
            if not self.expired:
                self._connections.append(conn)
                return conn
        _shut(conn)
        msg = "the folder listing was stopped"
        raise TimeoutError(msg)

    def _expire(self) -> None:
        with self._lock:
            self.expired = True
            connections = list(self._connections)
        for conn in connections:
            _shut(conn)


def _shut(conn: IMAPClient) -> None:
    """Close the socket under ``conn``, even while another thread reads from it."""
    with suppress(Exception):
        conn.shutdown()


def mail_failure(exc: Exception, account: Account) -> MailFailure | None:
    """``exc``, raised for ``account``, as a ``MailFailure``: mailroom-core's code and fixed
    message (``mailroom_core.mail.mail_error``, which logs the original with the account id), or
    None when it is not about reaching the mailbox. A login's error, whose text can echo the
    credentials escaped beyond recognition, and any error quoting the password are logged by
    their type only."""
    if not isinstance(exc, imaplib.IMAP4.error | OSError | EOFError):
        return None
    secret = isinstance(exc, LoginError) or (
        bool(account.password) and account.password in str(exc)
    )
    error = mail_error(exc, account.name, secret=secret)
    return MailFailure(error.code, error.message)
