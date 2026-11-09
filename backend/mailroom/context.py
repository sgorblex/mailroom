"""What the API's front ends (the REST routes, and any other) share: the database, the secrets
subkey, the TLS context the users' mailboxes are reached with and their mail connections.

``mailroom.api.main``'s lifespan builds them once and keeps them on ``app.state``
(``database_engine``, ``session_maker``, ``secrets_subkey``, ``mail_ssl_context``,
``mail_pool``, ``send_runner``); ``app_context`` reads them back as one ``AppContext``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mailroom.accounts.store import user_store

if TYPE_CHECKING:
    import ssl

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.datastructures import State

    from mailroom.mail.pool import MailPool
    from mailroom.mail.sends import SendRunner
    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.store import AccountStore


@dataclass(frozen=True, slots=True)
class AppContext:
    engine: Engine
    """The app's database; mailroom-core keeps the accounts there too (schema ``core``)."""
    session_maker: sessionmaker[Session]
    subkey: SecretsSubkey | None
    """None without ``MAILROOM_SECRETS_KEY``: no password can be stored or opened."""
    ssl_context: ssl.SSLContext
    mail_pool: MailPool | None = None
    """The users' IMAP connections for the mail client and the MCP mail tools
    (``mailroom.mail.pool``); None without the mail layer."""
    send_runner: SendRunner | None = None
    """Where sends run once admitted (``mailroom.mail.sends``); None without the mail layer."""

    def store(self, user_id: int) -> AccountStore:
        """The user's accounts, as mailroom-core keeps them."""
        return user_store(self.engine, user_id)

    def drop_mail_connections(self, user_id: int, account_id: str | None = None) -> None:
        """Close the user's open mail connections, of one account or of all; nothing without the
        mail layer."""
        if self.mail_pool is not None:
            self.mail_pool.invalidate(user_id, account_id)


def app_context(state: State) -> AppContext | None:
    """The context the lifespan left on ``state``; None while the database is unavailable."""
    engine: Engine | None = state.database_engine
    session_maker: sessionmaker[Session] | None = state.session_maker
    if engine is None or session_maker is None:
        return None
    return AppContext(
        engine=engine,
        session_maker=session_maker,
        subkey=state.secrets_subkey,
        ssl_context=state.mail_ssl_context,
        mail_pool=state.mail_pool,
        send_runner=state.send_runner,
    )
