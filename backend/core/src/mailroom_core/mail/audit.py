"""What a write records about each message it acted on (`src/mail/audit.ts`), and how a write
call's outcome follows from those records (`AuditTrail.outcome` in `src/mcp/tool.ts`).

A host writes the records into its own audit trail, or logs only their counts and codes; they
never carry a message's content: the sender and recipients in clear (an investigation needs
them), the subject only as a hash (docs/core.md, *Safety*).
"""

import hashlib
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Final, Literal

from mailroom_core.mail._mime import addresses, decode_words, text_value
from mailroom_core.mail.errors import MailError
from mailroom_core.render import clean_header

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from mailroom_core.mail.compose import MessageInput
    from mailroom_core.mail.errors import MailErrorCode

type AuditResult = Literal["ok", "internal"] | MailErrorCode
"""How a write ended for one message: `ok` or why not, `internal` when no error code says."""
type AuditOutcome = Literal["ok", "partial", "refused", "failed"]
type Junk = Literal["spam", "ham"]
"""What a move teaches the account's spam filter: into Junk, or out of it."""
type Removal = Literal["expunged", "trashed", "failed"]
"""How a message left its folder once written elsewhere (a draft's old version, or a message
moved to another account): expunged for good, moved to Trash, or still there."""
type Authoriser = Literal["user", "agent"]
"""Who asked for a send: a person's click, or an agent. `send_mode` limits agents only."""
type DeliveryStage = Literal["sent", "drafted", "attempted", "refused", "repeated"]
"""How far a send went: out, into Drafts instead, handed to SMTP without success, stopped by the
gate, or a retry answered from the earlier attempt."""

REFUSALS: Final[frozenset[AuditResult]] = frozenset(
    {
        "invalid_argument",
        "stale_reference",
        "not_found",
        "mailbox_missing",
        "unsupported",
        "too_large",
        "send_disabled",
        "rate_limited",
        "folder_exists",
        "folder_changed",
        # The mailbox lock was never acquired, so nothing was written.
        "busy",
    }
)
"""Errors that stop a write before it reaches the server; anything else means it failed."""


@dataclass(frozen=True, slots=True)
class AuditRecipients:
    """Where an outgoing message goes, in clear, Bcc included."""

    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditItem:
    """One message, or one folder, in a write call's audit record, with what happened to it."""

    account: str
    folder: str | None = None
    """None on a send that was never filed."""
    uid: int | None = None
    message_id: str | None = None
    sender: str | None = None
    """The first From address, without its display name."""
    recipients: AuditRecipients | None = None
    subject_sha256: str | None = None
    attachments: int | None = None
    """How many an outgoing message carries."""
    destination: str | None = None
    """Where a move took it."""
    destination_account: str | None = None
    """On a move to another account, that account: `destination` is its folder."""
    source_account: str | None = None
    """On the copy a move from another account made, that account."""
    source_folder: str | None = None
    """On the copy a move from another account made, the folder it came from."""
    copied: bool | None = None
    """True on a move that failed after copying it: it is in `destination`, and may still be in
    `folder`."""
    junk: Junk | None = None
    seen: bool | None = None
    """The flag change asked for, on a flag write."""
    flagged: bool | None = None
    flag: str | None = None
    """A flag set on its own, as `\\Answered` after a reply."""
    removed: Removal | None = None
    mode: str | None = None
    """The account's `send_mode`, on a send."""
    by: Authoriser | None = None
    delivery: DeliveryStage | None = None
    filed: bool | None = None
    """On a sent message: whether the copy reached Sent."""
    rejected: tuple[str, ...] | None = None
    """Recipients the server refused while taking the others."""
    messages: int | None = None
    """How many messages a deleted folder held, as the caller confirmed."""
    result: AuditResult

    @property
    def record(self) -> dict[str, object]:
        """The fields that are set, for a JSON audit line; `sender` is written as `from` and
        `recipients` as `to`, `cc` and `bcc`, as the TypeScript records them."""
        fields = {key: value for key, value in asdict(self).items() if value is not None}
        if "sender" in fields:
            fields["from"] = fields.pop("sender")
        recipients = fields.pop("recipients", None)
        if isinstance(recipients, dict):
            fields.update(recipients)
        return fields


type Note = Callable[[AuditItem], None]
"""Takes each audit item as it happens, so what was done is on record even if the call fails."""


def subject_sha256(subject: str) -> str:
    """A decoded subject's SHA-256: what an audit record keeps of it, the same for any front end
    that hashes it."""
    return hashlib.sha256(subject.encode("utf-8", "replace")).hexdigest()


def message_item(
    account: str,
    folder: str,
    uid: int,
    envelope: object,
    result: AuditResult,
    *,
    destination: str | None = None,
    destination_account: str | None = None,
    source: tuple[str, str] | None = None,
    copied: bool | None = None,
    junk: Junk | None = None,
    seen: bool | None = None,
    flagged: bool | None = None,
    removed: Removal | None = None,
) -> AuditItem:
    """The record of one message whose ENVELOPE (imapclient's) was fetched: enough to find it
    again and see who sent it; what the envelope lacks is left out. `source` is the account and
    folder a copy made by a move from another account came from."""
    message_id = getattr(envelope, "message_id", None)
    sender = next(iter(addresses(getattr(envelope, "from_", None))), None)
    subject = getattr(envelope, "subject", None)
    return AuditItem(
        account=account,
        folder=folder,
        uid=uid,
        message_id=None if message_id is None else clean_header(text_value(message_id), 300),
        sender=None if sender is None else sender.address or None,
        # Hashed as imapflow hands the subject over: encoded words decoded, nothing else changed.
        subject_sha256=(
            None if subject is None else subject_sha256(decode_words(text_value(subject)))
        ),
        destination=destination,
        destination_account=destination_account,
        source_account=None if source is None else source[0],
        source_folder=None if source is None else source[1],
        copied=copied,
        junk=junk,
        seen=seen,
        flagged=flagged,
        removed=removed,
        result=result,
    )


def outgoing_item(
    account: str,
    message: MessageInput,
    result: AuditResult,
    *,
    folder: str | None = None,
    message_id: str | None = None,
) -> AuditItem:
    """The record of an outgoing message: who it is from and to in clear, the subject hashed."""
    return AuditItem(
        account=account,
        folder=folder,
        message_id=message_id,
        sender=message.sender.address,
        recipients=AuditRecipients(
            to=tuple(m.address for m in message.to),
            cc=tuple(m.address for m in message.cc),
            bcc=tuple(m.address for m in message.bcc),
        ),
        subject_sha256=subject_sha256(message.subject),
        attachments=len(message.attachments),
        result=result,
    )


def audit_result(error: Exception) -> AuditResult:
    """What an exception records: its code, `internal` when it has none."""
    return error.code if isinstance(error, MailError) else "internal"


def audit_outcome(results: Iterable[AuditResult], error: AuditResult | None = None) -> AuditOutcome:
    """A call's outcome from its items' results and the error it ended with, if any: `partial`
    when anything succeeded, else `refused` when every failure stopped before a write."""
    seen = list(results)
    failures = [result for result in seen if result != "ok"] + ([] if error is None else [error])
    if not failures:
        return "ok"
    if "ok" in seen:
        return "partial"
    return "refused" if all(code in REFUSALS for code in failures) else "failed"
