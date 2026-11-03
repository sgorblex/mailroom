"""The mail client's reads (``docs/requirements/mail-client.md``, *REST contract*): folders,
message pages, search, one message, its HTML as a standalone document, and part downloads. Only
the signed-in user's accounts; nothing here changes a mailbox."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from typing import TYPE_CHECKING, Annotated, Final
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response
from mailroom_core.mail import DISPLAYED_IMAGES, SearchQuery
from pydantic import BaseModel, ConfigDict, Field

from mailroom.api.deps import Context, CurrentUser
from mailroom.api.refusals import http_refusal
from mailroom.errors import Refusal
from mailroom.mail import service

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from mailroom_core.mail import Folder, MessageSummary, MessageView, Page
    from mailroom_core.render.message import Address, PartInfo

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
_NO_STORE: Final = "private, no-store"
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
    "<style>:root{{color-scheme:light}}"
    "body{{margin:0;padding:12px;background:#fff;color:#000;overflow-wrap:anywhere}}"
    "img{{max-width:100%;height:auto}}</style>"
    "</head><body>{body}</body></html>\n"
)


def _not_stored(response: Response) -> Iterator[None]:
    """What these routes answer is the user's mail: no cache keeps it, a refusal included. (A
    route answering its own ``Response`` sets the header itself; FastAPI copies this one only
    onto the responses it builds.)"""
    response.headers["Cache-Control"] = _NO_STORE
    try:
        yield
    except Refusal as refusal:
        refused = http_refusal(refusal)
        refused.headers = {"Cache-Control": _NO_STORE}
        raise refused from None


mail_router = APIRouter(prefix="/mail", dependencies=[Depends(_not_stored)])

AccountId = Annotated[str, Path(min_length=1, max_length=64)]
Ref = Annotated[str, Path(min_length=1, max_length=4096)]
Cursor = Annotated[str | None, Query(max_length=service.MAX_CURSOR)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


class MailAddress(BaseModel):
    name: str
    address: str

    @classmethod
    def of(cls, address: Address) -> MailAddress:
        return cls(name=address.name or "", address=address.address or "")


def _addresses(addresses: Iterable[Address]) -> list[MailAddress]:
    return [MailAddress.of(address) for address in addresses]


class Flags(BaseModel):
    seen: bool
    flagged: bool
    answered: bool
    draft: bool

    @classmethod
    def of(cls, flags: Iterable[str]) -> Flags:
        lowered = {flag.lower() for flag in flags}
        return cls(
            seen="\\seen" in lowered,
            flagged="\\flagged" in lowered,
            answered="\\answered" in lowered,
            draft="\\draft" in lowered,
        )


class MailFolder(BaseModel):
    path: str
    role: str | None
    unread: int
    total: int

    @classmethod
    def of(cls, folder: Folder) -> MailFolder:
        return cls(
            path=folder.path,
            role=folder.role,
            unread=folder.unseen or 0,
            total=folder.messages or 0,
        )


class FolderList(BaseModel):
    account_id: str
    folders: list[MailFolder]


class MessageItem(BaseModel):
    ref: str
    account_id: str
    folder: str
    date: datetime | None
    from_: MailAddress = Field(serialization_alias="from")
    subject: str
    flags: Flags
    has_attachments: bool
    preview: str
    size: int

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
        )


class SourceFailure(BaseModel):
    account_id: str
    folder: str | None
    code: str


class MessagePage(BaseModel):
    messages: list[MessageItem]
    next_cursor: str | None
    failures: list[SourceFailure]

    @classmethod
    def of(cls, page: Page) -> MessagePage:
        return cls(
            messages=[MessageItem.of(summary) for summary in page.messages],
            next_cursor=page.next_cursor,
            failures=[
                SourceFailure(account_id=f.account, folder=f.folder, code=f.code)
                for f in page.failures
            ],
        )


class BodyText(BaseModel):
    content: str
    removed_chars: int
    incomplete: bool
    next_offset: int | None
    quoted_omitted: bool
    """Quoted history or a signature was left out: ``include_quoted=true`` shows it. Nothing in
    ``content`` marks the gap; the client offers to show it."""


class MessagePart(BaseModel):
    part: str
    name: str
    type: str
    size: int

    @classmethod
    def of(cls, info: PartInfo) -> MessagePart:
        return cls(part=info.part, name=info.name or "", type=info.type, size=info.size)


class MessageDetail(BaseModel):
    ref: str
    account_id: str
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
    has_remote_content: bool
    attachments: list[MessagePart]
    calendar: MessagePart | None

    @classmethod
    def of(cls, view: MessageView) -> MessageDetail:
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
            has_remote_content=bool(view.has_remote_content),
            attachments=[MessagePart.of(info) for info in view.attachments],
            calendar=None if view.calendar is None else MessagePart.of(view.calendar),
        )


def _term(value: str | None) -> str | None:
    """An empty search field is no criterion."""
    return value if value else None


@mail_router.get("/accounts/{account_id}/folders")
def folders(account_id: AccountId, ctx: Context, user: CurrentUser) -> FolderList:
    listed = service.folders(ctx, user.id, account_id)
    return FolderList(account_id=account_id, folders=[MailFolder.of(f) for f in listed])


@mail_router.get("/accounts/{account_id}/messages")
def messages(
    account_id: AccountId,
    ctx: Context,
    user: CurrentUser,
    folder: Annotated[str, Query(min_length=1, max_length=service.MAX_FOLDER)] = "INBOX",
    limit: Limit = PAGE_SIZE,
    cursor: Cursor = None,
) -> MessagePage:
    page = service.list_messages(ctx, user.id, account_id, folder, limit=limit, cursor=cursor)
    return MessagePage.of(page)


class SearchRequest(BaseModel):
    """A search's criteria, in a body rather than the query string so that they stay out of
    access logs."""

    model_config = ConfigDict(extra="forbid")

    account: Annotated[str, Field(min_length=1, max_length=64)] = service.ALL_ACCOUNTS
    folder: Annotated[str, Field(min_length=1, max_length=service.MAX_FOLDER)] = "INBOX"
    sender: Annotated[str | None, Field(alias="from", max_length=service.MAX_TERM)] = None
    to: Annotated[str | None, Field(max_length=service.MAX_TERM)] = None
    subject: Annotated[str | None, Field(max_length=service.MAX_TERM)] = None
    text: Annotated[str | None, Field(max_length=service.MAX_TERM)] = None
    since: date | None = None
    before: date | None = None
    unread: bool | None = None
    flagged: bool | None = None
    has_attachment: bool | None = None
    gmail_query: Annotated[str | None, Field(max_length=service.MAX_TERM)] = None
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)] = PAGE_SIZE
    cursor: Annotated[str | None, Field(max_length=service.MAX_CURSOR)] = None

    def query(self) -> SearchQuery:
        return SearchQuery(
            sender=_term(self.sender),
            to=_term(self.to),
            subject=_term(self.subject),
            text=_term(self.text),
            since=self.since,
            before=self.before,
            unread=self.unread,
            flagged=self.flagged,
            has_attachment=self.has_attachment,
            gmail_query=_term(self.gmail_query),
        )


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
    )
    return MessagePage.of(page)


@mail_router.get("/messages/{ref}")
def message(
    ref: Ref,
    ctx: Context,
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
    return MessageDetail.of(view)


def html_headers(*, remote: bool) -> dict[str, str]:
    """The HTML view's own headers; the nginx ``/api/`` location adds no CSP of its own."""
    images = "data: http: https:" if remote else "data:"
    return {
        "Content-Security-Policy": HTML_CSP.format(images=images),
        "Referrer-Policy": "no-referrer",
        **_PRIVATE,
    }


