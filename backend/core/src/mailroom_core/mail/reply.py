"""Answering and forwarding a message (`src/mail/reply.ts`): what is read from the original, and
what an answer takes from it. All of it is the sender's text: `quoted_original`,
`forwarded_original` and their HTML forms go in a message the owner sends, not to a model
unwrapped."""

import re
from dataclasses import dataclass, field
from html import escape
from typing import TYPE_CHECKING, Final

from mailroom_core.mail._mime import aware_date, header_fields, header_text, header_values
from mailroom_core.mail.download import body_text, part_content
from mailroom_core.mail.identity import Addressed
from mailroom_core.mail.message_headers import delivery_addresses
from mailroom_core.mail.read import clearly_over, fetch_structure, fetched_headers
from mailroom_core.mail.recipients import envelope_mailboxes, mailbox_text
from mailroom_core.mail.ref import MessageRef, check_fresh
from mailroom_core.mail.selected import store_flags, store_forwarded
from mailroom_core.render import clean_header, format_date, outgoing_html, render_body

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from mailroom_core.config import AccountConfig
    from mailroom_core.mail.imap import MailboxContext, MailConnections
    from mailroom_core.mail.recipients import Mailbox
    from mailroom_core.mail.structure import InlinePart
    from mailroom_core.render import PartInfo

MAX_ORIGINAL_BYTES: Final = 2 * 1024 * 1024
"""Of an original's body: less than a read, since only its start is quoted."""
MAX_QUOTED_HTML_BYTES: Final = 1024 * 1024
"""Of an original's HTML: a longer one is quoted as text, since HTML cannot be cut."""
_MAX_REFERENCES: Final = 20
"""References kept when answering: the most recent ones, as the thread read uses them too."""
MAX_QUOTED_CHARS: Final = 20_000
"""Of an original's text in a quote or a forward: it is cut there."""
_HEADERS: Final = ("REFERENCES", "X-ORIGINAL-TO", "DELIVERED-TO")
_MESSAGE_ID: Final = re.compile(r"<[^<>\s]+@[^<>\s]+>")
_ANSWER_PREFIX: Final = re.compile(r"\s*(?:re|r|aw|sv)\s*:", re.IGNORECASE)
_FORWARD_PREFIX: Final = re.compile(r"\s*(?:fwd?|i|wg|tr)\s*:", re.IGNORECASE)


@dataclass(frozen=True, slots=True, kw_only=True)
class Original:
    """What answering or forwarding a message needs from it."""

    ref: MessageRef
    message_id: str | None = None
    references: tuple[str, ...] = ()
    subject: str = ""
    date: datetime | None = None
    sender: tuple[Mailbox, ...] = ()
    reply_to: tuple[Mailbox, ...] = ()
    to: tuple[Mailbox, ...] = ()
    cc: tuple[Mailbox, ...] = ()
    addressed: Addressed = field(default_factory=Addressed)
    text: str = ""
    """Its text, quotes included, as a read renders it; empty when it has none."""
    html: str | None = None
    """Its HTML as the sender wrote it, unsanitised; None when it has none, a longer one than
    `MAX_QUOTED_HTML_BYTES`, or was not asked for (`original_message`'s `with_html`)."""
    attachments: tuple[PartInfo, ...] = ()
    inline: tuple[InlinePart, ...] = ()
    """The parts its HTML shows in place, which a forward carries as inline `AttachmentSource`s."""


@dataclass(frozen=True, slots=True)
class Threading:
    """The threading headers of an answer."""

    in_reply_to: str | None
    references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReplyRecipients:
    """The To and Cc of a reply."""

    to: tuple[Mailbox, ...]
    cc: tuple[Mailbox, ...]


def _found(pattern: re.Pattern[str], values: Iterable[str]) -> tuple[str, ...]:
    return tuple(found for value in values for found in pattern.findall(value))


