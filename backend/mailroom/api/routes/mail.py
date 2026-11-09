"""The mail client (``docs/requirements/mail-client.md``, *REST contract*): folders, message
pages, search, one message, its HTML as a standalone document and part downloads, which change
nothing; the organize routes (move, archive, trash, flag), which change folders and flags; and
drafts and sends. Only the signed-in user's accounts."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from typing import TYPE_CHECKING, Annotated, Final, Literal
from urllib.parse import quote

from fastapi import APIRouter, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders, UploadFile
from starlette.exceptions import HTTPException

from mailroom.api.deps import Context, CurrentUser
from mailroom.errors import mail_refusal
from mailroom.mail import service
from mailroom_core.mail import (
    DISPLAYED_IMAGES,
    MAX_UPLOADS,
    MESSAGES,
    Mailbox,
    OutgoingAttachment,
    SearchQuery,
    format_ref,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from mailroom_core.mail import (
        Draft,
        FlagState,
        Folder,
        GroupResult,
        MessageSummary,
        MessageView,
        OrganizeResult,
        Page,
        SavedDraft,
    )
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
_MAX_DRAFT_FIELD: Final = 1024 * 1024
"""The ``draft`` part of a save: more than its capped text and recipients can take."""
_MAX_KEPT: Final = 50
_MAX_ADDRESS: Final = 1024
_MAX_NAME: Final = 1024
_PART: Final = r"^\d+(\.\d+)*$"
_COMPOSE_ID: Final = r"^[A-Za-z0-9_-]{1,64}$"
_UPLOAD_TYPE: Final = "application/octet-stream"

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


class NotStored:
    """What answers under ``prefix`` is the user's mail: no cache keeps it, whatever the status. As
    middleware outside the cross-site check, so that a refusal, the 401 of a missing session, a
    422 and that check's 403 carry ``Cache-Control: private, no-store`` too."""

    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return

        async def not_stored(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = _NO_STORE
            await send(message)

        await self.app(scope, receive, not_stored)


mail_router = APIRouter(prefix="/mail")

AccountId = Annotated[str, Path(min_length=1, max_length=64)]
Ref = Annotated[str, Path(min_length=1, max_length=service.MAX_REF_LENGTH)]
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


def _mailboxes(mailboxes: Iterable[Mailbox]) -> list[MailAddress]:
    return [MailAddress(name=mailbox.name or "", address=mailbox.address) for mailbox in mailboxes]


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
    part: Annotated[str, Path(pattern=_PART, max_length=64)],
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


BatchRef = Annotated[str, StringConstraints(min_length=1, max_length=service.MAX_REF_LENGTH)]


class RefsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refs: Annotated[list[BatchRef], Field(min_length=1, max_length=service.MAX_REFS)]


class MoveRequest(RefsRequest):
    folder: Annotated[str, Field(min_length=1, max_length=service.MAX_FOLDER)]


class FlagRequest(RefsRequest):
    seen: bool | None = None
    flagged: bool | None = None


class MessageFlags(BaseModel):
    ref: str
    seen: bool | None
    flagged: bool | None

    @classmethod
    def of(cls, state: FlagState) -> MessageFlags:
        return cls(ref=state.ref, seen=state.seen, flagged=state.flagged)


class GroupError(BaseModel):
    code: str
    message: str


class GroupOutcome(BaseModel):
    account: str
    folder: str
    ok: bool
    count: int
    new_refs: list[str] | None
    junk: Literal["spam", "ham"] | None
    flags: list[MessageFlags]
    error: GroupError | None

    @classmethod
    def of(cls, group: GroupResult) -> GroupOutcome:
        error = group.error
        return cls(
            account=group.account,
            folder=group.folder,
            ok=group.ok,
            count=group.count,
            new_refs=None if group.new_refs is None else list(group.new_refs),
            junk=group.junk,
            flags=[MessageFlags.of(state) for state in group.flags],
            error=None if error is None else GroupError(code=error.code, message=error.message),
        )


class OrganizeAnswer(BaseModel):
    results: list[GroupOutcome]


def organize_answer(result: OrganizeResult) -> OrganizeAnswer:
    """The groups' results, when one succeeded at least; else the first failure's refusal."""
    if result.succeeded == 0:
        error = next(group.error for group in result.groups if group.error is not None)
        raise mail_refusal(error.code, error.message)
    return OrganizeAnswer(results=[GroupOutcome.of(group) for group in result.groups])


@mail_router.post("/messages/move")
def move(request: MoveRequest, ctx: Context, user: CurrentUser) -> OrganizeAnswer:
    return organize_answer(service.move(ctx, user.id, request.refs, request.folder))


@mail_router.post("/messages/archive")
def archive(request: RefsRequest, ctx: Context, user: CurrentUser) -> OrganizeAnswer:
    return organize_answer(service.archive(ctx, user.id, request.refs))


@mail_router.post("/messages/trash")
def trash(request: RefsRequest, ctx: Context, user: CurrentUser) -> OrganizeAnswer:
    return organize_answer(service.trash(ctx, user.id, request.refs))


@mail_router.post("/messages/flag")
def flag(request: FlagRequest, ctx: Context, user: CurrentUser) -> OrganizeAnswer:
    return organize_answer(
        service.flag(ctx, user.id, request.refs, seen=request.seen, flagged=request.flagged)
    )


class PrefillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: BatchRef
    mode: Literal["reply", "reply_all", "forward"]


class KeptPart(MessagePart):
    ref: str


class PrefillAnswer(BaseModel):
    account: str
    from_: str = Field(serialization_alias="from")
    to: list[MailAddress]
    cc: list[MailAddress]
    bcc: list[MailAddress]
    subject: str
    text: str
    answers: str | None
    attachments: list[KeptPart]

    @classmethod
    def of(cls, started: service.Prefill) -> PrefillAnswer:
        return cls(
            account=started.account,
            from_=started.sender,
            to=_mailboxes(started.to),
            cc=_mailboxes(started.cc),
            bcc=[],
            subject=started.subject,
            text=started.text,
            answers=started.answers,
            attachments=[
                KeptPart(ref=started.source, **MessagePart.of(info).model_dump())
                for info in started.attachments
            ],
        )


class DraftAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=_MAX_NAME)] = ""
    address: Annotated[str, Field(max_length=_MAX_ADDRESS)]

    def mailbox(self) -> Mailbox:
        return Mailbox(self.address, self.name or None)


class KeepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: BatchRef
    part: Annotated[str, StringConstraints(pattern=_PART, max_length=64)]


class DraftRequest(BaseModel):
    """The ``draft`` part of a save. Addresses, subject and text are held to their caps by the
    service, which answers ``invalid_argument`` for them, not here."""

    model_config = ConfigDict(extra="forbid")

    account: Annotated[str, Field(min_length=1, max_length=64)]
    compose_id: Annotated[str, Field(pattern=_COMPOSE_ID)]
    ref: BatchRef | None = None
    answers: BatchRef | None = None
    sender: Annotated[str | None, Field(alias="from", max_length=_MAX_ADDRESS)] = None
    to: list[DraftAddress] = []
    cc: list[DraftAddress] = []
    bcc: list[DraftAddress] = []
    subject: str = ""
    text: str = ""
    keep: Annotated[list[KeepRequest], Field(max_length=_MAX_KEPT)] = []

    def fields(self) -> service.DraftFields:
        return service.DraftFields(
            account=self.account,
            compose_id=self.compose_id,
            ref=self.ref,
            answers=self.answers,
            sender=self.sender,
            to=[address.mailbox() for address in self.to],
            cc=[address.mailbox() for address in self.cc],
            bcc=[address.mailbox() for address in self.bcc],
            subject=self.subject,
            text=self.text,
            keep=[(kept.ref, kept.part) for kept in self.keep],
        )


class SavedDraftAnswer(BaseModel):
    ref: str | None
    parts: list[MessagePart]
    replaced: bool
    autosave: bool

    @classmethod
    def of(cls, saved: SavedDraft) -> SavedDraftAnswer:
        return cls(
            ref=None if saved.ref is None else format_ref(saved.ref),
            parts=[MessagePart.of(info) for info in saved.parts],
            replaced=saved.replaced in ("expunged", "trashed"),
            autosave=saved.autosave,
        )


class FoundDraftAnswer(BaseModel):
    ref: str
    parts: list[MessagePart]

    @classmethod
    def of(cls, draft: Draft) -> FoundDraftAnswer:
        return cls(
            ref=format_ref(draft.ref),
            parts=[MessagePart.of(info) for info in draft.attachments],
        )


class DraftSend(BaseModel):
    send_id: str
    state: str
    code: str | None


class DraftAnswer(BaseModel):
    ref: str
    account: str
    compose_id: str | None
    from_: str | None = Field(serialization_alias="from")
    to: list[MailAddress]
    cc: list[MailAddress]
    bcc: list[MailAddress]
    subject: str
    text: str
    answers: str | None
    lossy: bool
    parts: list[MessagePart]
    send: DraftSend | None

    @classmethod
    def of(cls, view: service.DraftView) -> DraftAnswer:
        draft = view.draft
        send = view.send
        return cls(
            ref=format_ref(draft.ref),
            account=draft.ref.account,
            compose_id=draft.compose_id,
            from_=draft.sender[0].address if draft.sender else None,
            to=_mailboxes(draft.to),
            cc=_mailboxes(draft.cc),
            bcc=_mailboxes(draft.bcc),
            subject=draft.subject,
            text=draft.text,
            answers=None if draft.answers is None else format_ref(draft.answers),
            lossy=draft.lossy,
            parts=[MessagePart.of(info) for info in draft.attachments],
            send=None
            if send is None
            else DraftSend(send_id=send.send_id, state=send.state, code=send.code),
        )


class DraftRemoved(BaseModel):
    removed: str


class SendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_resend: bool = False


class SendAccepted(BaseModel):
    send_id: str


