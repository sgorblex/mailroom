"""RFC 5322 messages from tool input (`src/mail/compose.ts`, nodemailer's MailComposer there).

The MIME body comes from the `email` package. The message's own headers are written here: the
package's header classes parse their input, so an encoded word in a subject or a name would be
decoded back into the header, CR/LF included, and they fold a Message-ID too long for a line into
an encoded word, which is no Message-ID any more.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.header import Header
from email.message import EmailMessage
from email.utils import format_datetime, quote
from typing import TYPE_CHECKING, Final

from mailroom_core.mail._mime import header_values, well_formed
from mailroom_core.mail.errors import MailError
from mailroom_core.mail.outgoing import MAX_HTML_BYTES
from mailroom_core.mail.recipients import Mailbox, deliverable_address
from mailroom_core.mail.ref import MessageRef, format_ref, parse_ref
from mailroom_core.render import clean_header, html_to_text, outgoing_html

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

DRAFT_HEADER: Final = "X-Mailroom-Draft"
"""Marks a draft mailroom wrote, so only those are ever expunged when replaced (docs/core.md,
*Safety*). Its value is `1`, `1; answers=<ref>` when sending the draft is to set \\Answered on
that message, or `1; forwards=<ref>` when it is to set `FORWARDED` on it."""

_POLICY: Final = policy.SMTP.clone(max_line_length=76)
_MAX_LINE: Final = 78
_ENCODED_WORD_START: Final = "=?"
_MAX_ID: Final = 900
"""A Message-ID longer than this would leave no room for its field name in a line of 998."""
_ID_CHAR: Final = "[!-;=?-~]"
_ID: Final = re.compile(f"<{_ID_CHAR}+@{_ID_CHAR}+>")
"""What a Message-ID looks like: no spaces, angle brackets, controls or anything but ASCII."""
_DRAFT_VALUE: Final = re.compile(r"1(?:;[ \t]*(answers|forwards)=([A-Za-z0-9_-]+))?")
_PART_TYPE: Final = re.compile(r"[a-z0-9][\w.+-]*/[\w.+-]+", re.ASCII)
_NOT_BASE64: Final = ("multipart/", "message/")
"""A multipart needs a boundary and a message/* may not be base64 (RFC 2046, 5.2.1): as
attachments, both go as opaque bytes."""
_PHRASE: Final = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~ -]+")
"""Atoms and the single spaces `clean_header` leaves: a display name that needs no quotes."""
_CONTENT_ID: Final = re.compile(r"[\w!#$&'*+./=@^`{|}~-]{1,250}", re.ASCII)
"""A Content-ID that goes in a header as it is: no `?`, so never an encoded word, and nothing a
`cid:` URL would need to escape, `%` included: the URL is unquoted when read."""
_SEPARATOR: Final = re.compile(rb"\r?\n\r?\n")
_LINE_START: Final = re.compile(rb"(?<=\n)")
_LAST_BREAK: Final = re.compile(rb"\r?\n\Z")
# The last branch takes a whole run of CRs: one at a time, `\r+\n` would rescan the run from each.
_BREAKS: Final = re.compile(rb"\r+\n|\n\r?|\r+")
_BARE_CR: Final = re.compile(rb"\r(?!\n)")


@dataclass(frozen=True, slots=True)
class OutgoingAttachment:
    """A file to attach to an outgoing message: its name, media type and bytes."""

    filename: str
    content_type: str
    content: bytes
    content_id: str | None = None
    """Set on an inline part: what the HTML shows as `cid:<content_id>` (angle brackets
    optional). It goes with an inline disposition, only while the HTML refers to it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageInput:
    """What a message is composed from: identity, recipients, subject, text or HTML, and
    attachments."""

    sender: Mailbox
    """The From identity (`from_identity`)."""
    to: tuple[Mailbox, ...] = ()
    cc: tuple[Mailbox, ...] = ()
    bcc: tuple[Mailbox, ...] = ()
    subject: str
    text: str
    html: str | None = None
    """The body as HTML, sanitised when composed (`render.outgoing_html`); the text part is then
    derived from it and `text` is not used."""
    in_reply_to: str | None = None
    """The Message-ID answered; with `references`, the thread so far."""
    references: tuple[str, ...] = ()
    attachments: tuple[OutgoingAttachment, ...] = ()
    draft: bool = False
    answers: str | None = None
    """On a draft: the ref of the message it answers."""
    forwards: str | None = None
    """On a draft: the ref of the message it forwards; never with `answers`."""


@dataclass(frozen=True, slots=True)
class Envelope:
    """SMTP envelope: the identity, and every recipient Bcc included."""

    sender: str
    recipients: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Composed:
    """A composed message: the bytes to file, the bytes to send, its Message-ID and envelope."""

    stored: bytes
    """With its Bcc header and, on a draft, its marker: what is filed in Drafts or Sent."""
    wire: bytes
    """Without either: what goes over SMTP, so Bcc recipients stay hidden from the others."""
    message_id: str
    envelope: Envelope


def new_message_id(address: str, compose_id: str | None = None) -> str:
    """A Message-ID in the domain of `address`: built on `compose_id`, so each version of a draft
    keeps it, else random."""
    return f"<{compose_id or uuid.uuid4()}@{address.rpartition('@')[2]}>"


def _is_id(value: str) -> bool:
    return len(value) <= _MAX_ID and _ID.fullmatch(value) is not None


def _words(name: str, words: Iterable[str]) -> bytes:
    """A header of ASCII words, folded only between them."""
    start = f"{name}:"
    lines = [start]
    for word in words:
        if lines[-1] != start and len(lines[-1]) + 1 + len(word) > _MAX_LINE:
            lines.append("")
        lines[-1] += f" {word}"
    return "\r\n".join(lines).encode("ascii") + b"\r\n"


def _is_plain(text: str) -> bool:
    return text.isascii() and _ENCODED_WORD_START not in text


def _encoded_words(name: str, text: str) -> list[str]:
    """`text` as RFC 2047 encoded words, whatever it holds: decoded once, it reads as written."""
    return Header(text, "utf-8", maxlinelen=_MAX_LINE - 2, header_name=name).encode().split()


def _subject(subject: str) -> bytes:
    words = subject.split(" ")
    if _is_plain(subject) and max(map(len, words)) <= _MAX_LINE - 2:
        return _words("Subject", words)
    return _words("Subject", _encoded_words("Subject", subject))


def _mailbox_words(field: str, mailbox: Mailbox) -> list[str]:
    name = clean_header(well_formed(mailbox.name or ""), 100)
    if not name:
        return [mailbox.address]
    if not _is_plain(name):
        return [*_encoded_words(field, name), f"<{mailbox.address}>"]
    phrase = name.split(" ") if _PHRASE.fullmatch(name) else [f'"{quote(name)}"']
    return [*phrase, f"<{mailbox.address}>"]


def _addresses(name: str, mailboxes: Sequence[Mailbox]) -> bytes:
    if not mailboxes:
        return b""
    entries = [_mailbox_words(name, mailbox) for mailbox in mailboxes]
    for entry in entries[:-1]:
        entry[-1] += ","
    return _words(name, [word for entry in entries for word in entry])


def _deliverable(mailboxes: Iterable[Mailbox]) -> tuple[Mailbox, ...]:
    return tuple(Mailbox(deliverable_address(m.address), m.name) for m in mailboxes)


def _part_type(declared: str) -> tuple[str, str]:
    kind = declared.partition(";")[0].strip().lower()
    if not _PART_TYPE.fullmatch(kind) or kind.startswith(_NOT_BASE64):
        kind = "application/octet-stream"
    main, _, sub = kind.partition("/")
    return main, sub


def _filename(name: str) -> str:
    # Not an encoded word when the package parses it back: see the module's docstring.
    return clean_header(well_formed(name), 200).replace(_ENCODED_WORD_START, "= ?")


def _inline(message: MessageInput) -> dict[str, OutgoingAttachment]:
    """The inline parts by Content-ID; one that could not go in a header as it is has nothing
    the HTML may show, and is left out."""
    parts = {}
    for attachment in message.attachments:
        if attachment.content_id is None:
            continue
        cid = attachment.content_id.strip().removeprefix("<").removesuffix(">")
        if _CONTENT_ID.fullmatch(cid):
            parts[cid] = attachment
    return parts


def html_text(html: str) -> str:
    """The text part a message whose HTML body is `html` carries."""
    cleaned = outgoing_html(well_formed(html), data_images=False).html
    return html_to_text(cleaned, False, max_chars=2 * MAX_HTML_BYTES).text


def _html_body(body: EmailMessage, html: str, message: MessageInput) -> None:
    """multipart/alternative of the text derived from the sanitised HTML and that HTML, wrapped
    in a multipart/related with the parts it shows in place, if any."""
    inline = _inline(message)
    cleaned = outgoing_html(well_formed(html), content_ids=inline)
    parts = [
        *((cid, a.content_type, a.content, a.filename) for cid, a in inline.items()),
        *((i.content_id, i.content_type, i.content, "") for i in cleaned.images),
    ]
    shown = [part for part in parts if part[0] in cleaned.content_ids]
    # The package converts only the other way round: related into alternative, not this.
    alternative = EmailMessage(policy=_POLICY) if shown else body
    alternative.set_content(
        # Room for the entities sanitising writes out, as drafts.MAX_DRAFT_HTML_BYTES has.
        html_to_text(cleaned.html, False, max_chars=2 * MAX_HTML_BYTES).text,
        charset="utf-8",
        cte="quoted-printable",
    )
    alternative.add_alternative(
        cleaned.html, subtype="html", charset="utf-8", cte="quoted-printable"
    )
    if not shown:
        return
    body["MIME-Version"] = "1.0"
    body.make_related()
    # RFC 2387: the type of the root part, which is the first.
    body.set_param("type", "multipart/alternative")
    body.attach(alternative)
    for cid, declared, content, name in shown:
        main, sub = _part_type(declared)
        body.add_related(
            content,
            maintype=main,
            subtype=sub,
            cid=f"<{cid}>",
            disposition="inline",
            filename=_filename(name) or None,
        )


def _body(message: MessageInput) -> bytes:
    """The MIME headers and the body: the text as UTF-8 quoted-printable, or with `html` the
    alternatives and their inline parts (`_html_body`), then the attachments in base64, whatever
    they hold, so they arrive byte for byte. Inline parts go only with HTML that shows them."""
    body = EmailMessage(policy=_POLICY)
    if message.html is None:
        body.set_content(well_formed(message.text), charset="utf-8", cte="quoted-printable")
    else:
        _html_body(body, message.html, message)
    for attachment in message.attachments:
        if attachment.content_id is not None:
            continue
        main, sub = _part_type(attachment.content_type)
        filename = _filename(attachment.filename)
        body.add_attachment(
            attachment.content,
            maintype=main,
            subtype=sub,
            filename=filename or "attachment",
            params={"name": filename or "attachment"},
        )
    return body.as_bytes()


def _draft_mark(message: MessageInput) -> bytes:
    if not message.draft:
        return b""
    if message.answers is not None and message.forwards is not None:
        raise MailError("invalid_argument", "A draft answers or forwards a message, not both.")
    for kind, ref in (("answers", message.answers), ("forwards", message.forwards)):
        if ref is not None:
            return _words(DRAFT_HEADER, ["1;", f"{kind}={format_ref(parse_ref(ref))}"])
    return _words(DRAFT_HEADER, ["1"])


def compose(message: MessageInput, message_id: str | None = None) -> Composed:
    """The message under `message_id`, a new one when None. Every header value is cleaned, so no
    CR/LF can start a new header; threading ids that do not look like Message-IDs are dropped;
    `invalid_argument` on a recipient that is not deliverable, wherever it came from, or a
    malformed `message_id`, `answers` or `forwards`, or both of the last two."""
    chosen = new_message_id(message.sender.address) if message_id is None else message_id
    if not _is_id(chosen):
        raise MailError("invalid_argument", "Not a Message-ID.")
    to, cc, bcc = map(_deliverable, (message.to, message.cc, message.bcc))
    subject = clean_header(well_formed(message.subject), 998)
    in_reply_to = message.in_reply_to
    references = [value for value in message.references if _is_id(value)]
    addressed = b"".join(
        (_addresses("From", [message.sender]), _addresses("To", to), _addresses("Cc", cc))
    )
    rest = b"".join(
        (
            _subject(subject) if subject else b"",
            _words("Message-ID", [chosen]),
            _words("In-Reply-To", [in_reply_to])
            if in_reply_to is not None and _is_id(in_reply_to)
            else b"",
            _words("References", references) if references else b"",
            _words("Date", [format_datetime(datetime.now(UTC))]),
        )
    )
    body = _body(message)
    return Composed(
        stored=b"".join((addressed, _addresses("Bcc", bcc), rest, _draft_mark(message), body)),
        wire=b"".join((addressed, rest, body)),
        message_id=chosen,
        envelope=Envelope(message.sender.address, tuple(m.address for m in (*to, *cc, *bcc))),
    )


def _draft_match(headers: bytes | str) -> re.Match[str] | None:
    for value in header_values(headers, DRAFT_HEADER):
        match = _DRAFT_VALUE.fullmatch(value)
        if match is not None:
            return match
    return None


def is_own_draft(headers: bytes | str) -> bool:
    """Whether a header block (the whole one, or the fetched field) marks a draft mailroom
    wrote, in either form of `DRAFT_HEADER`'s value."""
    return _draft_match(headers) is not None


def _draft_original(headers: bytes | str, kind: str) -> MessageRef | None:
    match = _draft_match(headers)
    if match is None or match.group(1) != kind:
        return None
    try:
        return parse_ref(match.group(2))
    except MailError:
        return None


def draft_answers(headers: bytes | str) -> MessageRef | None:
    """The message a draft answers, from its header block: None when it says none, or says
    something that is no ref."""
    return _draft_original(headers, "answers")


def draft_forwards(headers: bytes | str) -> MessageRef | None:
    """The message a draft forwards, as `draft_answers` reads the one it answers."""
    return _draft_original(headers, "forwards")


def _crlf(breaks: re.Match[bytes]) -> bytes:
    run = breaks.group()
    return b"\r\n" * (run.count(b"\n") or len(run))


def crlf_lines(raw: bytes) -> bytes:
    """`raw` with every line ending in CRLF, which is what the wire will read. An LF is one break
    with the CRs before it and a CR after it, or `\\r\\r\\n` would be a blank line that turns the
    fields after it into body text; every other CR is a break of its own."""
    return _BREAKS.sub(_crlf, raw)


def _header_block(raw: bytes) -> bytes:
    """`raw` through the blank line that ends its headers, not to it: a CR just before that line is
    judged by the LF that follows. All of `raw` when it has no such line."""
    separator = _SEPARATOR.search(raw)
    return raw if separator is None else raw[: separator.end()]


def bare_cr_in_headers(raw: bytes) -> bool:
    """Whether a CR with no LF after it comes before the blank line that ends `raw`'s headers."""
    return _BARE_CR.search(_header_block(raw)) is not None


def field_count(raw: bytes, name: str) -> int:
    """How many `name` fields `raw`'s headers hold, each counted as `without_headers` would find it,
    so `From :` is one too."""
    wanted = name.lower().encode("ascii")
    return sum(
        line[:1] not in (b" ", b"\t") and line.partition(b":")[0].strip().lower() == wanted
        for line in _LINE_START.split(_header_block(raw))
    )


def without_headers(raw: bytes, names: Iterable[str]) -> bytes:
    """The raw message without the named header fields (folded continuation lines included); the
    body is left byte for byte as it was. A line ends at an LF here, so bytes another client wrote
    go through `crlf_lines` first."""
    separator = _SEPARATOR.search(raw)
    end = len(raw) if separator is None else separator.start()
    wanted = {name.lower().encode("ascii") for name in names}
    kept: list[bytes] = []
    dropping = False
    for line in _LINE_START.split(raw[:end]):
        if line[:1] not in (b" ", b"\t"):
            dropping = line.partition(b":")[0].strip().lower() in wanted
        if not dropping:
            kept.append(line)
    # The separator that follows brings its own line break: the last kept line must not add one.
    return _LAST_BREAK.sub(b"", b"".join(kept)) + raw[end:]
