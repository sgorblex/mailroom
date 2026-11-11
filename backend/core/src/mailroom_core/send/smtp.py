"""Hands a composed message to an account's SMTP server (`src/send/smtp.ts`, over the standard
library's smtplib where that was nodemailer).

One connection per send: nothing is pooled, and the certificate is verified on each. What matters
most here is telling "the server refused it" from "we do not know": a message reported as failed
gets sent again, so a send only fails when the server said so, or when it broke off before DATA
was entered and no byte of the message left. From DATA on, anything but a 4xx or 5xx reply is
`delivery_unknown` (RFC 5321, 6.1: the server may have taken it and the answer been lost).
`sendmail` does not say how far it got, so `_Connection.data` notes it.
"""

import logging
import smtplib
import socket
import ssl
import threading
from base64 import b64encode
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from mailroom_core.mail.compose import crlf_lines
from mailroom_core.mail.errors import MailError
from mailroom_core.store import SEND_CEILING

if TYPE_CHECKING:
    from collections.abc import Buffer, Callable, Iterator

    from mailroom_core.config import AccountConfig
    from mailroom_core.config.schema import Endpoint
    from mailroom_core.mail.compose import Envelope
    from mailroom_core.mail.errors import MailErrorCode

_log = logging.getLogger("mailroom_core.send")

_EHLO_NAME: Final = "[127.0.0.1]"
"""As nodemailer says it on a host with no public name. smtplib would otherwise ask DNS for this
machine's, outside every timeout here, and tell the server what it found."""
_GREETING: Final = 220
_LOGGED_IN: Final = 235
_CONTINUE: Final = 334
_SERVER_REPLIES: Final = range(400, 600)


@dataclass(frozen=True, slots=True)
class SmtpTimeouts:
    """Seconds: `connect` for the connection, the greeting and all it takes to have TLS up,
    `command` for each reply after that, DATA for `data` plus the message's size at `data_rate`
    bytes a second, `goodbye` for QUIT, which nothing depends on, and `watchdog` for the whole
    send, which closes the socket on a server dripping bytes under the others. Its default is the
    store's `SEND_CEILING`, past which a send reads as `unknown`; `deliver` holds an attempt to
    what is left of that since its claim."""

    connect: float = 15.0
    command: float = 60.0
    data: float = 60.0
    data_rate: int = 128 * 1024
    goodbye: float = 5.0
    watchdog: float = SEND_CEILING.total_seconds()

    def for_data(self, size: int) -> float:
        """Seconds allowed for DATA with a message of `size` bytes."""
        return self.data + size / self.data_rate


@dataclass(frozen=True, slots=True)
class Delivery:
    """The server took the message."""

    rejected: tuple[str, ...] = ()
    """Recipients it refused while taking the others: they did not get it."""


class Transfer:
    """One send's connection, for a watchdog or a host shutting down: `abort`, from any thread,
    ends it at once, and the blocked send fails as it would have by timing out.

    It holds a duplicate of the TCP socket, so TLS can be going on over the original: shutting the
    connection down touches no SSL state another thread is using, and needs no handle on a socket
    that a TLS handshake has just replaced.
    """

    def __init__(self) -> None:
        """Start with no socket attached and not aborted."""
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._aborted = False

    @property
    def aborted(self) -> bool:
        """Whether `abort` has been called."""
        with self._lock:
            return self._aborted

    def abort(self) -> None:
        """Mark the transfer aborted and shut its socket down; a later `attach` is shut down too."""
        with self._lock:
            self._aborted = True
            self._shut_down()

    def attach(self, connected: socket.socket) -> None:
        """Called by the send itself: from here on `abort` reaches `connected`."""
        with self._lock:
            self._socket = connected.dup()
            if self._aborted:
                self._shut_down()

    def release(self) -> None:
        """Close the duplicate socket, if one is attached, once the send is over."""
        with self._lock:
            if self._socket is not None:
                self._socket.close()
                self._socket = None

    def _shut_down(self) -> None:
        if self._socket is not None:
            with suppress(OSError):
                self._socket.shutdown(socket.SHUT_RDWR)


@contextmanager
def watched(transfer: Transfer, seconds: float) -> Iterator[None]:
    """Aborts `transfer` if the block is still running after `seconds`."""
    watchdog = threading.Timer(seconds, transfer.abort)
    watchdog.daemon = True
    watchdog.start()
    try:
        yield
    finally:
        watchdog.cancel()
        transfer.release()


