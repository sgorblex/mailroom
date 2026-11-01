import imaplib
import logging
import socket
import ssl

import pytest
from imapclient.exceptions import CapabilityError, IllegalStateError, LoginError

from mailroom_core.mail import MAIL_ERROR_CODES, MESSAGES, MailError, mail_error


def _login_error_during(error: BaseException) -> LoginError:
    """As imapclient raises it: `raise LoginError(str(e))` inside its `except`."""
    try:
        raise error
    except BaseException as caught:
        try:
            raise LoginError(str(caught))
        except LoginError as wrapped:
            return wrapped


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LoginError("b'[AUTHENTICATIONFAILED] Authentication failed.'"), "auth_failed"),
        (_login_error_during(imaplib.IMAP4.error("[AUTHENTICATIONFAILED] no")), "auth_failed"),
        # A connection lost during the login is no wrong password.
        (_login_error_during(imaplib.IMAP4.abort("socket error: EOF")), "unreachable"),
        (_login_error_during(ConnectionResetError(104, "reset")), "unreachable"),
        (imaplib.IMAP4.error("login failed: [AUTHENTICATIONFAILED] no"), "auth_failed"),
        (
            imaplib.IMAP4.error("select failed: [NONEXISTENT] Mailbox doesn't exist: Nope"),
            "mailbox_missing",
        ),
        (
            imaplib.IMAP4.error("append failed: [TRYCREATE] Mailbox doesn't exist: Nope"),
            "mailbox_missing",
        ),
        (
            imaplib.IMAP4.error("create failed: [ALREADYEXISTS] Mailbox already exists"),
            "server_error",
        ),
        (CapabilityError("Server does not support MOVE capability"), "unsupported"),
        (TimeoutError("timed out"), "timeout"),
        (ssl.SSLCertVerificationError("certificate verify failed"), "unreachable"),
        (ssl.SSLError("handshake failure"), "unreachable"),
        (ConnectionRefusedError(111, "Connection refused"), "unreachable"),
        (ConnectionResetError(104, "Connection reset by peer"), "unreachable"),
        (socket.gaierror(-2, "Name or service not known"), "unreachable"),
        (imaplib.IMAP4.abort("socket error: EOF"), "unreachable"),
        (EOFError(), "unreachable"),
        # A subclass of `abort`, but the connection is fine.
        (imaplib.IMAP4.readonly("mailbox status changed to READ-ONLY"), "server_error"),
        (IllegalStateError("command illegal in state AUTH"), "server_error"),
        (ValueError("a bug"), "server_error"),
    ],
)
def test_maps(error: BaseException, code: str) -> None:
    assert mail_error(error, "acc").code == code


def test_keeps_server_text_out_of_the_message_but_logs_it(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="mailroom_core.mail"):
        mapped = mail_error(
            LoginError("NO [AUTHENTICATIONFAILED] login me@x.org pass hunter2"), "acc"
        )
    assert str(mapped) == "The mail server rejected the login for this account."
    assert "hunter2" not in repr(mapped)
    [record] = caplog.records
    assert record.getMessage() == "imap_error"
    assert record.__dict__["account"] == "acc"
    assert record.__dict__["mapped"] == "auth_failed"
    assert "hunter2" in record.__dict__["error"]


def test_logs_only_the_type_of_a_login_error(caplog: pytest.LogCaptureFixture) -> None:
    try:
        "perché".encode("ascii")
    except UnicodeEncodeError as error:
        with caplog.at_level(logging.WARNING, logger="mailroom_core.mail"):
            mapped = mail_error(error, "acc", secret=True)
    assert mapped.code == "server_error"
    [record] = caplog.records
    assert record.__dict__["error_type"] == "UnicodeEncodeError"
    assert "error" not in record.__dict__
    assert record.exc_info is None
    assert "é" not in caplog.text


def test_logs_a_bug_with_its_traceback(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="mailroom_core.mail"):
        mail_error(KeyError(b"BODY[1]"), "acc")
        mail_error(imaplib.IMAP4.abort("socket error: EOF"), "acc")
    bug, connection = caplog.records
    assert bug.exc_info is not None
    assert connection.exc_info is None


def test_passes_mail_errors_through(caplog: pytest.LogCaptureFixture) -> None:
    error = MailError("stale_reference")
    assert mail_error(error, "acc") is error
    assert caplog.records == []


def test_messages_are_fixed_with_an_optional_detail() -> None:
    assert len(MAIL_ERROR_CODES) == 14
    assert set(MESSAGES) == set(MAIL_ERROR_CODES)
    assert MailError("not_found").message == "No such message or part."
    error = MailError("invalid_argument", 'Unknown account "x".')
    assert str(error) == 'Invalid argument. Unknown account "x".'
    assert (error.code, error.detail) == ("invalid_argument", 'Unknown account "x".')
