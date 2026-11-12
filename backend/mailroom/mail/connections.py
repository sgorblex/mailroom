"""How the app reaches its users' mailboxes through mailroom-core's ``MailConnections``: the TLS
settings, a user's connections built from their account store, and the app's own reasons an account
cannot log in before any connection is tried.

Every connection verifies TLS: against the system CAs, plus the one ``MAILROOM_MAIL_CA_FILE`` names
(the dev mail backends' CA, ``dev/.state/certs/ca.pem``).
"""

from __future__ import annotations

import logging
import os
import ssl
from typing import TYPE_CHECKING, Final, Protocol

from mailroom.accounts.store import AccountNotFound, account_password, load_accounts, user_store
from mailroom.errors import SECRETS_KEY_MISSING
from mailroom_core.mail import MailConnections

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from mailroom_core.config import AccountConfig
    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.mail import Timeouts
    from mailroom_core.store import AccountStore

logger = logging.getLogger(__name__)

MAIL_CA_FILE_ENV: Final = "MAILROOM_MAIL_CA_FILE"
PASSWORD_MISSING: Final = "password_missing"
PASSWORD_MISSING_TEXT: Final = "No stored password opens for this account: enter it again."
SECRETS_KEY_MISSING_TEXT: Final = (
    "MAILROOM_SECRETS_KEY is not set: no stored password can be opened."
)


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


class PasswordMissing(Exception):  # noqa: N818 -- a state of the account, not a bug
    """No stored password opens for the account any more (changed since the call began)."""


class MailSource(Protocol):
    """Where a process finds its users' accounts and how it reaches them (the API's
    ``AppContext``)."""

    @property
    def engine(self) -> Engine:
        """The app's database, where mailroom-core keeps the accounts (schema ``core``)."""
        ...

    @property
    def subkey(self) -> SecretsSubkey | None:
        """None without ``MAILROOM_SECRETS_KEY``: no password opens."""
        ...

    @property
    def ssl_context(self) -> ssl.SSLContext: ...


def user_connections(
    source: MailSource, user_id: int, *, pool_size: int, timeouts: Timeouts
) -> MailConnections:
    """The user's connections, each account and password looked up in their store whenever a
    connection opens: an account gone is unknown to them, a password that no longer opens raises
    ``PasswordMissing``."""
    store = user_store(source.engine, user_id)
    subkey = source.subkey

    def account(account_id: str) -> AccountConfig | None:
        try:
            return load_accounts(store).account(account_id)
        except AccountNotFound:
            return None

    def password(account_id: str) -> str:
        found = account_password(store, subkey, account_id)
        if found is None:
            raise PasswordMissing
        return found

    return MailConnections(
        account, password, ssl_context=source.ssl_context, pool_size=pool_size, timeouts=timeouts
    )


def unusable(
    store: AccountStore, subkey: SecretsSubkey | None, account_id: str
) -> tuple[str, str] | None:
    """Why the store user's ``account_id`` cannot log in, found before any connection: the app's
    own code and fixed message (``secrets_key_missing``, ``password_missing``), or None when it
    can try. Raises ``AccountNotFound``."""
    load_accounts(store).account(account_id)
    if subkey is None:
        return SECRETS_KEY_MISSING, SECRETS_KEY_MISSING_TEXT
    if account_password(store, subkey, account_id) is None:
        return PASSWORD_MISSING, PASSWORD_MISSING_TEXT
    return None