def references_in(headers: bytes) -> tuple[str, ...]:
    """The Message-IDs of a header block's References."""
    return _found(_MESSAGE_ID, header_values(headers, "references"))


def original_message(
    connections: MailConnections, ref: MessageRef, *, with_html: bool = False
) -> Original:
    """Reads the message a reply or forward is built on, under EXAMINE, at most
    `MAX_ORIGINAL_BYTES` of its body; with `with_html`, its HTML too, for
    `quoted_original_html` or `forwarded_original_html`."""

    def run(context: MailboxContext) -> Original:
        check_fresh(ref, context.uid_validity)
        client = context.client
        fields = f"BODY.PEEK[HEADER.FIELDS ({' '.join(_HEADERS)})]"
        data, structure = fetch_structure(context, ref.uid, ["ENVELOPE", fields])
        headers = fetched_headers(data)
        text = ""
        content = b""
        fmt = structure.body_format
        if structure.body is not None and fmt is not None:
            content = part_content(client, ref.uid, structure.body, MAX_ORIGINAL_BYTES)
            decoded = body_text(content, structure.body, complete=len(content) < MAX_ORIGINAL_BYTES)
            text = render_body(
                decoded, fmt, include_quoted=True, offset=0, max_chars=MAX_QUOTED_CHARS
            ).text
        html: str | None = None
        part = structure.html if with_html else None
        if part is not None and not clearly_over(part, MAX_QUOTED_HTML_BYTES):
            if part is not structure.body:
                content = part_content(client, ref.uid, part, MAX_QUOTED_HTML_BYTES + 1)
            if len(content) <= MAX_QUOTED_HTML_BYTES:
                html = body_text(content, part, complete=True)
        envelope = data.get(b"ENVELOPE")
        to = envelope_mailboxes(getattr(envelope, "to", None))
        cc = envelope_mailboxes(getattr(envelope, "cc", None))
        own = getattr(envelope, "message_id", None)
        return Original(
            ref=ref,
            message_id=None if own is None else header_text(own) or None,
            references=references_in(headers),
            subject=header_text(getattr(envelope, "subject", None)),
            date=aware_date(getattr(envelope, "date", None)),
            sender=envelope_mailboxes(getattr(envelope, "from_", None)),
            reply_to=envelope_mailboxes(getattr(envelope, "reply_to", None)),
            to=to,
            cc=cc,
            addressed=Addressed(
                to=tuple(m.address for m in to),
                cc=tuple(m.address for m in cc),
                delivered=delivery_addresses(header_fields(headers)),
            ),
            text=text,
            html=html,
            attachments=structure.attachments,
            inline=structure.inline,
        )

    return connections.with_mailbox(ref.account, ref.folder, True, run)


def mark_answered(connections: MailConnections, ref: MessageRef) -> None:
    """Sets \\Answered on the original once a reply to it has gone out."""

    def run(context: MailboxContext) -> None:
        check_fresh(ref, context.uid_validity)
        store_flags(context, [ref.uid], add=["\\Answered"])

    connections.with_mailbox(ref.account, ref.folder, False, run)


def mark_forwarded(connections: MailConnections, ref: MessageRef) -> None:
    """Sets `FORWARDED` on the original once a forward of it has gone out, where the folder keeps
    keywords (`MailboxContext.keywords`); elsewhere, Gmail included, nothing is set."""

    def run(context: MailboxContext) -> None:
        check_fresh(ref, context.uid_validity)
        store_forwarded(context, [ref.uid])

    connections.with_mailbox(ref.account, ref.folder, False, run)


def reply_subject(subject: str) -> str:
    """`Re: subject`, unless it already is a reply in a common language (Re, R, AW, SV)."""
    return subject if _ANSWER_PREFIX.match(subject) else f"Re: {subject}"


def forward_subject(subject: str) -> str:
    """`Fwd: subject`, unless it already is a forward (Fwd, Fw, I, WG, TR)."""
    return subject if _FORWARD_PREFIX.match(subject) else f"Fwd: {subject}"


