"""Reaching an account's mailbox over IMAP through the filter engine, as the worker's runs and the
API's dry run still do (everything else goes through ``mailroom.mail.connections``).

TLS is verified as ``mailroom.mail.connections.mail_ssl_context`` sets it. Failures are reported by
kind, never with the password.
"""

from __future__ import annotations

import imaplib
from typing import TYPE_CHECKING, Final

from imapclient.exceptions import LoginError

from mailroom.accounts.store import account_password, load_accounts
from mailroom.errors import SECRETS_KEY_MISSING
from mailroom.mail.connections import (
    PASSWORD_MISSING,
    PASSWORD_MISSING_TEXT,
    SECRETS_KEY_MISSING_TEXT,
)
from mailroom_core.filters import Account
from mailroom_core.mail import mail_error

if TYPE_CHECKING:
    import ssl

    from imapclient import IMAPClient

    from mailroom_core.config import AccountConfig
    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.store import AccountStore

IMAP_TIMEOUT_SECONDS: Final = 20.0
"""For connecting and for every read: a dry run never hangs a request on one reply."""


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
        raise MailFailure(SECRETS_KEY_MISSING, SECRETS_KEY_MISSING_TEXT)
    password = account_password(store, subkey, account_id)
    if password is None:
        raise MailFailure(PASSWORD_MISSING, PASSWORD_MISSING_TEXT)
    return imap_account(account, password, ssl_context)


def hierarchy_delimiter(conn: IMAPClient) -> str | None:
    """The server's folder delimiter (``LIST "" ""``), or None when it reports none."""
    for _flags, delimiter, _name in conn.list_folders("", ""):
        if isinstance(delimiter, bytes):
            return delimiter.decode()
        if isinstance(delimiter, str) and delimiter:
            return delimiter
    return None


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