class SendAnswer(BaseModel):
    send_id: str
    state: str
    code: str | None
    filed: bool | None
    rejected: list[str]
    notes: list[str]
    note_codes: list[str]
    settled: bool

    @classmethod
    def of(cls, send: service.SendView) -> SendAnswer:
        return cls(
            send_id=send.send_id,
            state=send.state,
            code=send.code,
            filed=send.filed,
            rejected=list(send.rejected),
            notes=list(send.notes),
            note_codes=list(send.note_codes),
            settled=send.settled,
        )


def _too_large() -> Exception:
    return mail_refusal("too_large", MESSAGES["too_large"])


def _declared_over(content_length: str, limit: int) -> bool:
    declared = content_length.strip()
    if not (declared.isascii() and declared.isdecimal()):
        return False
    return len(declared) > len(str(limit)) or int(declared) > limit


def _at_most(receive: Receive, limit: int) -> Receive:
    """``receive`` refusing a body past ``limit``, whatever its ``Content-Length`` said, if it
    said anything."""
    received = 0

    async def bounded() -> Message:
        nonlocal received
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > limit:
                raise _too_large()
        return message

    return bounded


def _draft_request(value: object) -> DraftRequest:
    if not isinstance(value, str):
        raise RequestValidationError(
            [{"type": "missing", "loc": ("body", "draft"), "msg": "Field required"}]
        )
    try:
        return DraftRequest.model_validate_json(value)
    except ValidationError as error:
        raise RequestValidationError(
            error.errors(include_url=False, include_context=False, include_input=False)
        ) from None


async def _attachment(upload: UploadFile) -> OutgoingAttachment:
    return OutgoingAttachment(
        upload.filename or "", upload.content_type or _UPLOAD_TYPE, await upload.read()
    )


@mail_router.post("/drafts/prefill")
def prefill_draft(request: PrefillRequest, ctx: Context, user: CurrentUser) -> PrefillAnswer:
    return PrefillAnswer.of(service.prefill(ctx, user.id, request.ref, request.mode))


@mail_router.post("/drafts", status_code=201)
async def save_draft(request: Request, ctx: Context, user: CurrentUser) -> SavedDraftAnswer:
    """Multipart, parsed here and not by a signature: FastAPI would read the whole body before
    the signed-in user is known, and a declared size is refused before any of it is read. So is
    a save past the user's few at a time, which the mail gates could only refuse once it was
    read. Files past the memory threshold spool to disk until the form closes, before the save,
    so that a file is not held twice while it runs."""
    limit = await run_in_threadpool(service.upload_limit, ctx, user.id)
    if _declared_over(request.headers.get("content-length", ""), limit):
        raise _too_large()
    with service.upload_place(ctx, user.id):
        body = Request(request.scope, _at_most(request.receive, limit))
        try:
            async with body.form(
                max_files=MAX_UPLOADS, max_fields=1, max_part_size=_MAX_DRAFT_FIELD
            ) as form:
                draft = _draft_request(form.get("draft"))
                uploads = [
                    await _attachment(item)
                    for item in form.getlist("files[]")
                    if isinstance(item, UploadFile)
                ]
        except HTTPException:  # how Starlette words a malformed body or too many parts
            raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"]) from None
        saved = await run_in_threadpool(service.save_draft, ctx, user.id, draft.fields(), uploads)
    return SavedDraftAnswer.of(saved)


@mail_router.get("/drafts/find")
def find_draft(
    account: Annotated[str, Query(min_length=1, max_length=64)],
    compose_id: Annotated[str, Query(pattern=_COMPOSE_ID)],
    ctx: Context,
    user: CurrentUser,
) -> FoundDraftAnswer:
    return FoundDraftAnswer.of(service.find_draft(ctx, user.id, account, compose_id))


@mail_router.get("/drafts/{ref}")
def read_draft(ref: Ref, ctx: Context, user: CurrentUser) -> DraftAnswer:
    return DraftAnswer.of(service.read_draft(ctx, user.id, ref))


@mail_router.delete("/drafts/{ref}")
def delete_draft(ref: Ref, ctx: Context, user: CurrentUser) -> DraftRemoved:
    return DraftRemoved(removed=service.delete_draft(ctx, user.id, ref))


@mail_router.post("/drafts/{ref}/send", status_code=202)
def send_draft(
    ref: Ref,
    ctx: Context,
    user: CurrentUser,
    response: Response,
    request: SendRequest | None = None,
) -> SendAccepted | SendAnswer:
    """202 with the send to follow, unless this draft's key has been delivered already: then 200
    with what came of it, and nothing goes out."""
    confirm_resend = request is not None and request.confirm_resend
    send = service.send_draft(ctx, user.id, ref, by="user", confirm_resend=confirm_resend)
    if send.state == "sending":
        return SendAccepted(send_id=send.send_id)
    response.status_code = 200
    return SendAnswer.of(send)


@mail_router.get("/sends/{send_id}")
def read_send(
    send_id: Annotated[str, Path(max_length=64)], ctx: Context, user: CurrentUser
) -> SendAnswer:
    return SendAnswer.of(service.read_send(ctx, user.id, send_id))
