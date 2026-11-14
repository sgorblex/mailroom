"""The caps every written message shares, wherever it comes from (the input schema of
`src/mcp/tools/outgoing.ts`): a host checks them before reading anything from the mailbox."""

from typing import TYPE_CHECKING, Final

from mailroom_core.mail.errors import MailError
from mailroom_core.render import format_size
from mailroom_core.render._js import Utf16Index

if TYPE_CHECKING:
    from collections.abc import Iterable, Sized

    from mailroom_core.config import AccountConfig, Limits

MAX_ADDRESSES: Final = 50
"""Per field, before `Limits.max_recipients` bounds the message."""
MAX_TEXT_CHARS: Final = 100_000
MAX_SUBJECT_CHARS: Final = 998
MAX_UPLOADS: Final = 10


def attachment_cap(limits: Limits) -> int:
    """The bytes all of a message's attachments may total."""
    return int(limits.attachment_max_mb * 1024 * 1024)


def move_cap(limits: Limits) -> int:
    """The bytes a message moved to another account may have."""
    return int(limits.move_max_mb * 1024 * 1024)


def check_sendable(account: AccountConfig) -> None:
    """`send_disabled` on an account whose `send_mode` is `off`: no drafts or sends."""
    if account.send_mode == "off":
        raise MailError("send_disabled")


def attachments_too_large(cap: int) -> MailError:
    """The error for attachments that together pass ``cap`` bytes."""
    return MailError("too_large", f"Attachments may total {format_size(cap)} per message.")


def check_fields(
    *,
    to: Sized = (),
    cc: Sized = (),
    bcc: Sized = (),
    subject: str = "",
    text: str = "",
) -> None:
    """Refuses a message past the caps on its fields; lengths in UTF-16 units, as the TypeScript
    schema counts them. Takes the fields as given, so nothing is parsed past a cap."""
    if max(len(to), len(cc), len(bcc)) > MAX_ADDRESSES:
        raise MailError("invalid_argument", f"At most {MAX_ADDRESSES} addresses per field.")
    if Utf16Index(subject).length > MAX_SUBJECT_CHARS:
        raise MailError("invalid_argument", f"A subject is at most {MAX_SUBJECT_CHARS} characters.")
    if Utf16Index(text).length > MAX_TEXT_CHARS:
        raise MailError("invalid_argument", f"A message is at most {MAX_TEXT_CHARS} characters.")


def check_uploads(sizes: Iterable[int], cap: int) -> None:
    """Refuses more than `MAX_UPLOADS` files, or files past `cap` bytes together."""
    listed = list(sizes)
    if len(listed) > MAX_UPLOADS:
        raise MailError("invalid_argument", f"At most {MAX_UPLOADS} attachments per message.")
    if sum(listed) > cap:
        raise attachments_too_large(cap)
