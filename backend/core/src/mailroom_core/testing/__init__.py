"""Test support shared by mailroom-core, mailroom-mcp and the Mailroom app.

Loaded by pytest as a plugin (the `pytest11` entry point), it refuses sockets beyond this machine
and the mail test servers, and provides the `imap_server` and `smtp_server` fixtures and, for the
store, `throwaway_databases` and `core_engine`. `mailroom_core.testing.imap` seeds and inspects
the IMAP test server.
"""

from mailroom_core.testing.config import config_env, local_config_text
from mailroom_core.testing.database import Migrate, ThrowawayDatabases, schema_differences
from mailroom_core.testing.imap import (
    MimeAttachment,
    RawMessage,
    connections_for,
    create_folders,
    folder_contents,
    mime,
    raw_client,
    seed,
    test_account,
    unverified_context,
)
from mailroom_core.testing.mail_servers import Delivered, ImapTestServer, SmtpTestServer
from mailroom_core.testing.no_network import NetworkAccessError, no_network

__all__ = [
    "Delivered",
    "ImapTestServer",
    "Migrate",
    "MimeAttachment",
    "NetworkAccessError",
    "RawMessage",
    "SmtpTestServer",
    "ThrowawayDatabases",
    "config_env",
    "connections_for",
    "create_folders",
    "folder_contents",
    "local_config_text",
    "mime",
    "no_network",
    "raw_client",
    "schema_differences",
    "seed",
    "test_account",
    "unverified_context",
]
