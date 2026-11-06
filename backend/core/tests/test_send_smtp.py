"""SMTP over the scripted stub: above all, where a failure counts as refused and where as unknown.

Past DATA, `delivery_unknown` is the answer to everything but a real 4xx or 5xx: `failed` is the
one state that lets a message be sent again.
"""

import logging
import socket
import ssl
import threading
import time
from typing import TYPE_CHECKING

import pytest

from mailroom_core.config import AccountConfig
from mailroom_core.mail import Envelope, MailError
from mailroom_core.send import (
    Delivery,
    SmtpSender,
    SmtpTimeouts,
    Transfer,
    smtp_error,
    verify_smtp,
)
from mailroom_core.store import SEND_CEILING
from mailroom_core.testing import HANG_UP, STALL, SmtpStub

if TYPE_CHECKING:
    from collections.abc import Sequence

PASSWORD = "not-for-any-log"
ME = "me@example.org"
RAW = b"From: me@example.org\r\nTo: ana@example.org\r\nSubject: s\r\n\r\nHi\r\n"
FAST = SmtpTimeouts(connect=2, command=0.3, data=0.3, goodbye=0.1, watchdog=30)
"""Short enough for a stall to fail a test quickly; `data` must outlast the stub's replies."""


def account_on(stub: SmtpStub, **smtp: object) -> AccountConfig:
    return AccountConfig.model_validate(
        {
            "id": "acc",
            "login": "me",
            "send_mode": "direct",
            "auth": {"type": "password"},
            "imap": {"host": "127.0.0.1", "port": 9, "tls": "implicit"},
            "smtp": {**stub.smtp(), **smtp},
            "identities": [{"address": ME, "default": True}],
        }
    )


def sender_for(
    stub: SmtpStub,
    timeouts: SmtpTimeouts = FAST,
    context: ssl.SSLContext | None = None,
    password: str = PASSWORD,
) -> SmtpSender:
    account = account_on(stub)
    return SmtpSender(
        lambda _id: account,
        lambda _id: password,
        ssl_context=context or SmtpStub.trusted_context(),
        timeouts=timeouts,
    )


def send(
    stub: SmtpStub,
    recipients: Sequence[str] = ("ana@example.org",),
    raw: bytes = RAW,
    **options: object,
) -> Delivery:
    return sender_for(stub, **options).send("acc", Envelope(ME, tuple(recipients)), raw)  # type: ignore[arg-type]


def failure(stub: SmtpStub, *args: object, **options: object) -> MailError:
    with pytest.raises(MailError) as raised:
        send(stub, *args, **options)  # type: ignore[arg-type]
    return raised.value


def verbs(stub: SmtpStub) -> list[str]:
    return [command.split(" ")[0].split(":")[0].upper() for command in stub.commands]


def test_implicit_tls_with_a_verified_certificate() -> None:
    with SmtpStub() as stub:
        assert send(stub, ["ana@example.org", "bo@example.org"]) == Delivery(())
    assert verbs(stub) == ["EHLO", "AUTH", "MAIL", "RCPT", "RCPT", "DATA", "QUIT"]
    assert stub.commands[2].startswith("mail from:<me@example.org>")
    assert stub.messages == [RAW]


def test_starttls_logs_in_only_inside_tls() -> None:
    with SmtpStub(tls="starttls") as stub:
        send(stub)
    assert verbs(stub) == ["EHLO", "STARTTLS", "EHLO", "AUTH", "MAIL", "RCPT", "DATA", "QUIT"]


def test_stripped_starttls_gets_no_login_and_no_message() -> None:
    with SmtpStub(tls="stripped") as stub:
        error = failure(stub)
    assert error.code == "unreachable"
    assert "nothing was sent" in error.message
    assert verbs(stub) == ["EHLO"]
    assert stub.logins == []


def test_a_stalled_starttls_handshake_gets_the_connect_timeout() -> None:
    timeouts = SmtpTimeouts(connect=0.2, command=30, data=30, watchdog=30)
    started = time.monotonic()
    with SmtpStub(tls="starttls", tls_handshake=False) as stub:
        assert failure(stub, timeouts=timeouts).code == "timeout"
    assert time.monotonic() - started < 5


