"""Reading mail, which changes nothing: message pages, search, one message, its HTML as a
standalone document, part downloads, its source as a download, its printable page and a filter
drafted from it."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Final, Literal
from urllib.parse import quote

from fastapi import Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from mailroom.api.deps import AccountId, Context, CurrentUser, DbSession
from mailroom.api.routes.mail._shared import (
    _NO_STORE,
    _PART,
    MailAddress,
    MatchRequest,
    MessagePart,
    Ref,
    mail_router,
)
from mailroom.filters.drafts import MessageDraft, message_draft
from mailroom.mail import remote_senders, sender_views, service, unsubscribe
from mailroom.mail.print_page import printable_page
from mailroom_core.mail import DISPLAYED_IMAGES, FORWARDED, TO_SORT

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mailroom_core.mail import MessageSummary, MessageView, Page, PageFailure
    from mailroom_core.render import Address

PAGE_SIZE: Final = 25
MAX_PAGE_SIZE: Final = 50

HTML_CSP: Final = (
    "sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; "
    "style-src 'unsafe-inline'; img-src {images}; frame-ancestors 'self'; base-uri 'none'"
)
"""The HTML view's policy: the document is framed by the SPA only, runs nothing, and loads only
inlined images (``data:``), plus remote images (``http:`` and ``https:``, the ones
mailroom-core keeps) once the reader reveals remote content; never remote CSS or fonts."""
PART_CSP: Final = "default-src 'none'; sandbox"
PRINT_CSP: Final = (
    "sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; "
    "style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'none'"
)
"""The printable page's policy: a tab of its own, never framed, running nothing and loading
only its inlined images; its links open apart from it."""
_PRIVATE: Final = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": _NO_STORE,
}
_SERVED_TEXT: Final = frozenset(
    {"text/plain", "text/csv", "text/markdown", "text/calendar", "application/json"}
)
"""Downloads that keep their type beside the displayed images; any other is a plain file."""
_MAX_FILENAME: Final = 150

_DOCUMENT: Final = (
    "<!doctype html>\n"
    '<html><head><meta charset="utf-8">'
    '<meta name="referrer" content="no-referrer">'
    '<base target="_blank">'
    "<style>{style}"
    "img{{max-width:100%;height:auto}}</style>"
    "</head><body>{body}</body></html>\n"
)
_LIGHT: Final = (
    ":root{color-scheme:light}"
    "body{margin:0;padding:12px;background:#fff;color:#000;overflow-wrap:anywhere}"
)
ORIGINAL_TARGET: Final = "mailroom-original"
"""The id the dark document gives its fragment target: ``#mailroom-original`` shows the message's
own colours, any other fragment the dark ones, so that the client switches with the URL's fragment
alone, a same-document navigation with no request and no script. ``safe_html`` keeps no ``id`` or
``name``, so no element of the message can be the target."""
_UNLESS_ORIGINAL: Final = f":root:not(:has(#{ORIGINAL_TARGET}:target))"
_TARGET: Final = f'<span id="{ORIGINAL_TARGET}" hidden></span>'
_DARK: Final = (
    f"{_UNLESS_ORIGINAL}{{color-scheme:dark}}"
    f"{_UNLESS_ORIGINAL} body{{background:#121212;color:#e8e8e8}}"
    f"{_UNLESS_ORIGINAL} a{{color:#8ab4f8}}{_UNLESS_ORIGINAL} a:visited{{color:#c58af9}}"
)
"""Dark colours for HTML that sets none of its own, over ``_LIGHT``."""
_INVERTED: Final = (
    f"{_UNLESS_ORIGINAL}{{background:#121212}}"
    f"{_UNLESS_ORIGINAL} body{{box-sizing:border-box;min-height:100vh;background:#ededed;"
    "filter:invert(1) hue-rotate(180deg)}"
    f"{_UNLESS_ORIGINAL} img{{filter:invert(1) hue-rotate(180deg)}}"
)
"""HTML that sets its own colours, over ``_LIGHT``: inverted with its hues kept, and its images
inverted back: img alone, since safe_html keeps no picture or video and no CSS url(). The body is
inverted rather than the root, whose filter not every browser applies to the canvas: the root's
background is what the body's turns into."""

