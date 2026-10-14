"""The throwaway Dovecot (IMAP) and Mailpit (SMTP sink) the integration tests run against.

`mise run test-integration-py` starts both locally; Woodpecker runs them as services. The same
variables as the TypeScript tests (`src/testing/imap.ts`, `src/testing/smtp.ts`) locate them.
"""

import json
import os
import secrets
from dataclasses import dataclass
from typing import Self
from urllib.parse import quote
from urllib.request import urlopen


@dataclass(frozen=True)
class ImapTestServer:
    """docker.io/dovecot/dovecot: any username logs in with `password` to a fresh, empty home."""

    host: str
    port: int
    password: str = "pass"

    @classmethod
    def from_env(cls) -> Self | None:
        host = os.environ.get("IMAP_TEST_HOST")
        if host is None:
            return None
        return cls(host, int(os.environ.get("IMAP_TEST_PORT", "31993")))

    @staticmethod
    def unique_user(prefix: str) -> str:
        """A username no other test uses, so every test starts from an empty mailbox."""
        return f"{prefix}-{secrets.token_hex(4)}"


@dataclass(frozen=True)
class Delivered:
    """A message as Mailpit received it."""

    recipients: list[str]
    """SMTP envelope recipients, Bcc included."""
    raw: bytes


@dataclass(frozen=True)
class SmtpTestServer:
    """docker.io/axllent/mailpit: implicit TLS with a self-signed certificate, any login accepted,
    and an HTTP API to read what arrived."""

    host: str
    port: int
    api: str

    @classmethod
    def from_env(cls) -> Self | None:
        host = os.environ.get("SMTP_TEST_HOST")
        if host is None:
            return None
        return cls(
            host,
            int(os.environ.get("SMTP_TEST_PORT", "31025")),
            os.environ.get("MAILPIT_TEST_API", "http://127.0.0.1:38025"),
        )

    def delivered_to(self, address: str) -> list[Delivered]:
        """Every message Mailpit received for `address` (as an SMTP recipient), oldest first."""
        query = quote(f'addressed:"{address}"')
        with urlopen(f"{self.api}/api/v1/search?query={query}&limit=50", timeout=10) as response:
            messages = json.load(response)["messages"]
        delivered = []
        for message in reversed(messages):
            people = [*message["To"], *(message["Cc"] or []), *(message["Bcc"] or [])]
            with urlopen(f"{self.api}/api/v1/message/{message['ID']}/raw", timeout=10) as response:
                raw = response.read()
            delivered.append(Delivered([p["Address"].lower() for p in people], raw))
        return delivered