class Sender(Protocol):
    """Hands one composed message to an account's SMTP server."""

    def send(
        self, account_id: str, envelope: Envelope, raw: bytes, transfer: Transfer | None = None
    ) -> Delivery:
        """Returns once the server took it. Raises a `MailError`: `delivery_unknown` when it may
        have gone out, any other code when it did not."""
        ...


class _Connection(smtplib.SMTP):
    def __init__(self, timeouts: SmtpTimeouts) -> None:
        super().__init__(local_hostname=_EHLO_NAME, timeout=timeouts.connect)
        self._timeouts = timeouts
        self._data_timeout = timeouts.data
        self.entered_data = False

    def open_to(self, endpoint: Endpoint, context: ssl.SSLContext, transfer: Transfer) -> None:
        """Connects, reads the greeting and has TLS up, or raises: a server that does not offer
        STARTTLS gets no login and no message."""
        self._host = endpoint.host  # what `starttls` checks the certificate against
        self.sock = socket.create_connection((endpoint.host, endpoint.port), self._timeouts.connect)
        transfer.attach(self.sock)
        if endpoint.tls == "implicit":
            self.sock = context.wrap_socket(self.sock, server_hostname=endpoint.host)
        code, _reply = self.getreply()
        if code != _GREETING:
            raise smtplib.SMTPConnectError(code, b"")
        if endpoint.tls == "starttls":
            try:
                self.starttls(context=context)
            except smtplib.SMTPResponseException as error:
                raise smtplib.SMTPNotSupportedError from error
        self.sock.settimeout(self._timeouts.command)
        self.ehlo_or_helo_if_needed()

    def log_in(self, login: str, password: str) -> None:
        """One attempt, carrying UTF-8 as nodemailer does: smtplib's `login` goes through every
        mechanism on a refusal, each counting against a server's rate limit, and only speaks
        ASCII."""
        offered = self.esmtp_features.get("auth", "").upper().split()
        if "PLAIN" in offered:
            code, _reply = self.docmd("AUTH", f"PLAIN {_base64(f'\0{login}\0{password}')}")
        elif "LOGIN" in offered:
            code, _reply = self.docmd("AUTH", "LOGIN")
            for answer in (login, password):
                if code == _CONTINUE:
                    code, _reply = self.docmd(_base64(answer))
        else:
            raise MailError("auth_failed", "The server offers no login this client can use.")
        if code != _LOGGED_IN:
            raise smtplib.SMTPAuthenticationError(code, b"")

    def transmit(self, sender: str, recipients: list[str], raw: bytes) -> tuple[str, ...]:
        """The message, by `sendmail`; the recipients refused among those who took it. Every line
        ends in CRLF first: smtplib fixes that for text only, and stuffs dots after an LF alone, so
        a bare LF would reach the wire and a bare CR would hide a dot. This is too late to go by for
        anything read out of the message: that is why `prepare_draft` does it too, before it
        strips."""
        wire = crlf_lines(raw)
        self._data_timeout = self._timeouts.for_data(len(wire))
        return tuple(self.sendmail(sender, recipients, wire))

    def data(self, msg: Buffer | str) -> tuple[int, bytes]:
        self.entered_data = True
        if self.sock is not None:
            self.sock.settimeout(self._data_timeout)
        return super().data(msg)

    def goodbye(self) -> None:
        """QUIT, once nothing depends on it."""
        try:
            if self.sock is not None:
                self.sock.settimeout(self._timeouts.goodbye)
            self.quit()
        except Exception as error:
            _log.info(
                "smtp_quit_failed",
                extra={"event": "smtp_quit_failed", "error_type": type(error).__name__},
            )
            self.close()


def _base64(text: str) -> str:
    return b64encode(text.encode("utf-8")).decode("ascii")


def _timed_out(error: BaseException) -> bool:
    # smtplib wraps a timeout in SMTPServerDisconnected; the original is its context.
    return isinstance(error, TimeoutError) or isinstance(error.__context__, TimeoutError)


def _refused_after_data(error: BaseException) -> bool:
    """Whether a server's own 4xx or 5xx ended DATA. Not smtplib's other errors with a code: it
    makes up 500 for a reply line too long, -1 for one that is not a reply, and `sendmail` raises
    SMTPDataError for anything but 250, a 2xx or a 3xx too."""
    return isinstance(error, smtplib.SMTPDataError) and error.smtp_code in _SERVER_REPLIES


