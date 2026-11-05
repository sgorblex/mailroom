"""What a write records about each message it acted on (`src/mail/audit.ts`), and how a write
call's outcome follows from those records (`AuditTrail.outcome` in `src/mcp/tool.ts`).

A host writes the records into its own audit trail, or logs only their counts and codes; they
never carry a message's content: the sender in clear (an investigation needs it), the subject
only as a hash (DESIGN.md, *Safety*).
"""

import hashlib
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Final, Literal

from mailroom_core.mail._mime import addresses, decode_words, text_value
from mailroom_core.render import clean_header

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mailroom_core.mail.errors import MailErrorCode

type AuditResult = Literal["ok"] | MailErrorCode
"""How a write ended for one message: `ok` or why not."""
type AuditOutcome = Literal["ok", "partial", "refused", "failed"]
type Junk = Literal["spam", "ham"]
"""What a move teaches the account's spam filter: into Junk, or out of it."""

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
        # The mailbox lock was never acquired, so nothing was written.
        "busy",
    }
)
"""Errors that stop a write before it reaches the server; anything else means it failed."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditItem:
    """One message in a write call's audit record, with what happened to it."""

    account: str
    folder: str
    uid: int
    message_id: str | None = None
    sender: str | None = None
    """The first From address, without its display name."""
    subject_sha256: str | None = None
    destination: str | None = None
    """Where a move took it."""
    copied: bool | None = None
    """True on a move that failed after copying it: it is in `destination`, and may still be in
    `folder`."""
    junk: Junk | None = None
    seen: bool | None = None
    """The flag change asked for, on a flag write."""
    flagged: bool | None = None
    result: AuditResult

    @property
    def record(self) -> dict[str, object]:
        """The fields that are set, for a JSON audit line; `sender` is written as `from`, as the
        TypeScript records it."""
        fields = {key: value for key, value in asdict(self).items() if value is not None}
        if "sender" in fields:
            fields["from"] = fields.pop("sender")
        return fields


def message_item(
    account: str,
    folder: str,
    uid: int,
    envelope: object,
    result: AuditResult,
    *,
    destination: str | None = None,
    copied: bool | None = None,
    junk: Junk | None = None,
    seen: bool | None = None,
    flagged: bool | None = None,
) -> AuditItem:
    """The record of one message whose ENVELOPE (imapclient's) was fetched: enough to find it
    again and see who sent it; what the envelope lacks is left out."""
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
        subject_sha256=None
        if subject is None
        else hashlib.sha256(decode_words(text_value(subject)).encode("utf-8")).hexdigest(),
        destination=destination,
        copied=copied,
        junk=junk,
        seen=seen,
        flagged=flagged,
        result=result,
    )


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