Cursor = Annotated[str | None, Query(max_length=service.MAX_CURSOR)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
Group = Annotated[
    Literal["thread"] | None,
    Query(description="thread: by conversation, where the page is read from the mail index."),
]
THREAD_SIZE: Final = 30


def _addresses(addresses: Iterable[Address]) -> list[MailAddress]:
    return [MailAddress.of(address) for address in addresses]


class Flags(BaseModel):
    seen: bool
    flagged: bool
    answered: bool
    forwarded: bool
    draft: bool
    to_sort: bool

    @classmethod
    def of(cls, flags: Iterable[str]) -> Flags:
        lowered = {flag.lower() for flag in flags}
        return cls(
            seen="\\seen" in lowered,
            flagged="\\flagged" in lowered,
            answered="\\answered" in lowered,
            forwarded=FORWARDED.lower() in lowered,
            draft="\\draft" in lowered,
            to_sort=TO_SORT.lower() in lowered,
        )


class MessageItem(BaseModel):
    ref: str
    account_id: int
    folder: str
    date: datetime | None
    from_: MailAddress = Field(serialization_alias="from")
    subject: str
    flags: Flags
    has_attachments: bool
    preview: str
    size: int
    listed_folder: str | None = None
    """The folder the row was listed under when not its own: on Gmail, a row read from the index
    names All Mail, listed under INBOX or a label's folder. Sent back as the ref's
    ``source_folders`` entry, an action acts there."""

    @classmethod
    def of(cls, summary: MessageSummary) -> MessageItem:
        sender = summary.sender[0] if summary.sender else None
        return cls(
            ref=summary.ref,
            account_id=summary.account,
            folder=summary.folder,
            date=summary.date,
            from_=MailAddress(name="", address="") if sender is None else MailAddress.of(sender),
            subject=summary.subject,
            flags=Flags.of(summary.flags),
            has_attachments=summary.has_attachments,
            preview=summary.preview or "",
            size=summary.size,
            listed_folder=summary.listed_folder
            if isinstance(summary, service.ListedSummary)
            else None,
        )


class SourceFailure(BaseModel):
    account_id: int
    folder: str | None
    code: str

    @classmethod
    def of(cls, failure: PageFailure) -> SourceFailure:
        return cls(account_id=failure.account, folder=failure.folder, code=failure.code)


class ConversationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    count: int
    unread: int
    participants: list[str]
    """The first senders (a display name, else the address), in the order they first wrote, at
    most four."""
    has_attachments: bool
    flagged: bool
    refs: list[str]
    """The messages an action on the conversation takes, newest first: every one in the source
    read but the user's own copies (Sent and Drafts; on Gmail labelled so), unless it holds
    nothing else."""
    listed_folders: dict[str, str]
    """The folder each of ``refs`` was listed under, where it is not its own (as a message's
    ``listed_folder``)."""
    latest_from_new_sender: bool
    """Its latest message's sender neither wrote nor was written to in any other of its
    messages; false for a conversation of one."""


class MessagePage(BaseModel):
    messages: list[MessageItem]
    next_cursor: str | None
    failures: list[SourceFailure]
    conversations: list[ConversationItem] | None = None
    """With ``group=thread``, each message's conversation (the message is its latest); null when
    the page is not grouped: not asked, or read live."""

    @classmethod
    def of(cls, page: Page) -> MessagePage:
        grouped = page.conversations if isinstance(page, service.ConversationPage) else None
        return cls(
            messages=[MessageItem.of(summary) for summary in page.messages],
            next_cursor=page.next_cursor,
            failures=[SourceFailure.of(failure) for failure in page.failures],
            conversations=None
            if grouped is None
            else [ConversationItem.model_validate(c) for c in grouped],
        )


class BodyText(BaseModel):
    content: str
    removed_chars: int
    incomplete: bool
    next_offset: int | None
    quoted_omitted: bool
    """Quoted history or a signature was left out: ``include_quoted=true`` shows it. Nothing in
    ``content`` marks the gap; the client offers to show it."""


class ListLinks(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str | None
    name: str | None
    archive: str | None
    post: str | None
    help: str | None
    unsubscribe_https: str | None
    unsubscribe_mailto: str | None
    one_click: bool
    unsubscribe_fields: int
    unsubscribe_post_fields: int


class AuthResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    method: str
    result: str
    header_d: str | None
    header_b: str | None
    header_from: str | None


class AuthResults(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    authserv_id: str | None
    results: list[AuthResult]


class DkimSignature(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    d: str
    b: str
    h: list[str]


class HeaderInfo(BaseModel):
    """What mailroom-core parsed from the list, authentication and delivery fields: the
    sender's or a relay's words, none of them verified yet."""

    model_config = ConfigDict(from_attributes=True)

    mailing_list: ListLinks | None
    authentication: list[AuthResults]
    dkim_signatures: list[DkimSignature]
    from_fields: int
    delivered_to: list[str]
    truncated: bool


class AuthenticationVerdict(BaseModel):
    """The account's own authentication server's verdict: all null unless ``verified``."""

    model_config = ConfigDict(from_attributes=True)

    verified: bool
    spf: str | None
    spf_domain: str | None
    """The domain SPF checked, as an ASCII host name."""
    dkim: str | None
    dkim_domain: str | None
    """The signing domain of the DKIM result, likewise."""
    dmarc: str | None
    dmarc_pass: bool
    from_domain: str | None


class MessageDetail(BaseModel):
    ref: str
    account_id: int
    folder: str
    from_: MailAddress | None = Field(serialization_alias="from")
    to: list[MailAddress]
    cc: list[MailAddress]
    reply_to: list[MailAddress]
    date: datetime | None
    subject: str
    message_id: str | None
    flags: Flags
    text: BodyText
    has_html: bool
    remote_images: int
    """Remote images the HTML asks for, blocked until the reader shows them; 0 without HTML."""
    loadable_remote_images: int
    """Of those, the ``<img>`` sources that ``remote=1`` shows; the others stay blocked."""
    attachments: list[MessagePart]
    calendar: MessagePart | None
    headers: HeaderInfo
    sender_mismatch: bool
    """The From display name names another address or domain than ``from.address``."""
    sent_to: str | None
    """The account's identity the message arrived at."""
    authentication: AuthenticationVerdict
    remote_trusted: bool
    """The user shows this sender's remote content without asking, and DMARC passed for the
    message's one From: the client asks for the HTML with ``remote=1``."""
    one_click_unsubscribe: bool
    """``POST /messages/{ref}/unsubscribe`` would post: the account's server vouches for it."""
    preferred_view: sender_views.View
    """The view the message opens in: ``html`` when it has HTML, unless the user reads its
    sender as text (``/sender-views``)."""

    @classmethod
    def of(
        cls,
        view: MessageView,
        *,
        remote_trusted: bool,
        one_click_unsubscribe: bool,
        preferred_view: sender_views.View,
    ) -> MessageDetail:
        body = view.body
        return cls(
            ref=view.ref,
            account_id=view.account,
            folder=view.folder,
            from_=MailAddress.of(view.sender[0]) if view.sender else None,
            to=_addresses(view.to),
            cc=_addresses(view.cc),
            reply_to=_addresses(view.reply_to),
            date=view.date,
            subject=view.subject,
            message_id=view.message_id,
            flags=Flags.of(view.flags),
            text=BodyText(
                content="",
                removed_chars=0,
                incomplete=False,
                next_offset=None,
                quoted_omitted=False,
            )
            if body is None
            else BodyText(
                content=body.text,
                removed_chars=body.removed_chars,
                incomplete=body.incomplete,
                next_offset=body.next_offset,
                quoted_omitted=body.quoted_lines + body.signature_lines > 0,
            ),
            has_html=view.has_html,
            remote_images=view.remote_images or 0,
            loadable_remote_images=view.loadable_remote_images or 0,
            attachments=[MessagePart.of(info) for info in view.attachments],
            calendar=None if view.calendar is None else MessagePart.of(view.calendar),
            headers=HeaderInfo.model_validate(view.headers),
            sender_mismatch=view.sender_mismatch,
            sent_to=view.sent_to,
            authentication=AuthenticationVerdict.model_validate(view.authentication),
            remote_trusted=remote_trusted,
            one_click_unsubscribe=one_click_unsubscribe,
            preferred_view=preferred_view,
        )


@mail_router.get("/accounts/{account_id}/messages")
def messages(
    account_id: AccountId,
    ctx: Context,
    user: CurrentUser,
    folder: Annotated[str, Query(min_length=1, max_length=service.MAX_FOLDER)] = "INBOX",
    limit: Limit = PAGE_SIZE,
    cursor: Cursor = None,
    subfolders: bool = False,
    group: Group = None,
) -> MessagePage:
    page = service.list_messages(
        ctx,
        user.id,
        account_id,
        folder,
        limit=limit,
        cursor=cursor,
        subfolders=subfolders,
        group=group == "thread",
    )
    return MessagePage.of(page)


class SearchRequest(MatchRequest):
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)] = PAGE_SIZE
    cursor: Annotated[str | None, Field(max_length=service.MAX_CURSOR)] = None
    group: Literal["thread"] | None = None


@mail_router.post("/search")
def search(request: SearchRequest, ctx: Context, user: CurrentUser) -> MessagePage:
    page = service.search(
        ctx,
        user.id,
        request.account,
        request.folder,
        request.query(),
        limit=request.limit,
        cursor=request.cursor,
        subfolders=request.subfolders,
        group=request.group == "thread",
    )
    return MessagePage.of(page)


@mail_router.get("/messages/{ref}/thread")
def thread(
    ref: Ref,
    ctx: Context,
    user: CurrentUser,
    limit: Limit = THREAD_SIZE,
) -> MessagePage:
    """The conversation of the message ``ref`` names, newest first, read live: its folder and
    Sent (on Gmail, its thread in All Mail)."""
    return MessagePage.of(service.thread(ctx, user.id, ref, limit=limit))


@mail_router.get("/messages/{ref}")
def message(
    ref: Ref,
    ctx: Context,
    db: DbSession,
    user: CurrentUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_quoted: bool = False,
) -> MessageDetail:
    view = service.read_message(
        ctx,
        user.id,
        ref,
        offset=offset,
        include_quoted=include_quoted,
        remote_check=True,
    )
    sender = remote_senders.verified_sender(view)
    trusted = sender is not None and remote_senders.remembered(db, user.id, sender)
    return MessageDetail.of(
        view,
        remote_trusted=trusted,
        one_click_unsubscribe=unsubscribe.offered(ctx, user.id, view),
        preferred_view=sender_views.preferred_view(db, user.id, view),
    )


def html_headers(*, remote: bool) -> dict[str, str]:
    """The HTML view's own headers; the nginx ``/api/`` location adds no CSP of its own."""
    images = "data: http: https:" if remote else "data:"
    return {
        "Content-Security-Policy": HTML_CSP.format(images=images),
        "Referrer-Policy": "no-referrer",
        **_PRIVATE,
    }


def html_document(fragment: str, *, dark: bool = False, sets_colours: bool = False) -> str:
    """mailroom-core's sanitised fragment in a minimal document: UTF-8, links in a new
    top-level context (``safe_html`` also sets ``target`` and ``rel`` on each). ``dark`` adapts
    it to a dark theme with CSS alone: dark colours when the fragment sets none of its own,
    else (``sets_colours``) the inverted page with its images inverted back; the same document
    shows the original colours when its URL's fragment is ``#mailroom-original``
    (``ORIGINAL_TARGET``)."""
    if not dark:
        return _DOCUMENT.format(style=_LIGHT, body=fragment)
    style = _LIGHT + (_INVERTED if sets_colours else _DARK)
    return _DOCUMENT.format(style=style, body=_TARGET + fragment)


@mail_router.get("/messages/{ref}/html", response_class=Response)
def message_html(
    ref: Ref, ctx: Context, user: CurrentUser, remote: bool = False, dark: bool = False
) -> Response:
    """A standalone document for the client's sandboxed iframe, not JSON. Served as a document
    (not ``srcdoc``) so that it carries its own CSP rather than the SPA's. ``dark``: the
    message adapted to a dark theme, its original colours one fragment away
    (``html_document``)."""
    safe = service.message_html(ctx, user.id, ref, remote=remote)
    return Response(
        content=html_document(safe.html, dark=dark, sets_colours=safe.sets_colours),
        media_type="text/html; charset=utf-8",
        headers=html_headers(remote=remote),
    )


@mail_router.get("/messages/{ref}/html/check", status_code=204, response_class=Response)
def message_html_check(ref: Ref, ctx: Context, user: CurrentUser, remote: bool = False) -> Response:
    """204 when ``/html`` would answer the document, else its refusal, as JSON: the client asks
    this before it frames the document, so that a refusal is its own words, never the frame's.
    The view is kept (``service.message_html``), so the document then costs no second read."""
    service.message_html(ctx, user.id, ref, remote=remote)
    return Response(status_code=204)


def served_type(kind: str) -> str:
    """The type a download is served as: displayed images and plain-text kinds as they are
    (mailroom-core's ``content_type`` checked the images' bytes), anything else, HTML and SVG
    above all, as a plain file."""
    if kind in DISPLAYED_IMAGES or kind in _SERVED_TEXT:
        return kind
    return "application/octet-stream"


def download_name(name: str | None, fallback: str) -> str:
    """The sender's file name (or a subject) without control or invisible characters and path
    separators, bounded; ``fallback`` when nothing is left."""
    cleaned = "".join(
        "_" if ch in "/\\" else ch
        for ch in unicodedata.normalize("NFC", name or "")
        if not unicodedata.category(ch).startswith("C")
    )
    cleaned = cleaned.strip(" .")[:_MAX_FILENAME].strip()
    return cleaned or fallback


@mail_router.get("/messages/{ref}/parts/{part}", response_class=Response)
def message_part(
    ref: Ref,
    part: Annotated[str, Path(pattern=_PART, max_length=64)],
    ctx: Context,
    user: CurrentUser,
) -> Response:
    """One attachment, inline part or the calendar part, transfer encoding decoded, as a
    download."""
    attachment = service.attachment(ctx, user.id, ref, part)
    filename = quote(download_name(attachment.name, f"attachment-{part}"), safe="")
    return Response(
        content=attachment.data,
        media_type=served_type(attachment.type),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Content-Security-Policy": PART_CSP,
            **_PRIVATE,
        },
    )


@mail_router.get("/messages/{ref}/source", response_class=Response)
def message_source(ref: Ref, ctx: Context, user: CurrentUser) -> Response:
    """The whole message as stored, as a ``.eml`` download named after its subject."""
    raw = service.message_source(ctx, user.id, ref)
    filename = quote(f"{download_name(raw.subject, 'message')}.eml", safe="")
    return Response(
        content=raw.source,
        media_type="message/rfc822",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Content-Security-Policy": PART_CSP,
            **_PRIVATE,
        },
    )


@mail_router.get("/messages/{ref}/print", response_class=Response)
def message_print(ref: Ref, ctx: Context, user: CurrentUser) -> Response:
    """A standalone page of the message for the browser's own print, opened in a tab of its
    own: it runs nothing and loads nothing remote."""
    page = printable_page(service.printable(ctx, user.id, ref))
    return Response(
        content=page,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": PRINT_CSP,
            "Referrer-Policy": "no-referrer",
            **_PRIVATE,
        },
    )


@mail_router.get("/messages/{ref}/filter-draft")
def filter_draft(ref: Ref, ctx: Context, user: CurrentUser) -> MessageDraft:
    """A filter the editor starts from, made from the message's List-Id and sender."""
    view, _ = service.read_headers(ctx, user.id, ref, event="mail_filter_draft_read")
    return message_draft(view)
