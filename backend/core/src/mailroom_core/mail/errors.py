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
    "server_error": "The mail server reported an error.",
}

_log = logging.getLogger("mailroom_core.mail")


class MailError(Exception):
    """An error safe to show: a code and its fixed message, optionally with a detail we wrote.

    `detail` must never carry text from a mail server or a message.
    """

    code: MailErrorCode
    detail: str | None

    def __init__(self, code: MailErrorCode, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(MESSAGES[code] if detail is None else f"{MESSAGES[code]} {detail}")

    @property
    def message(self) -> str:
        return str(self)


def mail_error(error: BaseException, account: str, *, secret: bool = False) -> MailError:
    """`error` as a MailError; a MailError passes through as it is.

    The original goes to the log with the account id: its text may echo server responses, so it
    never reaches the MailError. An error that is neither an IMAP nor a socket error is a bug
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
    return MailError(code)


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
        if "[AUTHENTICATIONFAILED]" in text:
            return "auth_failed"
        # NONEXISTENT on SELECT, EXAMINE and STATUS; TRYCREATE on APPEND, COPY and MOVE.
        if "[NONEXISTENT]" in text or "[TRYCREATE]" in text:
            return "mailbox_missing"
    return "server_error"
