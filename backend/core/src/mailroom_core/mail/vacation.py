"""A vacation auto-reply (RFC 3834): what decides whether a new message gets one, and the reply.
The host keeps the settings, when the account is away, and who was answered lately; it reads the
message (`incoming_message`), asks `vacation_refusal`, and sends `vacation_reply` through the gate.

Only header fields decide, never the message's text: the sender's own words could otherwise steer
an automatic answer. Every field is the sender's, so the checks only ever refuse more.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from mailroom_core.mail._mime import header_fields, header_text
from mailroom_core.mail.compose import MessageInput
from mailroom_core.mail.errors import MailError
from mailroom_core.mail.read import fetched_headers
from mailroom_core.mail.recipients import Mailbox, deliverable_address, envelope_mailboxes
from mailroom_core.mail.ref import MessageRef, check_fresh
from mailroom_core.mail.reply import references_in

if TYPE_CHECKING:
    from collections.abc import Collection

    from mailroom_core.mail.imap import MailboxContext, MailConnections

LIST_FIELDS: Final = (
    "list-id",
    "list-unsubscribe",
    "list-unsubscribe-post",
    "list-post",
    "list-help",
    "list-subscribe",
    "list-owner",
    "list-archive",
    "mailing-list",
    "x-mailing-list",
)
"""Any one of them makes a message a list's (RFC 2369, 2919, 8058, and older lists' own)."""
_FIELDS: Final = (
    "FROM",
    "RETURN-PATH",
    "REFERENCES",
    "PRECEDENCE",
    "AUTO-SUBMITTED",
    "X-AUTO-RESPONSE-SUPPRESS",
    "X-AUTOREPLY",
    "X-AUTORESPOND",
    *(name.upper() for name in LIST_FIELDS),
)
MAX_HEADER_BYTES: Final = 64 * 1024
_FETCH_ITEM: Final = f"BODY.PEEK[HEADER.FIELDS ({' '.join(_FIELDS)})]<0.{MAX_HEADER_BYTES}>"
_BULK: Final = frozenset({"bulk", "junk", "list"})
_SUPPRESSED: Final = frozenset({"all", "oof", "autoreply"})
"""X-Auto-Response-Suppress values that ask for no out-of-office reply."""
_NO_REPLY_NAMES: Final = frozenset(
    {
        "noreply",
        "donotreply",
        "dontreply",
        "donotrespond",
        "mailerdaemon",
        "postmaster",
        "nobody",
        "bounce",
        "bounces",
        "listserv",
        "majordomo",
        "notifications",
        "notification",
        "autoreply",
        "autoresponder",
    }
)
_NO_REPLY_STARTS: Final = ("noreply", "donotreply", "dontreply", "mailerdaemon", "bounce")
_NO_REPLY_ENDS: Final = ("-request", "-owner", "-bounce", "-bounces", "-noreply")
_SEPARATORS: Final = re.compile(r"[-_.]")
MAX_REFERENCES: Final = 20
"""The newest References kept in a reply: enough to thread it."""

type VacationRefusal = Literal[
    "no_sender",
    "auto_submitted",
    "bulk",
    "mailing_list",
    "no_reply_sender",
    "own_address",
    "not_addressed",
]
"""Why a message gets no vacation reply, in the order `vacation_refusal` asks."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Incoming:
    """A message's header fields as the vacation reply's checks read them. Addresses lowercase."""

    sender: Mailbox | None
    """Its one From mailbox; None without exactly one, or one that is no deliverable address."""
    return_path: str | None = None
    """Return-Path's address, `""` for the null path `<>`; None without the field."""
    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    message_id: str | None = None
    references: tuple[str, ...] = ()
    list_fields: frozenset[str] = frozenset()
    """Which of `LIST_FIELDS` it has."""
    precedence: str | None = None
    """Lowercase."""
    auto_submitted: str | None = None
    """Its keyword, lowercase, parameters left out."""
    response_suppress: frozenset[str] = frozenset()
    """X-Auto-Response-Suppress's values, lowercase."""
    autoreply_marks: bool = False
    """X-Autoreply or X-Autorespond: other responders' marks on what they sent."""


def _keyword(values: list[str] | None) -> str | None:
    """The first field's value up to a `;` or a comment, lowercase."""
    if not values:
        return None
    return re.split(r"[;(\s]", values[0].strip(), maxsplit=1)[0].lower()


def _return_path(values: list[str] | None) -> str | None:
    if not values:
        return None
    value = values[0].strip()
    found = re.search(r"<([^<>]*)>", value)
    return (found.group(1) if found else value).strip().lower()


