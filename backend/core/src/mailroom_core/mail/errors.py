"""Mail errors with fixed, secret-free messages: the port of `src/imap/errors.ts`.

A `MailError`'s message is the only error wording that reaches a user or a model (docs/core.md,
*Safety*). Whatever imapclient, imaplib or the socket raised goes to the log, with the account
id, by `mail_error`.
"""

import imaplib
import logging
import ssl
from typing import TYPE_CHECKING, Final, Literal, get_args

from imapclient.exceptions import CapabilityError, LoginError

if TYPE_CHECKING:
    from collections.abc import Mapping

type MailErrorCode = Literal[
    "auth_failed",
    "unreachable",
    "timeout",
    "mailbox_missing",
    "unsupported",
    "busy",
    "stale_reference",
    "not_found",
    "invalid_argument",
    "too_large",
    "send_disabled",
    "rate_limited",
    "recipient_refused",
    "delivery_unknown",
    "folder_exists",
    "folder_changed",
    "server_error",
]
MAIL_ERROR_CODES: Final[tuple[MailErrorCode, ...]] = get_args(MailErrorCode.__value__)

MESSAGES: Final[Mapping[MailErrorCode, str]] = {
    "auth_failed": "The mail server rejected the login for this account.",
    "unreachable": "The mail server for this account could not be reached.",
    "timeout": "The mail server did not answer in time.",
    "mailbox_missing": "That folder does not exist on this account.",
    "unsupported": "The mail server does not support this operation.",
    "busy": "The account is busy with another request; try again.",
    "stale_reference": (
        "This message reference is out of date (the folder changed). Search again for a fresh one."
    ),
    "not_found": "No such message or part.",
    "invalid_argument": "Invalid argument.",
    "too_large": "Too large to return.",
    "send_disabled": "Sending is disabled for this account (send_mode off): no drafts or sends.",
    "rate_limited": "The hourly send limit is reached; nothing was sent.",
    "recipient_refused": "The mail server refused the message; nothing was sent.",
    "delivery_unknown": (
        "Delivery could not be confirmed: the message may already have gone out, so it was not "
        "sent again."
    ),
    "folder_exists": "A folder with that name already exists.",
    "folder_changed": "The folder changed since it was read.",
    "server_error": "The mail server reported an error.",
}

CERTIFICATE_DETAIL: Final = "Its TLS certificate did not verify."
"""The detail of an `unreachable` whose reason is a server certificate that did not verify:
fixed, so a host may recognise it at the end of the message."""

_log = logging.getLogger("mailroom_core.mail")


class MailError(Exception):
    """An error safe to show: a code and its fixed message, optionally with a detail we wrote.

    `detail` must never carry text from a mail server or a message.
    """

    code: MailErrorCode
    detail: str | None

    def __init__(self, code: MailErrorCode, detail: str | None = None) -> None:
        """Sets the code and detail: the text is the code's fixed message, then the detail."""
        self.code = code
        self.detail = detail
        super().__init__(MESSAGES[code] if detail is None else f"{MESSAGES[code]} {detail}")

    @property
    def message(self) -> str:
        """The error's text, as ``str(error)``."""
        return str(self)


def mail_error(error: BaseException, account: str, *, secret: bool = False) -> MailError:
    """`error` as a MailError; a MailError passes through as it is.

    The original goes to the log with the account id: its text may echo server responses, so it
    never reaches the MailError. An `unreachable` caused by a certificate that did not verify
    carries `CERTIFICATE_DETAIL`. An error that is neither an IMAP nor a socket error is a bug
    and is logged with its traceback. `secret` (the login step, whose errors can quote the
    credentials, as a UnicodeEncodeError does) logs only the error's type.
    """
    if isinstance(error, MailError):
        return error
    code = _code(error)
    extra: dict[str, object] = {
        "event": "imap_error",
        "account": account,
        "mapped": code,
        "error_type": type(error).__name__,
    }
    if not secret:
        extra["error"] = str(error)
    unexpected = not secret and not is_connection_error(error)
    _log.warning("imap_error", extra=extra, exc_info=error if unexpected else None)
    if code == "unreachable" and _certificate_unverified(error):
        return MailError(code, CERTIFICATE_DETAIL)
    return MailError(code)


def _certificate_unverified(error: BaseException) -> bool:
    """Whether `error`, or an error it was raised from or while handling, is a certificate that
    did not verify (a LoginError wraps what failed during the login)."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def is_connection_error(error: BaseException) -> bool:
    """Whether `error` came from the server or the connection, rather than from a bug."""
    return isinstance(error, imaplib.IMAP4.error | OSError | EOFError)


def _code(error: BaseException) -> MailErrorCode:
    # Order matters: LoginError and CapabilityError are imaplib errors, IMAPClientReadOnlyError
    # (imaplib's `readonly`) is an `abort`, and TimeoutError and the SSL errors are OSErrors.
    if isinstance(error, LoginError):
        # imapclient turns any error during the login into a LoginError, a lost connection too.
        cause = error.__cause__ or error.__context__
        if isinstance(cause, imaplib.IMAP4.abort | OSError | EOFError):
            return _code(cause)
        if _is_temporary(str(error)):
            return "unreachable"
        return "auth_failed"
    if isinstance(error, CapabilityError):
        return "unsupported"
    if isinstance(error, TimeoutError):
        # The connection is unusable after it: MailConnections drops it.
        return "timeout"
    if isinstance(error, ssl.SSLError):
        # A certificate that does not verify, or a failed handshake.
        return "unreachable"
    if isinstance(error, imaplib.IMAP4.readonly):
        return "server_error"
    if isinstance(error, imaplib.IMAP4.abort | OSError | EOFError):
        return "unreachable"
    if isinstance(error, imaplib.IMAP4.error):
        text = str(error)
        if _is_temporary(text):
            return "unreachable"
        if "[AUTHENTICATIONFAILED]" in text:
            return "auth_failed"
        # NONEXISTENT on SELECT, EXAMINE and STATUS; TRYCREATE on APPEND, COPY and MOVE.
        if "[NONEXISTENT]" in text or "[TRYCREATE]" in text:
            return "mailbox_missing"
    return "server_error"


def _is_temporary(text: str) -> bool:
    """Whether a NO answer says the server is down for now (RFC 5530's UNAVAILABLE), as Dovecot
    answers a login while its auth backend is unavailable: no wrong password, worth retrying."""
    return "[UNAVAILABLE]" in text or "Temporary authentication failure" in text
