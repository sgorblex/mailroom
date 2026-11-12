"""What the worker's tasks and its IDLE supervisor share: database sessions, and the users'
mailboxes reached through their account stores.

The worker's mail connections are a ``MailPool`` of its own, one connection per account: the IDLE
supervisor leases a user's connections only to look a folder's name up and build its watch, which
then idles on a connection of its own, so the pool logs the looked-up one out once unused; a run
leases them for as long as it runs. Only ``lease`` is used, without the API's gates or deadline.
``close`` it at shutdown.
"""

from __future__ import annotations

import functools
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from mailroom.accounts.secrets_key import secrets_subkey_from_env
from mailroom.database import create_database_engine, create_session_factory, get_sync_database_url
from mailroom.mail.connections import mail_ssl_context
from mailroom.mail.pool import MailPool
from mailroom_core.mail import Timeouts

if TYPE_CHECKING:
    import ssl
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.mail import MailConnections

WORKER_TIMEOUTS: Final = Timeouts(connect=15.0, read=120.0, lock=60.0)
"""Nothing waits on the worker for an answer: a reply may take 2 minutes (a search of a large
folder), and a caller waits up to a minute for the account's one connection."""
WORKER_CONNECTIONS_PER_ACCOUNT: Final = 1
"""Runs on one mailbox go one at a time already (their job's lock)."""


def _worker_pool() -> MailPool:
    return MailPool(
        timeouts=WORKER_TIMEOUTS, connections_per_account=WORKER_CONNECTIONS_PER_ACCOUNT
    )


@dataclass(frozen=True, slots=True)
class Mailboxes:
    """The users' accounts as the worker reaches them: a ``mailroom.mail.connections.MailSource``,
    with the worker's pool."""

    engine: Engine
    """The app's database, where mailroom-core keeps the accounts (schema ``core``)."""
    subkey: SecretsSubkey | None
    ssl_context: ssl.SSLContext
    pool: MailPool = field(default_factory=_worker_pool)

    @contextmanager
    def lease(self, user_id: int, revision: int) -> Iterator[MailConnections]:
        """The user's connections from the worker's pool for the block (``MailPool.lease``):
        ``revision`` is their settings' current one."""
        with self.pool.lease(self, user_id, revision) as connections:
            yield connections

    def close(self) -> None:
        """Log every connection of the pool out: once, at shutdown."""
        self.pool.close()


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
