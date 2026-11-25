"""What the API's front ends (the REST routes, and any other) share: the database, the secrets
subkey, the TLS context the users' mailboxes are reached with, their mail connections and the
instance's features.

``mailroom.api.main``'s lifespan builds them once and keeps them on ``app.state``
(``database_engine``, ``session_maker``, ``secrets_subkey``, ``mail_ssl_context``,
``mail_pool``, ``send_runner``; ``features``, ``keyword_failures`` and ``html_views`` are
``create_application``'s); ``app_context`` reads them back as one ``AppContext``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from mailroom.accounts.store import load_accounts, user_store
from mailroom.audit import WEB, Origin
from mailroom.auth.users import time_zone_of
from mailroom.config.features import ALL_FEATURES
from mailroom.mail.service.read import HtmlViews, KeywordFailures
from mailroom.reach import WHOLE, Reach

if TYPE_CHECKING:
    import ssl

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.datastructures import State

    from mailroom.accounts.store import UserAccounts
    from mailroom.config.features import Features
    from mailroom.mail.pool import MailPool
    from mailroom.mail.sends import SendRunner
    from mailroom_core.config import TimeZoneName
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
    mail_pool: MailPool
    """The users' IMAP connections (``mailroom.mail.pool``): the mail client's, the MCP mail
    tools', an account's login test and the folder picker's."""
    send_runner: SendRunner | None = None
    """Where sends run once admitted (``mailroom.mail.sends``); None without the mail layer."""
    features: Features = ALL_FEATURES
    """What the instance runs (``MAILROOM_FEATURES``)."""
    keyword_failures: KeywordFailures = field(default_factory=KeywordFailures)
    """The accounts whose keywords listings leave alone for a while (``account_keywords``)."""
    html_views: HtmlViews = field(default_factory=HtmlViews)
    """Messages' sanitised HTML views, kept a minute (``message_html``)."""
    reach: Reach = WHOLE
    """How far the caller reaches: less than the whole for a connected app with limits."""
    origin: Origin = WEB
    """Who calls: the user on the web, or an agent, whose sends the gate takes as an agent's."""

    def within(self, reach: Reach, origin: Origin | None = None) -> AppContext:
        """This context for a caller that reaches only so far, coming from ``origin`` (None: the
        same as now)."""
        return replace(self, reach=reach, origin=self.origin if origin is None else origin)

    def store(self, user_id: int) -> AccountStore:
        """The user's accounts, as mailroom-core keeps them, whatever the ``reach``: to write
        them, or for what an account already checked needs (its password, its sends)."""
        return user_store(self.engine, user_id)

    def accounts(self, user_id: int) -> UserAccounts:
        """The user's accounts within the caller's ``reach``."""
        return self.reach.within(load_accounts(self.store(user_id)))

    def time_zone(self, user_id: int) -> TimeZoneName:
        """The zone the user's dates are written in (``mailroom.auth.users.time_zone_of``)."""
        with self.session_maker() as db:
            return time_zone_of(db, user_id)

    def drop_mail_connections(self, user_id: int, account_id: int | None = None) -> None:
        """Close the user's open mail connections, of one account or of all."""
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
        features=state.features,
        keyword_failures=state.keyword_failures,
        html_views=state.html_views,
    )