def threading(original: Original) -> Threading:
    """The thread so far plus the original, most recent kept."""
    if original.message_id is None:
        return Threading(None, original.references[-_MAX_REFERENCES:])
    references = (*original.references, original.message_id)
    return Threading(original.message_id, references[-_MAX_REFERENCES:])


def reply_recipients(
    original: Original, account: AccountConfig, reply_all: bool = False
) -> ReplyRecipients:
    """Who a reply goes to: Reply-To, else From; reply-all adds the original's To and Cc. The
    account's own identities never receive their own reply, and nobody appears twice."""
    own = {identity.address.lower() for identity in account.identities}
    seen: set[str] = set()

    def keep(mailboxes: Iterable[Mailbox]) -> tuple[Mailbox, ...]:
        kept = []
        for mailbox in mailboxes:
            if mailbox.address not in own and mailbox.address not in seen:
                seen.add(mailbox.address)
                kept.append(mailbox)
        return tuple(kept)

    # Answering one's own message (from Sent) goes to whom it was sent to.
    to = keep(original.reply_to or original.sender) or keep(original.to)
    if not reply_all:
        return ReplyRecipients(to, ())
    return ReplyRecipients((*to, *keep(original.to)), keep(original.cc))


def _sender(original: Original) -> str:
    return mailbox_text(original.sender[0]) if original.sender else "someone"


def _attribution(original: Original, time_zone: str) -> str:
    return f"On {format_date(original.date, time_zone)}, {_sender(original)} wrote:"


def _forward_header(original: Original, time_zone: str) -> tuple[str, ...]:
    def listed(mailboxes: Iterable[Mailbox]) -> str:
        return ", ".join(mailbox_text(mailbox) for mailbox in mailboxes)

    cc = (f"Cc: {listed(original.cc)}",) if original.cc else ()
    return (
        "---------- Forwarded message ---------",
        f"From: {_sender(original)}",
        f"Date: {format_date(original.date, time_zone)}",
        f"Subject: {clean_header(original.subject, 998)}",
        f"To: {listed(original.to)}",
        *cc,
    )


def _text_html(text: str) -> str:
    return "<div>" + "<br>".join(escape(line) for line in text.split("\n")) + "</div>"


def _original_html(original: Original, content_ids: Iterable[str]) -> str:
    """Its HTML sanitised as a send would (`render.outgoing_html`), else its text as HTML."""
    if original.html is None:
        return _text_html(original.text)
    return outgoing_html(
        original.html, content_ids=content_ids, data_images=False, signature_mark=False
    ).html


def quoted_original(original: Original, time_zone: str) -> str:
    """The original under an answer: an attribution line, then every line quoted with `> `."""
    quoted = "\n".join(f"> {line}" if line else ">" for line in original.text.split("\n"))
    return f"{_attribution(original, time_zone)}\n{quoted}"


def quoted_original_html(original: Original, time_zone: str) -> str:
    """`quoted_original` in HTML: the attribution line, then the original's HTML (its text when
    it has none) in a `<blockquote type="cite">`. Its images are left out: an answer does not
    carry its inline parts."""
    return (
        f"<div>{escape(_attribution(original, time_zone))}</div>"
        f'<blockquote type="cite">{_original_html(original, ())}</blockquote>'
    )


def forwarded_original(original: Original, time_zone: str) -> str:
    """The original as a forward shows it inline: its main headers, then its text."""
    return "\n".join((*_forward_header(original, time_zone), "", original.text))


def forwarded_original_html(original: Original, time_zone: str) -> str:
    """`forwarded_original` in HTML: its headers, then its HTML (its text when it has none),
    whose images show its `inline` parts, which the forward is to carry."""
    header = _text_html("\n".join(_forward_header(original, time_zone)))
    inline = [part.content_id for part in original.inline]
    return f"{header}<br>{_original_html(original, inline)}"