def test_a_refused_starttls_gets_no_login_either() -> None:
    with SmtpStub(tls="starttls", starttls_reply=b"454 not now\r\n") as stub:
        assert failure(stub).code == "unreachable"
    assert "AUTH" not in verbs(stub)
    assert "MAIL" not in verbs(stub)


def test_a_certificate_it_cannot_verify_gets_no_login() -> None:
    with SmtpStub() as stub:
        error = failure(stub, context=ssl.create_default_context())
    assert error.code == "unreachable"
    assert stub.commands == []


def test_a_certificate_for_another_name_gets_no_login(monkeypatch: pytest.MonkeyPatch) -> None:
    connect = socket.create_connection

    def to_the_stub(address: tuple[str, int], timeout: float) -> socket.socket:
        return connect(("127.0.0.1", address[1]), timeout)

    monkeypatch.setattr(socket, "create_connection", to_the_stub)
    with SmtpStub() as stub:
        account = account_on(stub, host="mail.example.org")
        sender = SmtpSender(
            lambda _id: account, lambda _id: PASSWORD, ssl_context=SmtpStub.trusted_context()
        )
        with pytest.raises(MailError) as raised:
            sender.send("acc", Envelope(ME, ("a@example.org",)), RAW)
    assert raised.value.code == "unreachable"
    assert stub.commands == []


def test_dots_are_stuffed_on_the_wire_and_arrive_as_written() -> None:
    raw = RAW + b".\r\n..two\r\n.leading\r\nlast\r\n"
    with SmtpStub() as stub:
        send(stub, raw=raw)
    assert stub.messages == [raw]


def test_every_line_goes_out_ending_in_crlf_with_its_dots_stuffed() -> None:
    with SmtpStub() as stub:
        send(stub, raw=b"Subject: s\n\na\n.\nb\r.\rc\r\nd\n")
    assert stub.messages == [b"Subject: s\r\n\r\na\r\n.\r\nb\r\n.\r\nc\r\nd\r\n"]
    assert b"\r\n..\r\n" in stub.raw_messages[0]


def test_each_recipient_is_named_once() -> None:
    with SmtpStub() as stub:
        send(stub, ["ana@example.org", "bo@example.org", "ana@example.org"])
    assert verbs(stub).count("RCPT") == 2


# Refused: nothing left, so it may be sent again.


def test_partial_rcpt_refusal_is_a_delivery_with_those_recipients() -> None:
    with SmtpStub(refuse=["bo@example.org"]) as stub:
        delivery = send(stub, ["ana@example.org", "bo@example.org", "cy@example.org"])
    assert delivery == Delivery(("bo@example.org",))
    assert stub.messages == [RAW]


def test_every_recipient_refused_is_recipient_refused_and_no_data() -> None:
    with SmtpStub(refuse=["ana@example.org"]) as stub:
        error = failure(stub)
    assert error.code == "recipient_refused"
    assert "DATA" not in verbs(stub)


def test_a_refused_mail_from_names_the_identity() -> None:
    with SmtpStub(mail_reply=b"553 not yours\r\n") as stub:
        error = failure(stub)
    assert error.code == "recipient_refused"
    assert "From address" in error.message


def test_a_refused_login_is_one_attempt() -> None:
    with SmtpStub(auth_reply=b"535 no\r\n") as stub:
        assert failure(stub).code == "auth_failed"
    assert verbs(stub).count("AUTH") == 1


def test_no_login_mechanism_it_can_use() -> None:
    with SmtpStub(auth="XOAUTH2") as stub:
        assert failure(stub).code == "auth_failed"
    assert "MAIL" not in verbs(stub)


@pytest.mark.parametrize("mechanisms", ["PLAIN LOGIN", "LOGIN"])
def test_the_login_goes_as_utf8(mechanisms: str) -> None:
    with SmtpStub(auth=mechanisms) as stub:
        send(stub, password="pàssword")
    assert stub.logins == ["\0me\0pàssword".encode()]


