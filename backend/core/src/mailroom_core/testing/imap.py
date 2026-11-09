"""Seeding and inspecting the Dovecot test server: the port of `src/testing/imap.ts`.

Every helper takes the `ImapTestServer` (the `imap_server` fixture) and a user: any username logs
in, to a fresh, empty home, so a test that takes a `unique_user` starts from an empty mailbox.
The raw client is independent of the code under test.
"""

import base64
import re
import secrets
import ssl
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.utils import format_datetime
from typing import TYPE_CHECKING

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError

from mailroom_core.config import AccountConfig
from mailroom_core.mail import MailConnections, Timeouts
from mailroom_core.testing.mail_servers import ImapTestServer, SmtpTestServer

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

SPECIAL_FOLDERS = ("Sent", "Drafts", "Junk", "Trash")


def unverified_context() -> ssl.SSLContext:
    """A TLS context that accepts the test servers' self-signed certificates. Tests only."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def test_account(
    account_id: str, user: str, server: ImapTestServer, **overrides: object
) -> AccountConfig:
    """An account on the test servers logging in as `user`; `overrides` replace fields
    (`imap={"host": "127.0.0.1", "port": 9, "tls": "implicit"}`, say)."""
    smtp = SmtpTestServer.from_env()
    fields: dict[str, object] = {
        "id": account_id,
        "login": user,
        "send_mode": "drafts",
        "auth": {"type": "password"},
        "imap": {"host": server.host, "port": server.port, "tls": "implicit"},
        "smtp": {
            "host": "127.0.0.1" if smtp is None else smtp.host,
            "port": 31025 if smtp is None else smtp.port,
            "tls": "implicit",
        },
        "identities": [{"address": f"{user}@example.test", "default": True}],
        "auto_approve": [],
    }
    return AccountConfig.model_validate({**fields, **overrides})


# Not a test, whatever pytest makes of its name where it is imported.
test_account.__test__ = False  # type: ignore[attr-defined]


def connections_for(
    server: ImapTestServer,
    accounts: Iterable[AccountConfig],
    passwords: Mapping[str, str] | None = None,
    *,
    pool_size: int = 1,
    timeouts: Timeouts | None = None,
) -> MailConnections:
    """MailConnections over `accounts`, each with the test password unless `passwords` names
    another; trusting the self-signed certificate, without a health cache."""
    by_id = {account.id: account for account in accounts}
    other = dict(passwords or {})
    return MailConnections(
        by_id.get,
        lambda account_id: other.get(account_id, server.password),
        ssl_context=unverified_context(),
        pool_size=pool_size,
        timeouts=timeouts or Timeouts(),
        health_cache=0.0,
    )


@contextmanager
def raw_client(server: ImapTestServer, user: str) -> Iterator[IMAPClient]:
    """A logged-in connection for seeding and inspecting, logged out after the block.

    CI's Dovecot runs without our drop-in config: this creates the special-use folders it would
    have. It also opens INBOX, since the first APPEND to a brand-new user's INBOX can report a
    UIDVALIDITY that STATUS then contradicts.
    """
    client = IMAPClient(server.host, server.port, ssl_context=unverified_context(), timeout=30)
    try:
        client.login(user, server.password)
        existing = {name for _flags, _delimiter, name in client.list_folders()}
        for path in SPECIAL_FOLDERS:
            if path not in existing:
                client.create_folder(path)
        client.select_folder("INBOX", readonly=True)
        yield client
    finally:
        try:
            client.logout()
        except Exception:
            client.shutdown()


_APPENDUID = re.compile(rb"\[APPENDUID \d+ (\d+)\]")


def seed(
    server: ImapTestServer,
    user: str,
    folder: str,
    message: str | bytes,
    flags: Iterable[str] = (),
    internal_date: datetime | None = None,
) -> int:
    """APPENDs one RFC 5322 message, optionally with its arrival date; returns its UID."""
    raw = message.encode() if isinstance(message, str) else message
    with raw_client(server, user) as client:
        response = client.append(folder, raw, tuple(flags), internal_date)
    found = _APPENDUID.search(response)
    if found is None:
        raise RuntimeError("append returned no uid")
    return int(found.group(1))


@dataclass(frozen=True)
class MimeAttachment:
    name: str
    type: str
    data: bytes


def mime(
    subject: str,
    *,
    sender: str = "Maria Rossi <maria@example.org>",
    to: str = "Ale <ale@example.test>",
    date: str | None = None,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    text: str | None = None,
    html: str | None = None,
    attachments: Iterable[MimeAttachment] = (),
) -> str:
    """A small RFC 5322 message builder for seeding; CRLF line endings as on the wire. `html`
    replaces the text body; attachments make it multipart/mixed."""
    boundary = f"b{secrets.token_hex(6)}"
    headers = [
        f"From: {sender}",
        f"To: {to}",
        f"Subject: {subject}",
        f"Date: {date or format_datetime(datetime.now(UTC), usegmt=True)}",
        f"Message-ID: {message_id or f'<{secrets.token_hex(8)}@example.org>'}",
        *([] if in_reply_to is None else [f"In-Reply-To: {in_reply_to}"]),
        *([] if references is None else [f"References: {references}"]),
        "MIME-Version: 1.0",
    ]

    def text_part(content_type: str, body: str) -> str:
        encoded = base64.b64encode(body.encode()).decode()
        return (
            f"Content-Type: {content_type}; charset=utf-8\r\n"
            f"Content-Transfer-Encoding: base64\r\n\r\n{encoded}"
        )

    body = text_part("text/plain", text or "") if html is None else text_part("text/html", html)
    attached = list(attachments)
    head = "\r\n".join(headers)
    if not attached:
        return f"{head}\r\n{body}\r\n"
    parts = [
        body,
        *(
            f'Content-Type: {a.type}; name="{a.name}"\r\n'
            f'Content-Disposition: attachment; filename="{a.name}"\r\n'
            f"Content-Transfer-Encoding: base64\r\n\r\n{base64.b64encode(a.data).decode()}"
            for a in attached
        ),
    ]
    joined = "\r\n".join(f"--{boundary}\r\n{part}" for part in parts)
    return (
        f'{head}\r\nContent-Type: multipart/mixed; boundary="{boundary}"\r\n\r\n'
        f"{joined}\r\n--{boundary}--\r\n"
    )


@dataclass(frozen=True)
class RawMessage:
    uid: int
    subject: str
    flags: tuple[str, ...]
    source: bytes
    """The whole message as stored."""


def folder_contents(server: ImapTestServer, user: str, folder: str) -> list[RawMessage]:
    """Every message of a folder as a raw client sees it, oldest first; empty if there is no
    such folder."""
    with raw_client(server, user) as client:
        try:
            client.select_folder(folder, readonly=True)
        except IMAPClientError:
            return []
        uids = client.search("ALL")
        if not uids:
            return []
        fetched = client.fetch(uids, ["ENVELOPE", "FLAGS", "BODY.PEEK[]"])
    return [
        RawMessage(
            uid=uid,
            subject=_subject(fetched[uid][b"ENVELOPE"].subject),
            flags=tuple(flag.decode() for flag in fetched[uid][b"FLAGS"]),
            source=fetched[uid][b"BODY[]"],
        )
        for uid in sorted(fetched)
    ]


def _subject(raw: bytes | None) -> str:
    if raw is None:
        return ""
    return str(make_header(decode_header(raw.decode("utf-8", "replace"))))


def create_folders(server: ImapTestServer, user: str, *paths: str) -> None:
    with raw_client(server, user) as client:
        for path in paths:
            client.create_folder(path)
