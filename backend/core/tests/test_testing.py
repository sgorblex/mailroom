import smtplib
import socket
import ssl
from email.message import EmailMessage
from imaplib import IMAP4_SSL

import pytest

from mailroom_core.testing import ImapTestServer, NetworkAccessError, SmtpTestServer


def _self_signed() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


@pytest.mark.parametrize("host", ["mail.example.net", "imap.gmail.com", "1.1.1.1"])
def test_no_network_refuses_real_hosts(host: str) -> None:
    with (
        socket.socket() as sock,
        pytest.raises(NetworkAccessError, match="only reach this machine"),
    ):
        sock.connect((host, 993))


def test_no_network_allows_this_machine() -> None:
    with socket.socket() as sock, pytest.raises(ConnectionRefusedError):
        # Refused by the (empty) port, not by the guard.
        sock.connect(("127.0.0.1", 9))


@pytest.mark.integration
def test_dovecot_logs_in_any_user(imap_server: ImapTestServer) -> None:
    with IMAP4_SSL(imap_server.host, imap_server.port, ssl_context=_self_signed()) as imap:
        imap.login(imap_server.unique_user("smoke"), imap_server.password)
        status, _ = imap.select("INBOX", readonly=True)
        assert status == "OK"


@pytest.mark.integration
def test_mailpit_records_what_it_receives(smtp_server: SmtpTestServer) -> None:
    to = f"{ImapTestServer.unique_user('smoke')}@example.test"
    message = EmailMessage()
    message["From"] = "smoke@example.test"
    message["To"] = to
    message["Subject"] = "smoke"
    message.set_content("hello")
    with smtplib.SMTP_SSL(smtp_server.host, smtp_server.port, context=_self_signed()) as smtp:
        smtp.login("smoke", "any")
        smtp.send_message(message, to_addrs=[to, "bcc@example.test"])

    [delivered] = smtp_server.delivered_to(to)
    assert sorted(delivered.recipients) == sorted([to, "bcc@example.test"])
    assert b"Subject: smoke" in delivered.raw
