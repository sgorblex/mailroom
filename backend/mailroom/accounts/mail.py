"""Reaching an account's mailbox over IMAP, through imap-thingy.

Every connection verifies TLS: against the system CAs, plus the one ``MAILROOM_MAIL_CA_FILE``
names (the dev mail backends' CA, ``dev/.state/certs/ca.pem``). Failures are reported by kind,
never with the password.
"""

from __future__ import annotations

import imaplib
import logging
import os
import ssl
from contextlib import suppress
from typing import TYPE_CHECKING, Final

from imap_thingy.accounts import Account
from imapclient.exceptions import LoginError

from mailroom.accounts.store import account_password, load_accounts

if TYPE_CHECKING:
    from imapclient import IMAPClient
    from mailroom_core.config import AccountConfig
    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.store import AccountStore

logger = logging.getLogger(__name__)

MAIL_CA_FILE_ENV: Final = "MAILROOM_MAIL_CA_FILE"
IMAP_TIMEOUT_SECONDS: Final = 20.0
"""For connecting and for every read: a test or folder listing never hangs a request."""
_MAX_MESSAGE: Final = 300


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
    """The mailbox could not be reached or would not let us in. ``code`` names the kind;
    ``message`` is the server's or the system's wording, without the password."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def imap_account(account: AccountConfig, password: str, ssl_context: ssl.SSLContext) -> Account:
    """imap-thingy's view of ``account``. Raises ``MailFailure`` for an IMAP endpoint it cannot
    reach (STARTTLS: imap-thingy connects with implicit TLS only)."""
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
    """imap-thingy's account for the store user's ``account_id``, its password opened. Raises
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
        failure = mail_failure(exc, account.password)
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


def list_folders(account: Account) -> list[list[str]]:
    """The folders that can hold mail, as path segments split on the server's delimiter, in the
    server's order. Raises ``MailFailure``."""
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
        failure = mail_failure(exc, account.password)
        if failure is None:
            raise
        raise failure from None


def mail_failure(exc: Exception, password: str) -> MailFailure | None:
    """``exc`` as a ``MailFailure``, or None when it is not about reaching the mailbox."""
    if isinstance(exc, LoginError):
        code = "login_failed"
    elif isinstance(exc, ssl.SSLError):
        code = "tls_failed"
    elif isinstance(exc, TimeoutError):
        code = "timeout"
    elif isinstance(exc, OSError):
        code = "unreachable"
    elif isinstance(exc, imaplib.IMAP4.error):
        code = "imap_error"
    else:
        return None
    message = str(exc) or type(exc).__name__
    if password:
        message = message.replace(password, "***")
    return MailFailure(code, message[:_MAX_MESSAGE])