def _before_data(error: BaseException, aborted: bool) -> MailError:
    """What a failure before any of the message left means. Order matters: every smtplib error
    is an OSError, and so are the SSL errors and timeouts."""
    match error:
        case MailError():
            return error
        case smtplib.SMTPAuthenticationError():
            return MailError("auth_failed")
        case smtplib.SMTPSenderRefused():
            return MailError(
                "recipient_refused",
                "It refused the From address: check that the server lets this login send as "
                "that identity.",
            )
        case smtplib.SMTPRecipientsRefused():
            return MailError("recipient_refused", "It refused a recipient address.")
        case smtplib.SMTPNotSupportedError():
            return MailError("unreachable", "It does not offer TLS, so nothing was sent.")
        case _ if aborted or _timed_out(error):
            return MailError("timeout")
        case smtplib.SMTPServerDisconnected() | smtplib.SMTPConnectError():
            return MailError("unreachable")
        case smtplib.SMTPException():
            return MailError("server_error")
        case OSError():
            return MailError("unreachable")
        case _:
            return MailError("server_error")


def smtp_error(
    error: BaseException, account: str, *, entered_data: bool = False, aborted: bool = False
) -> MailError:
    """An SMTP failure as a MailError, with a fixed message. `delivery_unknown` means the message
    may have gone out; any other code that it did not. The log gets the error's type and reply
    code, not its text: that echoes the server, and a login's can quote the password."""
    if not entered_data:
        mapped = _before_data(error, aborted)
    elif _refused_after_data(error):
        mapped = MailError("server_error")
    else:
        mapped = MailError("delivery_unknown")
    _log.warning(
        "smtp_error",
        extra={
            "event": "smtp_error",
            "account": account,
            "mapped": mapped.code,
            "error_type": type(error).__name__,
            "smtp_code": getattr(error, "smtp_code", None),
            "entered_data": entered_data,
            "aborted": aborted,
        },
    )
    return mapped


class SmtpSender:
    """Sends for the accounts `account` knows, with the passwords `password` gives. Stateless
    between sends, so a host whose accounts change needs to do nothing. `ssl_context` defaults to
    `ssl.create_default_context()`, which verifies the server's certificate and name; only tests
    pass one that does not."""

    def __init__(
        self,
        account: Callable[[str], AccountConfig | None],
        password: Callable[[str], str | None],
        *,
        ssl_context: ssl.SSLContext | None = None,
        timeouts: SmtpTimeouts = SmtpTimeouts(),  # noqa: B008  (frozen)
    ) -> None:
        """Both lookups take an account id and give None for one they do not know."""
        self._lookup_account = account
        self._lookup_password = password
        self._ssl_context = ssl_context if ssl_context is not None else ssl.create_default_context()
        self._timeouts = timeouts

    def send(
        self, account_id: str, envelope: Envelope, raw: bytes, transfer: Transfer | None = None
    ) -> Delivery:
        """Sends `raw` to the envelope's recipients, each named once, from a connection of its
        own. Never retried. `transfer`, when given, lets the caller abort it; the watchdog runs
        either way."""
        account = self._lookup_account(account_id)
        if account is None:
            raise MailError("invalid_argument", f'Unknown account "{account_id}".')
        password = self._lookup_password(account_id)
        if password is None:
            raise RuntimeError(f'no password for account "{account_id}"')
        running = Transfer() if transfer is None else transfer
        connection = _Connection(self._timeouts)
        with watched(running, self._timeouts.watchdog):
            try:
                connection.open_to(account.smtp, self._ssl_context, running)
                connection.log_in(account.login, password)
                refused = connection.transmit(
                    envelope.sender, list(dict.fromkeys(envelope.recipients)), raw
                )
            except Exception as error:
                connection.close()
                raise smtp_error(
                    error,
                    account_id,
                    entered_data=connection.entered_data,
                    aborted=running.aborted,
                ) from None
            connection.goodbye()
        return Delivery(refused)


def verify_smtp(
    account: AccountConfig,
    password: str,
    *,
    ssl_context: ssl.SSLContext | None = None,
    timeouts: SmtpTimeouts = SmtpTimeouts(),  # noqa: B008  (frozen)
) -> MailErrorCode | None:
    """Whether the account's SMTP server takes this login: connects, starts TLS and logs in,
    sending nothing, in one attempt. The error's code, None when all went well."""
    context = ssl_context if ssl_context is not None else ssl.create_default_context()
    transfer = Transfer()
    connection = _Connection(timeouts)
    with watched(transfer, timeouts.watchdog):
        try:
            connection.open_to(account.smtp, context, transfer)
            connection.log_in(account.login, password)
        except Exception as error:
            connection.close()
            return smtp_error(error, account.id, aborted=transfer.aborted).code
        connection.goodbye()
    return None
