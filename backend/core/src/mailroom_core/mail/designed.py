"""Designed mail (docs/core.md, *Designed mail*): a message built elsewhere, an .eml or an HTML
file with its images and attachments, taken in as a draft as it was built, and the checks shown
before it is sent.

The import keeps the message's MIME structure (alternative, related, mixed) and every part but its
HTML as it came. The HTML is sanitised as the owner's is (`render.outgoing_html`), keeping its
`cid:` references whether or not a part answers them, so that the checks can say so; what that
removed is counted by kind. Its own text part stays, and one is made from the HTML when it has
none. The headers above the body are Mailroom's own (`compose`), never the file's: From is an
identity, the Message-ID is new and the draft marker (`DRAFT_HEADER`'s `designed`) keeps what the
import changed. The draft is then sent as it is stored, like any other (`send.prepare_draft`).
"""

import uuid
from contextlib import suppress
from dataclasses import dataclass, replace
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import TYPE_CHECKING, Final, Literal, cast
from urllib.parse import unquote, urlsplit

from mailroom_core.mail._mime import decode_bytes, well_formed
from mailroom_core.mail.compose import (
    _CONTENT_ID,
    _POLICY,
    _filename,
    _part_type,
    compose,
    crlf_lines,
    draft_designed,
    new_message_id,
)
from mailroom_core.mail.drafts import (
    SavedDraft,
    _check_compose_id,
    check_removable,
    draft_source,
    draft_source_cap,
    store_draft,
)
from mailroom_core.mail.errors import MailError
from mailroom_core.mail.outgoing import (
    MAX_HTML_BYTES,
    MAX_SUBJECT_CHARS,
    attachment_cap,
    attachments_too_large,
    check_fields,
    check_sendable,
)
from mailroom_core.mail.recipients import Mailbox
from mailroom_core.render import (
    clean_header,
    format_size,
    html_images,
    html_to_text,
    image_sources,
    outgoing_html,
    unsafe_parts,
    with_image_sources,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from mailroom_core.config import AccountConfig, Limits
    from mailroom_core.mail.audit import Note
    from mailroom_core.mail.compose import MessageInput, OutgoingAttachment
    from mailroom_core.mail.imap import MailConnections
    from mailroom_core.mail.ref import MessageRef

GMAIL_CLIP_BYTES: Final = 102 * 1024
"""HTML past this many bytes Gmail cuts off, behind a "View entire message" link."""
MAX_IMPORT_PARTS: Final = 100
"""Of an imported message, its multiparts included."""
MAX_IMPORT_DEPTH: Final = 16
"""How deeply an imported message's multiparts may nest."""
_MAX_ITEMS: Final = 20
"""Of the names a finding lists; its count says how many there are."""
_TEXT_CHANGE: Final = "text"
"""The change that is no removal: the text part made from the HTML."""
_DOCUMENT: Final = (
    '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
    "<body>{}</body></html>\n"
)
_KEPT_ROOT_HEADERS: Final = frozenset({"content-type", "content-transfer-encoding"})
_UNREADABLE: Final = "That is not a message Mailroom can import."
_BAD_CONTENT_ID: Final = (
    "A content ID is 1 to 250 letters, digits and the characters !#$&'*+./=@^`{|}~_- only."
)
_IMPORT_ERRORS: Final = (
    AttributeError,
    LookupError,
    RecursionError,
    TypeError,
    UnicodeError,
    ValueError,
)
"""What the `email` package can raise on a malformed message: it is built for mail, not for
input aimed at it."""

type FindingCode = Literal[
    "removed_scripts",
    "removed_forms",
    "removed_handlers",
    "removed_images",
    "removed_data_images",
    "removed_remote",
    "removed_styles",
    "text_derived",
    "image_without_alt",
    "cid_without_part",
    "part_not_referenced",
    "html_clipped",
    "remote_images",
    "no_text_part",
    "too_large",
]


@dataclass(frozen=True, slots=True)
class Finding:
    """Something to look at before sending: `text` in English, `code`, which stays the same, for
    a host that words it itself, how many there are, and the names involved."""

    code: FindingCode
    text: str
    count: int = 1
    items: tuple[str, ...] = ()
    """Content-IDs or file names, from the message itself (so the sender's to trust or not), at
    most 20, each one line."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Built:
    """An imported message, sanitised: its MIME body and what its own headers said."""

    body: bytes
    """Its MIME headers and body, which `compose` puts Mailroom's headers above."""
    to: tuple[Mailbox, ...] = ()
    cc: tuple[Mailbox, ...] = ()
    bcc: tuple[Mailbox, ...] = ()
    subject: str = ""
    changes: tuple[tuple[str, int], ...] = ()
    """What the import changed, by kind (`render.unsafe_parts`' kinds, and `text`), with counts."""
    attachments: int = 0
    """Its files, the parts its HTML shows in place left out."""


@dataclass(frozen=True, slots=True)
class ImportedDraft:
    """An import that went through: the draft as saved, and the checks of what was stored."""

    saved: SavedDraft
    findings: tuple[Finding, ...]


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


_REMOVED: Final[Mapping[str, tuple[FindingCode, str, str]]] = {
    "scripts": (
        "removed_scripts", "script or javascript: link was", "scripts or javascript: links were"
    ),
    "forms": ("removed_forms", "form control was", "form controls were"),
    "handlers": ("removed_handlers", "event handler was", "event handlers were"),
    "images": (
        "removed_images",
        "image that loads from elsewhere was",
        "images that load from elsewhere were",
    ),
    "data": ("removed_data_images", "data: image was", "data: images were"),
    "remote": (
        "removed_remote",
        "other remote resource (a linked style sheet, a frame, an object, a CSS url()) was",
        "other remote resources (linked style sheets, frames, objects, CSS url()s) were",
    ),
    "styles": ("removed_styles", "style sheet was", "style sheets were"),
}  # fmt: skip
_REMOVED_AFTER: Final[Mapping[str, str]] = {
    "images": " Mailroom sends nothing remote, and many mail clients block remote images anyway: "
    "attach each image as an inline part its HTML names by cid:.",
    "data": " Attach each image as an inline part its HTML names by cid:.",
    "styles": " Only inline styles are sent.",
}


def _removal_findings(changes: Mapping[str, int]) -> list[Finding]:
    found = []
    for kind, (code, one, many) in _REMOVED.items():
        count = changes.get(kind, 0)
        if count > 0:
            text = f"{_n(count, one, many)} removed.{_REMOVED_AFTER.get(kind, '')}"
            found.append(Finding(code, text, count))
    if changes.get(_TEXT_CHANGE, 0) > 0:
        found.append(
            Finding("text_derived", "It had no plain-text part: one was made from its HTML.")
        )
    return found


def _items(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(clean_header(value, 200) for value in values[:_MAX_ITEMS])


def _is_multipart(part: EmailMessage) -> bool:
    return part.get_content_maintype() == "multipart" and isinstance(part.get_payload(), list)


def _children(part: EmailMessage) -> list[EmailMessage]:
    return cast("list[EmailMessage]", part.get_payload())


@dataclass(frozen=True, slots=True)
class _Leaf:
    part: EmailMessage
    parent: EmailMessage | None
    related: bool
    """Its parent is a multipart/related."""


def _leaves(root: EmailMessage, *, bounded: bool = False) -> Iterator[_Leaf]:
    """The leaves of `root`, depth first and in order, without recursion: an attached message is
    one leaf. With `bounded`, `invalid_argument` past `MAX_IMPORT_PARTS` or `MAX_IMPORT_DEPTH`."""
    stack: list[tuple[EmailMessage, EmailMessage | None, int]] = [(root, None, 0)]
    seen = 0
    while stack:
        part, parent, depth = stack.pop()
        seen += 1
        if bounded and (seen > MAX_IMPORT_PARTS or depth > MAX_IMPORT_DEPTH):
            raise MailError("invalid_argument", "That message has too many parts to import.")
        if _is_multipart(part):
            stack.extend((child, part, depth + 1) for child in reversed(_children(part)))
            continue
        related = parent is not None and parent.get_content_subtype() == "related"
        yield _Leaf(part, parent, related)


def _shown(part: EmailMessage) -> bool:
    """A part a reader shows as a body: no attachment disposition."""
    return part.get_content_disposition() != "attachment"


def _is_body(part: EmailMessage, kind: str) -> bool:
    """The body's text or HTML, as `structure.analyse_structure` finds it: shown, unnamed."""
    return part.get_content_type() == kind and _shown(part) and part.get_filename() is None


def _content_id(part: EmailMessage) -> str | None:
    value = part.get("Content-ID")
    if value is None:
        return None
    cid = str(value).strip().removeprefix("<").removesuffix(">").strip()
    return cid or None


def _decoded(part: EmailMessage) -> bytes:
    payload = part.get_payload(decode=True)
    return payload if isinstance(payload, bytes) else b""


def _carried_size(part: EmailMessage) -> int:
    """A leaf's decoded size; an attached message's (whose payload is a message) as it is sent."""
    payload = part.get_payload(decode=True)
    return len(payload) if isinstance(payload, bytes) else len(part.as_bytes())


def _text_of(part: EmailMessage) -> str:
    return decode_bytes(_decoded(part), part.get_content_charset())


def _parsed(source: bytes) -> EmailMessage:
    return BytesParser(policy=policy.default).parsebytes(source)


def _mailboxes(root: EmailMessage, field: str) -> tuple[Mailbox, ...]:
    found: list[Mailbox] = []
    for header in root.get_all(field) or ():
        for address in getattr(header, "addresses", ()):
            if address.addr_spec:
                found.append(Mailbox(address.addr_spec, address.display_name or None))
    return tuple(found)


def _set_html(part: EmailMessage, html: str) -> None:
    """Replaces a part's HTML, keeping the headers that say where it goes."""
    kept = [(name, part[name]) for name in ("Content-ID", "Content-Disposition") if name in part]
    part.set_content(html, subtype="html", charset="utf-8", cte="quoted-printable")
    del part["MIME-Version"]
    for name, value in kept:
        part[name] = value


def _text_part(text: str) -> EmailMessage:
    part = EmailMessage(policy=_POLICY)
    part.set_content(text, charset="utf-8", cte="quoted-printable")
    del part["MIME-Version"]
    return part


def _alternative(*parts: EmailMessage) -> EmailMessage:
    container = EmailMessage(policy=_POLICY)
    container.make_alternative()
    for part in parts:
        container.attach(part)
    return container


def _parent_of(root: EmailMessage, child: EmailMessage) -> EmailMessage | None:
    for parent, node in _containers(root):
        if node is child:
            return parent
    return None


def _containers(root: EmailMessage) -> Iterator[tuple[EmailMessage | None, EmailMessage]]:
    """Every node of `root` with its parent, multiparts included."""
    stack: list[tuple[EmailMessage | None, EmailMessage]] = [(None, root)]
    while stack:
        parent, node = stack.pop()
        yield parent, node
        if _is_multipart(node):
            stack.extend((node, child) for child in _children(node))


def _with_text(root: EmailMessage, html: EmailMessage, text: str) -> EmailMessage:
    """`root` with a text part as the first alternative to `html`: in its multipart/alternative,
    else in a new one around it, or around the multipart/related it is the root of."""
    parent = _parent_of(root, html)
    node = html
    while parent is not None and parent.get_content_subtype() == "related":
        node, parent = parent, _parent_of(root, parent)
    if parent is not None and parent.get_content_subtype() == "alternative":
        _children(parent).insert(0, _text_part(text))
        return root
    wrapped = _alternative(_text_part(text), node)
    if parent is None:
        return wrapped
    siblings = _children(parent)
    siblings[next(i for i, sibling in enumerate(siblings) if sibling is node)] = wrapped
    return root


def _sanitised(html: str) -> tuple[str, dict[str, int]]:
    """`html` as sent, its `cid:` images kept whether or not a part answers them, and what was
    removed from it by kind."""
    referred = html_images(html).content_ids
    cleaned = outgoing_html(
        well_formed(html), content_ids=referred, data_images=False, signature_mark=False
    )
    return cleaned.html, unsafe_parts(html)


def _check_text(text: str) -> None:
    if len(text.encode("utf-8", "surrogatepass")) > MAX_HTML_BYTES:
        raise MailError(
            "invalid_argument", f"A message's text is at most {format_size(MAX_HTML_BYTES)}."
        )


def _built(root: EmailMessage, cap: int, *, from_headers: bool = False) -> Built:
    """`root` sanitised and with a text part, its headers but the MIME ones gone; with
    `from_headers`, its recipients and subject as its own headers gave them."""
    leaves = list(_leaves(root, bounded=True))
    addressed = (
        {field: _mailboxes(root, field) for field in ("To", "Cc", "Bcc")} if from_headers else {}
    )
    subject = str(root.get("Subject") or "") if from_headers else ""
    changes: dict[str, int] = {}
    body_html: str | None = None
    html_part: EmailMessage | None = None
    has_text = False
    files = 0
    carried = 0
    for leaf in leaves:
        part = leaf.part
        if part.get_content_type() == "text/html" and _shown(part):
            content = _text_of(part)
            check_fields(html=content)
            cleaned, removed = _sanitised(content)
            for removal, count in removed.items():
                changes[removal] = changes.get(removal, 0) + count
            _set_html(part, _DOCUMENT.format(cleaned))
            if html_part is None and part.get_filename() is None:
                body_html, html_part = cleaned, part
            continue
        if _is_body(part, "text/plain") and not has_text:
            has_text = True
            _check_text(_text_of(part))
            continue
        carried += _carried_size(part)
        if not (leaf.related and _content_id(part) is not None and _shown(part)):
            files += 1
    if carried > cap:
        raise attachments_too_large(cap)
    for name in {name.lower() for name in root} - _KEPT_ROOT_HEADERS:
        del root[name]
    if html_part is not None and body_html is not None and not has_text:
        text = html_to_text(body_html, False, max_chars=2 * MAX_HTML_BYTES).text
        root = _with_text(root, html_part, text)
        changes[_TEXT_CHANGE] = 1
    del root["MIME-Version"]
    root["MIME-Version"] = "1.0"
    return Built(
        body=crlf_lines(root.as_bytes(policy=_POLICY)),
        to=addressed.get("To", ()),
        cc=addressed.get("Cc", ()),
        bcc=addressed.get("Bcc", ()),
        subject=clean_header(well_formed(subject), MAX_SUBJECT_CHARS),
        changes=tuple(sorted(changes.items())),
        attachments=files,
    )


def import_eml(source: bytes, cap: int) -> Built:
    """An .eml as `Built`: `invalid_argument` for one that is no message, or past
    `MAX_IMPORT_PARTS`, its HTML or text past `MAX_HTML_BYTES`; `too_large` when its other parts
    pass `cap` bytes together."""
    try:
        root = _parsed(source)
        if not root.keys():
            raise MailError("invalid_argument", _UNREADABLE)
        return _built(root, cap, from_headers=True)
    except _IMPORT_ERRORS:
        raise MailError("invalid_argument", _UNREADABLE) from None


def _referred_file(src: str, names: Mapping[str, OutgoingAttachment]) -> OutgoingAttachment | None:
    """The file a relative image source names by its last path segment, if any."""
    try:
        parts = urlsplit(src.strip())
    except ValueError:
        return None
    if parts.scheme or parts.netloc:
        return None
    return names.get(unquote(parts.path).rpartition("/")[2])


def _new_content_id(file: OutgoingAttachment) -> str:
    return (
        file.filename
        if _CONTENT_ID.fullmatch(file.filename)
        else f"{uuid.uuid4().hex}@mailroom.invalid"
    )


def import_html(html: str, files: Sequence[OutgoingAttachment], cap: int) -> Built:
    """An HTML file and its files as `Built`. A file with a `content_id` is an inline part; so is
    one its images name by a relative path (`images/logo.png` names `logo.png`) or `cid:` and
    its file name, and those paths become `cid:` references. The other files are attachments.
    The caps are `import_eml`'s; a host has checked the count and size of `files`."""
    check_fields(html=html)
    inline: dict[str, OutgoingAttachment] = {}
    assigned: dict[int, str] = {}
    names = {file.filename: file for file in files if file.content_id is None and file.filename}
    sources: dict[str, str] = {}
    for src in image_sources(html):
        stripped = src.strip()
        by_cid = stripped[:4].lower() == "cid:"
        named = unquote(stripped[4:]).strip("<>") if by_cid else None
        file = names.get(named) if named is not None else _referred_file(src, names)
        if file is None:
            continue
        cid = assigned.get(id(file)) or _new_content_id(file)
        assigned[id(file)] = cid
        inline[cid] = file
        if cid != named:
            sources[src] = f"cid:{cid}"
    for file in files:
        if file.content_id is not None:
            cid = file.content_id.strip().removeprefix("<").removesuffix(">")
            if not _CONTENT_ID.fullmatch(cid):
                raise MailError("invalid_argument", _BAD_CONTENT_ID)
            inline[cid] = file
    try:
        return _built(_assembled(html, sources, inline, files), cap)
    except _IMPORT_ERRORS:
        raise MailError("invalid_argument", _UNREADABLE) from None


def _assembled(
    html: str,
    sources: Mapping[str, str],
    inline: Mapping[str, OutgoingAttachment],
    files: Sequence[OutgoingAttachment],
) -> EmailMessage:
    """The message `import_html` builds: the HTML, its inline parts by Content-ID and the other
    files as attachments."""
    root = EmailMessage(policy=_POLICY)
    root.set_content(
        with_image_sources(html, sources) if sources else html,
        subtype="html",
        charset="utf-8",
        cte="quoted-printable",
    )
    if inline:
        root.make_related()
        root.set_param("type", "text/html")
    for cid, file in inline.items():
        main, sub = _part_type(file.content_type)
        root.add_related(
            file.content,
            maintype=main,
            subtype=sub,
            cid=f"<{cid}>",
            disposition="inline",
            filename=_filename(file.filename) or None,
        )
    shown = {id(file) for file in inline.values()}
    for file in files:
        if id(file) in shown:
            continue
        main, sub = _part_type(file.content_type)
        name = _filename(file.filename) or "attachment"
        root.add_attachment(
            file.content, maintype=main, subtype=sub, filename=name, params={"name": name}
        )
    return root


def _content_findings(root: EmailMessage, changes: Mapping[str, int]) -> list[Finding]:
    """`check_message`'s findings of what `root`'s HTML shows and its parts carry."""
    found: list[Finding] = []
    leaves = list(_leaves(root))
    html_part = next((leaf.part for leaf in leaves if _is_body(leaf.part, "text/html")), None)
    has_text = any(_is_body(leaf.part, "text/plain") for leaf in leaves)
    cids = {cid for leaf in leaves if (cid := _content_id(leaf.part)) is not None}
    inline = {
        cid: leaf.part.get_filename() or cid
        for leaf in leaves
        if leaf.related
        and _shown(leaf.part)
        and not _is_body(leaf.part, "text/html")
        and not _is_body(leaf.part, "text/plain")
        and (cid := _content_id(leaf.part)) is not None
    }
    if html_part is None:
        return found
    html = _text_of(html_part)
    images = html_images(html)
    if images.without_alt:
        text = f"{_n(images.without_alt, 'image has', 'images have')} no alt text."
        found.append(Finding("image_without_alt", text, images.without_alt))
    missing = sorted(images.content_ids - cids)
    if missing:
        text = f"{_n(len(missing), 'cid: reference names', 'cid: references name')} no part."
        found.append(Finding("cid_without_part", text, len(missing), _items(missing)))
    unused = sorted(name for cid, name in inline.items() if cid not in images.content_ids)
    if unused:
        text = f"{_n(len(unused), 'inline part is', 'inline parts are')} never shown."
        found.append(Finding("part_not_referenced", text, len(unused), _items(unused)))
    size = len(html.encode("utf-8", "surrogatepass"))
    if size > GMAIL_CLIP_BYTES:
        text = f"The HTML is {format_size(size)}: Gmail clips HTML past 102 KB."
        found.append(Finding("html_clipped", text))
    if images.remote:
        text = (
            f"{_n(images.remote, 'image loads', 'images load')} from a server, "
            "which many mail clients block."
        )
        found.append(Finding("remote_images", text, images.remote))
    if not has_text and not changes.get(_TEXT_CHANGE):
        found.append(Finding("no_text_part", "It has no plain-text part."))
    return found


def check_message(
    raw: bytes, cap: int, changes: Mapping[str, int] | None = None
) -> tuple[Finding, ...]:
    """The checks of a message before it is sent, from its source: what its import changed
    (`changes`, `draft_designed`'s), images without `alt`, `cid:` references no part answers and
    inline parts nothing refers to, HTML Gmail clips, remote images, no text part, and the whole
    message past `cap`, the attachment cap. Pure: reads nothing but `raw`."""
    found = _removal_findings(changes or {})
    with suppress(*_IMPORT_ERRORS):
        found.extend(_content_findings(_parsed(raw), changes or {}))
    if len(raw) > cap:
        text = (
            f"The whole message is {format_size(len(raw))}, over the {format_size(cap)} "
            "attachment limit: a receiving server may refuse it."
        )
        found.append(Finding("too_large", text))
    return tuple(found)


def import_draft(
    connections: MailConnections,
    account: AccountConfig,
    message: MessageInput,
    built: Built,
    *,
    cap: int,
    note: Note,
    compose_id: str,
    replaces: MessageRef | None = None,
) -> ImportedDraft:
    """Saves `built` in Drafts as it is, under `message`'s From, recipients and subject, as a new
    draft or the next version of `replaces`, and checks what was stored (`check_message`). The
    rest is `save_draft`'s: a host has checked the fields, the identity and the refs."""
    check_sendable(account)
    _check_compose_id(compose_id)
    if replaces is not None:
        if replaces.account != account.id:
            raise MailError("invalid_argument", "That draft belongs to another account.")
        check_removable(connections, replaces)
    draft = replace(
        message,
        text="",
        html=None,
        attachments=(),
        answers=None,
        forwards=None,
        draft=True,
        designed=built.changes,
    )
    composed = compose(draft, new_message_id(message.sender.address, compose_id), body=built.body)
    saved = store_draft(
        connections,
        account.id,
        draft,
        composed,
        note=note,
        replaces=replaces,
        attachments=built.attachments,
    )
    return ImportedDraft(saved, check_message(composed.stored, cap, dict(built.changes)))


def draft_findings(
    connections: MailConnections, ref: MessageRef, limits: Limits
) -> tuple[Finding, ...]:
    """The checks of a draft in Drafts as it is stored (`check_message`), with what its import
    changed when it was imported as built."""
    source = draft_source(connections, ref, draft_source_cap(limits))
    header_end = source.find(b"\r\n\r\n")
    head = source if header_end < 0 else source[: header_end + 2]
    return check_message(source, attachment_cap(limits), draft_designed(head))
