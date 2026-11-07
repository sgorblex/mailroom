"""What the tools that write a message share (``mail_draft`` and ``mail_send``): the input fields
as mailroom-mcp's ``draft_*`` and send tools take them, with this server's account naming, and how
a result names where a message goes.

A result says plainly from which identity and to whom, every recipient by field: a hostile
Reply-To then shows up in the transcript. Those addresses can come from an email, so they are
inside the untrusted-content envelope; the sentences around them are ours.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final

from mailroom_core.mail import (
    MAX_ADDRESSES,
    MAX_SUBJECT_CHARS,
    MAX_TEXT_CHARS,
    MAX_UPLOADS,
    MailError,
    mailbox_text,
    recipient_list,
)
from mailroom_core.render import untrusted
from pydantic import Field

from mailroom.errors import mail_refusal
from mailroom.mcp.tool import Arguments
from mailroom.mcp.tools.mail_read import RefText  # noqa: TC001 -- Pydantic reads it at runtime

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mailroom_core.mail import Mailbox

AccountId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        description=(
            "An account id, from accounts_list (optional with a single account, or with a ref)."
        ),
    ),
]
Addresses = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=1000)]],
    Field(
        max_length=MAX_ADDRESSES,
        description="Addresses, each `ana@example.org` or `Ana Rossi <ana@example.org>`.",
    ),
]
Subject = Annotated[str, Field(max_length=MAX_SUBJECT_CHARS)]
Text = Annotated[str, Field(max_length=MAX_TEXT_CHARS, description="The message, plain text.")]
Sender = Annotated[
    str,
    Field(
        min_length=1,
        max_length=1000,
        description=(
            "Send as this identity (accounts_list gives them); by default the one the original "
            "was sent to, else the account's default."
        ),
    ),
]
SENDER: Final = Field(default=None, alias="from")
"""The ``from`` field (a Python keyword: the attribute is ``sender``)."""


class AttachmentRef(Arguments):
    ref: RefText = Field(description="Ref of the message holding the attachment.")
    part: str = Field(
        pattern=r"^\d+(\.\d+)*$",
        max_length=64,
        description='Its part number in that message ("2" or "1.2").',
    )


Attachments = Annotated[
    list[AttachmentRef],
    Field(
        max_length=MAX_UPLOADS,
        description="Attachments of existing messages to attach, by ref and part: never content.",
    ),
]


def keep_of(attachments: Sequence[AttachmentRef] | None) -> list[tuple[str, str]]:
    """The ``(ref, part)`` pairs the service takes."""
    return [(attachment.ref, attachment.part) for attachment in attachments or ()]


def recipients(values: Sequence[str] | None) -> tuple[Mailbox, ...] | None:
    """The addresses given, parsed; None when the field was left out. An entry that is not one
    address refuses the whole call."""
    if values is None:
        return None
    try:
        return recipient_list(values)
    except MailError as error:
        raise mail_refusal(error.code, error.message) from None


def recipients_text(to: Sequence[Mailbox], cc: Sequence[Mailbox], bcc: Sequence[Mailbox]) -> str:
    """Every recipient by field, as results show them: where a message goes is never implicit."""
    fields = (("to", to), ("cc", cc), ("bcc", bcc))
    parts = [f"{label} {', '.join(map(mailbox_text, found))}" for label, found in fields if found]
    return "; ".join(parts) or "no recipients yet"


def who(
    sender: Sequence[Mailbox],
    to: Sequence[Mailbox],
    cc: Sequence[Mailbox],
    bcc: Sequence[Mailbox],
) -> str:
    """``from X, to Y``, inside the envelope."""
    origin = mailbox_text(sender[0]) if sender else "an unknown sender"
    return untrusted(f"from {origin}, {recipients_text(to, cc, bcc)}")