def test_a_login_mechanism_that_stops_midway_is_refused() -> None:
    with SmtpStub(auth="LOGIN", auth_reply=b"535 no\r\n") as stub:
        assert failure(stub).code == "auth_failed"
    assert verbs(stub).count("MAIL") == 0


@pytest.mark.parametrize("reply", [b"451 later\r\n", b"554 no\r\n", b"421 going away\r\n"])
def test_a_refused_data_command_failed(reply: bytes) -> None:
    with SmtpStub(data_reply=reply) as stub:
        assert failure(stub).code == "server_error"
    assert stub.messages == []


@pytest.mark.parametrize(
    "reply",
    [b"421 shutting down\r\n", b"451 try later\r\n", b"554 spam\r\n", b"452-a\r\n452 b\r\n"],
)
def test_a_4xx_or_5xx_reply_to_the_message_failed(reply: bytes) -> None:
    with SmtpStub(end_reply=reply) as stub:
        assert failure(stub).code == "server_error"
    assert stub.messages == [RAW]


def test_a_stall_before_data_failed() -> None:
    with SmtpStub(rcpt_reply=STALL) as stub:
        assert failure(stub).code == "timeout"
    assert "DATA" not in verbs(stub)


def test_a_line_lost_before_data_failed() -> None:
    with SmtpStub(mail_reply=HANG_UP) as stub:
        assert failure(stub).code == "unreachable"


def test_nothing_listening_is_unreachable() -> None:
    with SmtpStub() as stub:
        account = account_on(stub, port=9)
    sender = SmtpSender(
        lambda _id: account,
        lambda _id: PASSWORD,
        ssl_context=SmtpStub.trusted_context(),
        timeouts=FAST,
    )
    with pytest.raises(MailError) as raised:
        sender.send("acc", Envelope(ME, ("a@example.org",)), RAW)
    assert raised.value.code == "unreachable"


# Unknown: it may have gone out, and must not be sent again on its own.


def test_a_stall_after_the_message_is_unknown() -> None:
    with SmtpStub(end_reply=STALL) as stub:
        assert failure(stub).code == "delivery_unknown"
    assert stub.messages == [RAW]


def test_a_line_lost_after_the_message_is_unknown() -> None:
    with SmtpStub(end_reply=HANG_UP) as stub:
        assert failure(stub).code == "delivery_unknown"


def test_a_stall_on_the_data_command_is_unknown() -> None:
    with SmtpStub(data_reply=STALL) as stub:
        assert failure(stub).code == "delivery_unknown"


def test_a_line_lost_on_the_data_command_is_unknown() -> None:
    with SmtpStub(data_reply=HANG_UP) as stub:
        assert failure(stub).code == "delivery_unknown"


@pytest.mark.parametrize(
    "reply",
    [
        b"nonsense\r\n",  # smtplib reports code -1
        pytest.param(b"x" * 9000 + b"\r\n", id="too long, it makes up a 500"),
        b"354 again\r\n",
        b"251 elsewhere\r\n",  # 2xx, but not the 250 sendmail wants
        b"650 past the classes\r\n",
    ],
)
def test_what_is_not_a_refusal_after_the_message_is_unknown(reply: bytes) -> None:
    with SmtpStub(end_reply=reply) as stub:
        assert failure(stub).code == "delivery_unknown"


def test_the_default_watchdog_is_the_ceiling_past_which_a_send_reads_unknown() -> None:
    assert SmtpTimeouts().watchdog == SEND_CEILING.total_seconds()


def test_a_data_timeout_grows_with_the_size() -> None:
    assert SmtpTimeouts().for_data(0) == 60
    assert SmtpTimeouts().for_data(128 * 1024 * 60) == 120


def test_the_watchdog_ends_a_server_dripping_under_every_timeout() -> None:
    timeouts = SmtpTimeouts(connect=2, command=0.3, data=0.3, watchdog=0.8)
    started = time.monotonic()
    with SmtpStub(end_drip=0.05) as stub:
        assert failure(stub, timeouts=timeouts).code == "delivery_unknown"
    assert time.monotonic() - started < 5


