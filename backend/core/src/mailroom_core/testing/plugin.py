"""The pytest plugin, registered through the `pytest11` entry point of mailroom-core."""

from contextlib import ExitStack

import pytest

from mailroom_core.testing.mail_servers import ImapTestServer, SmtpTestServer
from mailroom_core.testing.no_network import no_network

_guard = pytest.StashKey[ExitStack]()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: needs the Dovecot and Mailpit test servers; `mise run test-integration-py`",
    )
    stack = ExitStack()
    stack.enter_context(no_network())
    config.stash[_guard] = stack


def pytest_unconfigure(config: pytest.Config) -> None:
    config.stash[_guard].close()


@pytest.fixture(scope="session")
def imap_server() -> ImapTestServer:
    """The Dovecot test server; skips the test without IMAP_TEST_HOST."""
    server = ImapTestServer.from_env()
    if server is None:
        pytest.skip("IMAP_TEST_HOST is not set: run `mise run test-integration-py`")
    return server


@pytest.fixture(scope="session")
def smtp_server() -> SmtpTestServer:
    """The Mailpit test server; skips the test without SMTP_TEST_HOST."""
    server = SmtpTestServer.from_env()
    if server is None:
        pytest.skip("SMTP_TEST_HOST is not set: run `mise run test-integration-py`")
    return server
