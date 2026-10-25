"""What the worker's tasks and its IDLE supervisor share: database sessions, and the users'
mailboxes reached through their account stores."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mailroom.accounts.mail import mail_ssl_context, mailbox_account
from mailroom.accounts.secrets_key import secrets_subkey_from_env
from mailroom.accounts.store import user_store
from mailroom.database import create_database_engine, create_session_factory, get_sync_database_url

if TYPE_CHECKING:
    import ssl

    from imap_thingy.accounts import Account
    from mailroom_core.crypto import SecretsSubkey
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True, slots=True)
class Mailboxes:
    """The users' accounts as imap-thingy reaches them."""

    engine: Engine
    """The app's database, where mailroom-core keeps the accounts (schema ``core``)."""
    subkey: SecretsSubkey | None
    ssl_context: ssl.SSLContext

    def account(self, user_id: int, account_id: str) -> Account:
        """``mailroom.filters.imap.mailbox_account`` of the user's ``account_id``: raises
        ``AccountNotFound`` or ``MailFailure``."""
        return mailbox_account(
            user_store(self.engine, user_id), self.subkey, self.ssl_context, account_id
        )


@dataclass(frozen=True, slots=True)
class WorkerContext:
    sessions: sessionmaker[Session]
    mailboxes: Mailboxes


@functools.cache
def worker_context() -> WorkerContext:
    """The worker process's context, built from the environment on first use (``DATABASE_URL``,
    ``MAILROOM_SECRETS_KEY``, ``MAILROOM_MAIL_CA_FILE``)."""
    engine = create_database_engine(get_sync_database_url())
    return WorkerContext(
        sessions=create_session_factory(engine),
        mailboxes=Mailboxes(engine, secrets_subkey_from_env(), mail_ssl_context()),
    )