def _sender(envelope: object, from_fields: int) -> Mailbox | None:
    mailboxes = envelope_mailboxes(getattr(envelope, "from_", None))
    if from_fields != 1 or len(mailboxes) != 1:
        return None
    [mailbox] = mailboxes
    try:
        address = deliverable_address(mailbox.address)
    except MailError:
        return None
    return Mailbox(address.lower(), mailbox.name)


def incoming_of(envelope: object, headers: bytes) -> Incoming:
    """`Incoming` from a message's ENVELOPE (imapclient's) and its `_FIELDS` block. Never
    raises: what cannot be read is left out, which only refuses more."""
    fields = header_fields(headers)
    message_id = header_text(getattr(envelope, "message_id", None)) or None
    suppress = ",".join(fields.get("x-auto-response-suppress", ()))
    return Incoming(
        sender=_sender(envelope, len(fields.get("from", ()))),
        return_path=_return_path(fields.get("return-path")),
        to=tuple(m.address for m in envelope_mailboxes(getattr(envelope, "to", None))),
        cc=tuple(m.address for m in envelope_mailboxes(getattr(envelope, "cc", None))),
        message_id=message_id,
        references=references_in(headers),
        list_fields=frozenset(name for name in LIST_FIELDS if name in fields),
        precedence=_keyword(fields.get("precedence")),
        auto_submitted=_keyword(fields.get("auto-submitted")),
        response_suppress=frozenset(
            word.strip().lower() for word in suppress.split(",") if word.strip()
        ),
        autoreply_marks="x-autoreply" in fields or "x-autorespond" in fields,
    )


def incoming_message(connections: MailConnections, ref: MessageRef) -> Incoming | None:
    """The message `ref` names, read under EXAMINE; None when it is gone. `stale_reference` as
    any ref."""

    def read(context: MailboxContext) -> Incoming | None:
        check_fresh(ref, context.uid_validity)
        data = context.client.fetch([ref.uid], ["ENVELOPE", _FETCH_ITEM]).get(ref.uid)
        if data is None:
            return None
        return incoming_of(data.get(b"ENVELOPE"), fetched_headers(data)[:MAX_HEADER_BYTES])

    return connections.with_mailbox(ref.account, ref.folder, True, read)


def no_reply_address(address: str) -> bool:
    """Whether `address` looks like one nobody reads answers at: no-reply, a mailer daemon, a
    list's request, owner or bounce address."""
    local = address.rpartition("@")[0].lower().partition("+")[0]
    compact = _SEPARATORS.sub("", local)
    return (
        compact in _NO_REPLY_NAMES
        or compact.startswith(_NO_REPLY_STARTS)
        or local.startswith("owner-")
        or local.endswith(_NO_REPLY_ENDS)
    )


def vacation_refusal(
    message: Incoming, *, identities: Collection[str], own: Collection[str]
) -> VacationRefusal | None:
    """Why `message` gets no vacation reply, or None when it does. `identities` are the
    addresses of the account it reached, `own` every address of the user's (both lowercase).

    Refused: no single deliverable From, or a null Return-Path (a bounce); `Auto-Submitted` other
    than `no`, other responders' marks or a suppression of out-of-office replies; `Precedence`
    bulk, junk or list; any list field; a no-reply style sender; one of the user's own addresses;
    a message not addressed in To or Cc to one of the account's identities (a Bcc, a list, a
    forward)."""
    sender = message.sender
    if sender is None or message.return_path == "":
        return "no_sender"
    if (
        (message.auto_submitted is not None and message.auto_submitted != "no")
        or message.autoreply_marks
        or message.response_suppress & _SUPPRESSED
    ):
        return "auto_submitted"
    if message.precedence in _BULK:
        return "bulk"
    if message.list_fields:
        return "mailing_list"
    if no_reply_address(sender.address) or (
        message.return_path is not None and no_reply_address(message.return_path)
    ):
        return "no_reply_sender"
    if sender.address in own:
        return "own_address"
    if not any(address in identities for address in (*message.to, *message.cc)):
        return "not_addressed"
    return None


def vacation_reply(message: Incoming, sender: Mailbox, *, subject: str, html: str) -> MessageInput:
    """The reply to `message` (which `vacation_refusal` let through) from `sender`: to its From
    alone, threaded under it, marked `Auto-Submitted: auto-replied`."""
    if message.sender is None:
        raise MailError("invalid_argument", "This message has no sender to answer.")
    thread = tuple(message.references[-(MAX_REFERENCES - 1) :])
    if message.message_id is not None:
        thread = (*thread, message.message_id)
    return MessageInput(
        sender=sender,
        to=(message.sender,),
        subject=subject,
        text="",
        html=html,
        in_reply_to=message.message_id,
        references=thread,
        auto_replied=True,
    )
