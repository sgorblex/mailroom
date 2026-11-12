"""Connections from plain config: `connections_from` and `MailConnections.from_accounts`."""

import tomllib

import pytest

from mailroom_core.config import ConfigError, CoreSettings, core_settings_or_issues
from mailroom_core.mail import MailConnections, connections_from
from mailroom_core.testing import (
    ImapTestServer,
    local_config_text,
    mime,
    seed,
    test_account,
    unverified_context,
)


def _account_toml(account_id: str, login: str, host: str, port: int) -> str:
    return f"""
[[account]]
id         = "{account_id}"
login      = "{login}"
send_mode  = "drafts"
auth       = {{ type = "password" }}
imap       = {{ host = "{host}", port = {port}, tls = "implicit" }}
smtp       = {{ host = "127.0.0.1", port = 9, tls = "implicit" }}
identities = [{{ address = "{login}@example.test" }}]
"""


def test_invalid_settings_raise_a_config_error_naming_each_issue() -> None:
    settings = tomllib.loads(
        _account_toml("Personal", "ada", "127.0.0.1", 9).replace('send_mode  = "drafts"\n', "")
    )
    with pytest.raises(ConfigError) as raised:
        connections_from(settings, {"Personal": "secret"})
    message = str(raised.value)
    assert message.startswith("invalid settings:\n")
    assert "→ at account[0].id" in message
    assert "→ at account[0].send_mode" in message


def test_the_app_config_file_is_not_plain_config() -> None:
    """Plain config is the stored settings alone: no bootstrap keys, no `secret_env`."""
    with pytest.raises(ConfigError, match=r'Unrecognized keys?: .*"secret_env"'):
        connections_from(tomllib.loads(local_config_text()), {})


def test_takes_checked_settings_as_they_are() -> None:
    checked = core_settings_or_issues(tomllib.loads(_account_toml("a", "ada", "127.0.0.1", 9)))
    assert isinstance(checked, CoreSettings)
    connections = connections_from(checked, {"a": "secret"})
    assert connections.serves("a")
    assert not connections.serves("b")


@pytest.mark.parametrize("passwords", [{}, {"a": ""}])
def test_an_account_without_a_password_fails_when_it_opens(passwords: dict[str, str]) -> None:
    connections = connections_from(
        tomllib.loads(_account_toml("a", "ada", "127.0.0.1", 9)), passwords
    )
    with pytest.raises(RuntimeError, match='no password for account "a"'):
        connections.with_mailbox("a", "INBOX", True, lambda ctx: None)


def test_from_accounts_refuses_two_accounts_with_one_id() -> None:
    server = ImapTestServer(host="127.0.0.1", port=9, password="x")
    with pytest.raises(ValueError, match='duplicate account id "a"'):
        MailConnections.from_accounts(
            [test_account("a", "one", server), test_account("a", "two", server)], {}
        )


# Against Dovecot


@pytest.mark.integration
def test_connects_from_plain_toml(imap_server: ImapTestServer) -> None:
    user = imap_server.unique_user("plain")
    seed(imap_server, user, "INBOX", mime("hello"))
    text = _account_toml("personal", user, imap_server.host, imap_server.port)
    connections = connections_from(
        tomllib.loads(text), {"personal": imap_server.password}, ssl_context=unverified_context()
    )
    try:
        exists = connections.with_mailbox("personal", "INBOX", True, lambda ctx: ctx.exists)
    finally:
        connections.close_all()
    assert exists == 1
