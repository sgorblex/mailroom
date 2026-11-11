"""One message (`src/mcp/tools/read.ts` and `attachment.ts`): its view, one part's content,
and its HTML made safe to show. Every fetch is `BODY.PEEK` under EXAMINE: reading never sets
`\\Seen`."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import unquote

from mailroom_core.mail._mime import addresses, aware_date, header_text, text_value
from mailroom_core.mail.content_type import content_type
from mailroom_core.mail.download import body_text, part_content
from mailroom_core.mail.errors import MailError
from mailroom_core.mail.ref import MessageRef, check_fresh, format_ref, parse_ref
from mailroom_core.mail.structure import MimePart, Structure, analyse_structure
from mailroom_core.render import (
    Address,
    HeaderView,
    Marker,
    PartInfo,
    RenderedBody,
    SafeHtml,
    default_marker,
    format_size,
    render_body,
    safe_html,
)
from mailroom_core.render.safe_html import RASTER_TYPES

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from imapclient import IMAPClient

    from mailroom_core.mail.imap import MailboxContext, MailConnections

MAX_BODY_BYTES: Final = 5 * 1024 * 1024
"""A body part bigger than this is cut before rendering; no reader would get past it."""
_PART: Final = re.compile(r"[0-9]+(?:\.[0-9]+)*", re.ASCII)
_CID: Final = re.compile(r"""cid:([^"'\s>)]+)""", re.IGNORECASE)


def _message(ref: str | MessageRef) -> MessageRef:
    return parse_ref(ref) if isinstance(ref, str) else ref


def fetch_structure(
    client: IMAPClient, uid: int, items: list[str]
) -> tuple[dict[bytes, object], Structure]:
    """The message's BODYSTRUCTURE analysed, with the other `items` fetched alongside it."""
    data = client.fetch([uid], ["BODYSTRUCTURE", *items]).get(uid)
    if data is None:
        raise MailError("not_found")
    body = data.get(b"BODYSTRUCTURE")
    if not isinstance(body, tuple | list):
        raise MailError("not_found")
    return data, analyse_structure(body)


def fetched_headers(data: Mapping[bytes, object]) -> bytes:
    """The header block of a `BODY.PEEK[HEADER.FIELDS (…)]` fetched with a message; empty when
    the server sent none."""
    return next(
        (
            value
            for key, value in data.items()
            if isinstance(key, bytes)
            and key.startswith(b"BODY[HEADER.FIELDS")
            and isinstance(value, bytes)
        ),
        b"",
    )


@dataclass(frozen=True, slots=True)
class MessageView:
    """A message as `read` shows it. Header text is decoded and flattened, but still the
    sender's words: escape it, or put it in the envelope."""

    ref: str
    account: str
    folder: str
    uid: int
    sender: tuple[Address, ...]
    to: tuple[Address, ...]
    cc: tuple[Address, ...]
    reply_to: tuple[Address, ...]
    date: datetime | None
    """The Date header, else the arrival time."""
    subject: str
    """`""` when absent."""
    message_id: str | None
    flags: frozenset[str]
    size: int
    body: RenderedBody | None
    """The body part (text/plain, else HTML as text) rendered and windowed; None when the
    message has no text body."""
    body_format: Literal["text", "html"] | None
    has_html: bool
    """The message has an HTML body: `html_view` shows it."""
    has_remote_content: bool | None
    """That HTML loads remote images (what `remote=True` would show); None unless the read
    asked for `remote_check`."""
    attachments: tuple[PartInfo, ...]
    """Sizes are decoded sizes, estimated (`MimePart.decoded_size`)."""
    inline_parts: tuple[PartInfo, ...]
    """What the HTML shows in place (`Structure.inline_parts`): not attachments."""
    calendar: PartInfo | None
    raw_headers: str | None
    """The header section as received, when asked for."""

    def header_view(self) -> HeaderView:
        """The header fields a read shows."""
        return HeaderView(
            sender=self.sender,
            to=self.to,
            cc=self.cc,
            date=self.date,
            subject=self.subject or None,
            attachments=self.attachments,
            calendar=self.calendar,
        )


def _html(client: IMAPClient, uid: int, structure: Structure) -> str | None:
    if structure.html is None:
        return None
    data = part_content(client, uid, structure.html, MAX_BODY_BYTES)
    return body_text(data, structure.html, complete=len(data) < MAX_BODY_BYTES)


def read_message(
    connections: MailConnections,
    ref: str | MessageRef,
    *,
    offset: int = 0,
    include_quoted: bool = False,
    body_chars: int = 6000,
    raw_headers: bool = False,
    remote_check: bool = False,
    marker: Marker | None = default_marker,
) -> MessageView:
    """One message: headers, attachment list, and one window of `body_chars` (UTF-16 units) of
    its body from `offset`, quoted history and signatures collapsed unless `include_quoted`
    (`marker` as for `render_body`). `remote_check` tells whether the HTML has remote content,
    which costs a sanitising pass over it and, when the HTML is not the body shown, its
    download: off by default, as the TypeScript's read does neither. Rendering costs about a
    second per megabyte of HTML: call it from a worker thread, as every call here."""
    message = _message(ref)
    if offset < 0:
        raise MailError("invalid_argument", "offset must be at least 0.")
    if body_chars < 1:
        raise MailError("invalid_argument", "body_chars must be at least 1.")

    def run(context: MailboxContext) -> MessageView:
        check_fresh(message, context.uid_validity)
        client = context.client
        items = ["ENVELOPE", "FLAGS", "INTERNALDATE", "RFC822.SIZE"]
        if raw_headers:
            items.append("BODY.PEEK[HEADER]")
        data, structure = fetch_structure(client, message.uid, items)
        envelope = data.get(b"ENVELOPE")
        body: RenderedBody | None = None
        html: str | None = None
        fmt = structure.body_format
        if structure.body is not None and fmt is not None:
            content = part_content(client, message.uid, structure.body, MAX_BODY_BYTES)
            text = body_text(content, structure.body, complete=len(content) < MAX_BODY_BYTES)
            if fmt == "html":
                html = text
            body = render_body(
                text,
                fmt,
                include_quoted=include_quoted,
                offset=offset,
                max_chars=body_chars,
                marker=marker,
            )
        has_remote: bool | None = None
        if remote_check:
            if html is None:
                html = _html(client, message.uid, structure)
            has_remote = (
                html is not None
                and safe_html(html, inline_images={}, remote=False, max_inline_bytes=0).has_remote
            )
        header = data.get(b"BODY[HEADER]")
        flags = data.get(b"FLAGS")
        size = data.get(b"RFC822.SIZE")
        own = getattr(envelope, "message_id", None)
        return MessageView(
            ref=format_ref(message),
            account=message.account,
            folder=message.folder,
            uid=message.uid,
            sender=addresses(getattr(envelope, "from_", None)),
            to=addresses(getattr(envelope, "to", None)),
            cc=addresses(getattr(envelope, "cc", None)),
            reply_to=addresses(getattr(envelope, "reply_to", None)),
            date=aware_date(getattr(envelope, "date", None))
            or aware_date(data.get(b"INTERNALDATE")),
            subject=header_text(getattr(envelope, "subject", None)),
            message_id=None if own is None else header_text(own) or None,
            flags=frozenset(
                text_value(f) for f in (flags if isinstance(flags, tuple | list) else ())
            ),
            size=size if isinstance(size, int) else 0,
            body=body,
            body_format=fmt,
            has_html=structure.html is not None,
            has_remote_content=has_remote,
            attachments=structure.attachments,
            inline_parts=structure.inline_parts,
            calendar=structure.calendar,
            raw_headers=(text_value(header) if raw_headers and isinstance(header, bytes) else None),
        )

    return connections.with_mailbox(message.account, message.folder, True, run)


@dataclass(frozen=True, slots=True)
class Attachment:
    """One attachment's content, transfer encoding decoded."""

    part: str
    name: str | None
    """The sender's file name, decoded but not cleaned."""
    declared_type: str
    """The type the sender declared, lowercase."""
    type: str
    """The type to serve it as: `content_type`, the bytes' signature first."""
    data: bytes
    encoded_size: int
    """As BODYSTRUCTURE reported it: what was transferred, not the size to show."""

    @property
    def size(self) -> int:
        """The decoded size, exact: the one to show (listings show its estimate)."""
        return len(self.data)


def _check_part(part: str) -> None:
    if not _PART.fullmatch(part):
        raise MailError("invalid_argument", 'A part number is like "2" or "1.2".')


def clearly_over(part: MimePart, max_bytes: int) -> bool:
    """Whether the part can be refused before any download: base64 lines shorter than 76
    characters make the estimate up to 1% high. Quoted-printable has no estimate."""
    margin = max_bytes + max_bytes // 50 + 4
    return part.encoding != "quoted-printable" and part.decoded_size > margin


def read_attachment(
    connections: MailConnections, ref: str | MessageRef, part: str, *, max_bytes: int
) -> Attachment:
    """An attachment or the calendar part of a message, at most `max_bytes` (`too_large`
    beyond); `not_found` for any other part, the body included."""
    message = _message(ref)
    _check_part(part)

    def run(context: MailboxContext) -> Attachment:
        check_fresh(message, context.uid_validity)
        _data, structure = fetch_structure(context.client, message.uid, [])
        info = next((p for p in structure.downloadable if p.part == part), None)
        leaf = structure.parts.get(part)
        if info is None or leaf is None:
            raise MailError("not_found", "That part is not an attachment of this message.")
        too_large = MailError(
            "too_large",
            f"The attachment exceeds the {format_size(max_bytes)} limit "
            f"(about {format_size(leaf.decoded_size)}).",
        )
        if clearly_over(leaf, max_bytes):
            raise too_large
        # One byte over the cap is enough to know it is too large.
        data = part_content(context.client, message.uid, leaf, max_bytes + 1)
        if len(data) > max_bytes:
            raise too_large
        return Attachment(
            part=part,
            name=info.name,
            declared_type=info.type,
            type=content_type(data, info.type, info.name),
            data=data,
            encoded_size=leaf.size,
        )

    return connections.with_mailbox(message.account, message.folder, True, run)


_GENERIC_TYPES: Final = frozenset({"application/octet-stream", "application/unknown", "/"})


def _maybe_raster(declared: str) -> bool:
    """Whether a part declared `declared` may be a raster image: a raster type, any other image
    type but SVG (a mislabelled JPEG, say), or a generic type."""
    if declared in RASTER_TYPES or declared in _GENERIC_TYPES:
        return True
    return declared.startswith("image/") and declared != "image/svg+xml"


def html_view(
    connections: MailConnections,
    ref: str | MessageRef,
    *,
    remote: bool,
    max_inline_bytes: int,
) -> SafeHtml:
    """The message's HTML body sanitised for display (`render.safe_html`), its `cid:` images
    inlined up to `max_inline_bytes` in total; `not_found` when it has no HTML body. Only raster
    parts the HTML refers to are downloaded."""
    message = _message(ref)

    def run(context: MailboxContext) -> SafeHtml:
        check_fresh(message, context.uid_validity)
        client = context.client
        _data, structure = fetch_structure(client, message.uid, [])
        html = _html(client, message.uid, structure)
        if html is None:
            raise MailError("not_found", "This message has no HTML body.")
        wanted = {
            unquote(match.group(1)).strip().removeprefix("<").removesuffix(">")
            for match in _CID.finditer(html)
        }
        images: dict[str, tuple[str, bytes]] = {}
        budget = max_inline_bytes
        for leaf in structure.parts.values():
            if leaf.content_id is None or leaf.content_id not in wanted:
                continue
            if not _maybe_raster(leaf.type) or leaf.content_id in images:
                continue
            # Nothing downloaded that the budget cannot hold (the estimate errs at most 1%).
            if leaf.decoded_size > budget + budget // 50 + 4:
                continue
            data = part_content(client, message.uid, leaf, budget + 1)
            # Typed by its bytes: mailers often declare inline images octet-stream.
            kind = content_type(data, leaf.type, leaf.name)
            if len(data) <= budget and kind in RASTER_TYPES:
                images[leaf.content_id] = (kind, data)
                budget -= len(data)
        return safe_html(
            html, inline_images=images, remote=remote, max_inline_bytes=max_inline_bytes
        )

    return connections.with_mailbox(message.account, message.folder, True, run)
