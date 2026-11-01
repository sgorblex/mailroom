"""Mail over IMAP: connections and error mapping (`src/imap/`), and the mail operations on them."""

from mailroom_core.mail.errors import (
    MAIL_ERROR_CODES,
    MESSAGES,
    MailError,
    MailErrorCode,
    mail_error,
)
from mailroom_core.mail.imap import (
    FOLDER_ROLES,
    Capabilities,
    Folder,
    FolderRole,
    MailboxContext,
    MailConnections,
    Reachability,
    Timeouts,
    retriable,
)

__all__ = [
    "FOLDER_ROLES",
    "MAIL_ERROR_CODES",
    "MESSAGES",
    "Capabilities",
    "Folder",
    "FolderRole",
    "MailConnections",
    "MailError",
    "MailErrorCode",
    "MailboxContext",
    "Reachability",
    "Timeouts",
    "mail_error",
    "retriable",
]