def test_the_watchdog_before_data_failed() -> None:
    timeouts = SmtpTimeouts(connect=2, command=5, data=5, watchdog=0.2)
    with SmtpStub(rcpt_reply=STALL) as stub:
        assert failure(stub, timeouts=timeouts).code == "timeout"


def test_a_host_can_abort_a_send_from_another_thread() -> None:
    timeouts = SmtpTimeouts(connect=2, command=30, data=30, watchdog=30)
    with SmtpStub(end_reply=STALL) as stub:
        transfer = Transfer()
        threading.Timer(0.2, transfer.abort).start()
        started = time.monotonic()
        with pytest.raises(MailError) as raised:
            sender_for(stub, timeouts).send("acc", Envelope(ME, ("a@example.org",)), RAW, transfer)
    assert raised.value.code == "delivery_unknown"
    assert time.monotonic() - started < 5


def test_an_abort_during_the_tls_handshake_ends_it() -> None:
    timeouts = SmtpTimeouts(connect=30, command=30, data=30, watchdog=30)
    with socket.create_server(("127.0.0.1", 0)) as quiet, SmtpStub() as stub:
        # Connected, by the backlog, and nothing ever answers the handshake.
        account = account_on(stub, port=quiet.getsockname()[1])
        sender = SmtpSender(
            lambda _id: account,
            lambda _id: PASSWORD,
            ssl_context=SmtpStub.trusted_context(),
            timeouts=timeouts,
        )
        transfer = Transfer()
        threading.Timer(0.2, transfer.abort).start()
        started = time.monotonic()
        with pytest.raises(MailError) as raised:
            sender.send("acc", Envelope(ME, ("a@example.org",)), RAW, transfer)
    assert raised.value.code == "timeout"
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("reply", [STALL, HANG_UP, b"500 no\r\n"])
def test_a_bad_quit_changes_nothing(reply: bytes | None) -> None:
    with SmtpStub(quit_reply=reply, refuse=["bo@example.org"]) as stub:
        assert send(stub, ["ana@example.org", "bo@example.org"]) == Delivery(("bo@example.org",))


# What comes out.


def test_server_text_and_the_password_reach_no_message_and_no_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with SmtpStub(end_reply=b"554 secret words\r\n", refuse=["bo@example.org"]) as stub:
        error = failure(stub, ["ana@example.org", "bo@example.org"])
    with SmtpStub(auth_reply=b"535 secret words\r\n") as stub:
        refused = failure(stub)
    logged = " ".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    for text in (error.message, refused.message, logged):
        assert "secret" not in text
        assert PASSWORD not in text
    assert "smtp_error" in logged


def test_an_unknown_account_or_password() -> None:
    sender = SmtpSender(lambda _id: None, lambda _id: None)
    with pytest.raises(MailError) as raised:
        sender.send("nope", Envelope(ME, ("a@example.org",)), RAW)
    assert raised.value.code == "invalid_argument"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (TimeoutError(), "timeout"),
        (ConnectionRefusedError(), "unreachable"),
        (ssl.SSLCertVerificationError(), "unreachable"),
        (ValueError("a bug"), "server_error"),
    ],
)
def test_maps_errors_before_data(error: Exception, code: str) -> None:
    assert smtp_error(error, "acc").code == code


@pytest.mark.parametrize(
    "error",
    [TimeoutError(), ConnectionResetError(), ssl.SSLEOFError(), ValueError("a bug")],
)
def test_past_data_every_error_is_unknown(error: Exception) -> None:
    assert smtp_error(error, "acc", entered_data=True).code == "delivery_unknown"


def test_verify_logs_in_and_sends_nothing() -> None:
    with SmtpStub() as stub:
        account = account_on(stub)
        assert verify_smtp(account, PASSWORD, ssl_context=SmtpStub.trusted_context()) is None
    assert verbs(stub) == ["EHLO", "AUTH", "QUIT"]


def test_verify_reports_a_refused_login() -> None:
    with SmtpStub(auth_reply=b"535 no\r\n") as stub:
        code = verify_smtp(account_on(stub), PASSWORD, ssl_context=SmtpStub.trusted_context())
    assert code == "auth_failed"