def html_document(fragment: str) -> str:
    """mailroom-core's sanitised fragment in a minimal document: UTF-8, links in a new
    top-level context (``safe_html`` also sets ``target`` and ``rel`` on each)."""
    return _DOCUMENT.format(body=fragment)


@mail_router.get("/messages/{ref}/html", response_class=Response)
def message_html(ref: Ref, ctx: Context, user: CurrentUser, remote: bool = False) -> Response:
    """A standalone document for the client's sandboxed iframe, not JSON. Served as a document
    (not ``srcdoc``) so that it carries its own CSP rather than the SPA's."""
    safe = service.message_html(ctx, user.id, ref, remote=remote)
    return Response(
        content=html_document(safe.html),
        media_type="text/html; charset=utf-8",
        headers=html_headers(remote=remote),
    )


def served_type(kind: str) -> str:
    """The type a download is served as: displayed images and plain-text kinds as they are
    (mailroom-core's ``content_type`` checked the images' bytes), anything else, HTML and SVG
    above all, as a plain file."""
    if kind in DISPLAYED_IMAGES or kind in _SERVED_TEXT:
        return kind
    return "application/octet-stream"


def download_name(name: str | None, part: str) -> str:
    """The sender's file name without control or invisible characters and path separators,
    bounded; ``attachment-<part>`` when nothing is left."""
    cleaned = "".join(
        "_" if ch in "/\\" else ch
        for ch in unicodedata.normalize("NFC", name or "")
        if not unicodedata.category(ch).startswith("C")
    )
    cleaned = cleaned.strip(" .")[:_MAX_FILENAME].strip()
    return cleaned or f"attachment-{part}"


@mail_router.get("/messages/{ref}/parts/{part}", response_class=Response)
def message_part(
    ref: Ref,
    part: Annotated[str, Path(pattern=r"^\d+(\.\d+)*$", max_length=64)],
    ctx: Context,
    user: CurrentUser,
) -> Response:
    """One attachment, inline part or the calendar part, transfer encoding decoded, as a
    download."""
    attachment = service.attachment(ctx, user.id, ref, part)
    filename = quote(download_name(attachment.name, part), safe="")
    return Response(
        content=attachment.data,
        media_type=served_type(attachment.type),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Content-Security-Policy": PART_CSP,
            **_PRIVATE,
        },
    )
