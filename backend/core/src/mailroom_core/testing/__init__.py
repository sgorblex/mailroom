"""Test support shared by mailroom-core, mailroom-mcp and the Mailroom app.

Loaded by pytest as a plugin (the `pytest11` entry point), it refuses sockets beyond this machine
and the mail test servers, and provides the `imap_server` and `smtp_server` fixtures and, for the
store, `throwaway_databases` and `core_engine`.
"""

from mailroom_core.testing.config import config_env, local_config_text
from mailroom_core.testing.database import Migrate, ThrowawayDatabases, schema_differences
from mailroom_core.testing.mail_servers import Delivered, ImapTestServer, SmtpTestServer
from mailroom_core.testing.no_network import NetworkAccessError, no_network

__all__ = [
    "Delivered",
    "ImapTestServer",
    "Migrate",
    "NetworkAccessError",
    "SmtpTestServer",
    "ThrowawayDatabases",
    "config_env",
    "local_config_text",
    "no_network",
    "schema_differences",
]
