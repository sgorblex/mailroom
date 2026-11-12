"""The pytest plugin, registered through the `pytest11` entry point of mailroom-core."""

from contextlib import ExitStack
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import OperationalError

from mailroom_core.store import migrate
from mailroom_core.testing.database import ThrowawayDatabases
from mailroom_core.testing.mail_servers import ImapTestServer, SmtpTestServer
from mailroom_core.testing.no_network import no_network

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine

_guard = pytest.StashKey[ExitStack]()


def pytest_configure(config: pytest.Config) -> None:
    """Register the `integration` marker and block network access for the whole run."""
    config.addinivalue_line(
        "markers",
        "integration: needs the Dovecot and Mailpit test servers; `mise run test-core-mail`",
    )
    stack = ExitStack()
    stack.enter_context(no_network())
    config.stash[_guard] = stack


def pytest_unconfigure(config: pytest.Config) -> None:
    """Lift the network block."""
    config.stash[_guard].close()


@pytest.fixture(scope="session")
def imap_server() -> ImapTestServer:
    """The Dovecot test server; skips the test without IMAP_TEST_HOST."""
    server = ImapTestServer.from_env()
    if server is None:
        pytest.skip("IMAP_TEST_HOST is not set: run `mise run test-core-mail`")
    return server


@pytest.fixture(scope="session")
def smtp_server() -> SmtpTestServer:
    """The Mailpit test server; skips the test without SMTP_TEST_HOST."""
    server = SmtpTestServer.from_env()
    if server is None:
        pytest.skip("SMTP_TEST_HOST is not set: run `mise run test-core-mail`")
    return server


@pytest.fixture(scope="session")
def smtp_starttls_server() -> SmtpTestServer:
    """The Mailpit test server offering STARTTLS; skips the test without SMTP_TEST_STARTTLS_HOST."""
    server = SmtpTestServer.starttls_from_env()
    if server is None:
        pytest.skip("SMTP_TEST_STARTTLS_HOST is not set: run `mise run test-core-mail`")
    return server


@pytest.fixture(scope="session")
def throwaway_databases() -> Iterator[ThrowawayDatabases]:
    """Throwaway databases on TEST_DATABASE_URL's server; skips the test without it."""
    databases = ThrowawayDatabases.from_env()
    if databases is None:
        pytest.skip("TEST_DATABASE_URL is not set: mise.toml sets it for the dev Postgres")
    try:
        databases.check()
    except OperationalError:
        pytest.fail("cannot reach TEST_DATABASE_URL's Postgres: start it with `mise run dev-db`")
    try:
        yield databases
    finally:
        databases.close()


@pytest.fixture
def core_engine(throwaway_databases: ThrowawayDatabases) -> Iterator[Engine]:
    """A fresh database with schema `core` migrated, dropped after the test."""
    with throwaway_databases.migrated(migrate) as engine:
        yield engine
