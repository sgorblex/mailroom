"""Drafts and sends: the prefill of a reply, a forward or an unsubscribe by mail, saving a draft
with its uploads (multipart, bounded before it is read), finding, reading and deleting one,
sending it and reading the send."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final, Literal

from fastapi import Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from mailroom.api.deps import Context, CurrentUser
from mailroom.api.routes.mail._shared import (
    _PART,
    BatchRef,
    MailAddress,
    MessagePart,
    Ref,
    mail_router,
)
from mailroom.errors import mail_refusal
from mailroom.mail import service
from mailroom_core.config import MAX_SAFE_INTEGER
from mailroom_core.mail import (
    MAX_HTML_BYTES,
    MAX_UPLOADS,
    MESSAGES,
    Mailbox,
    OutgoingAttachment,
    format_ref,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from starlette.types import Message, Receive

    from mailroom_core.mail import Draft, InlinePart, SavedDraft

_MAX_KEPT: Final = 50
_MAX_ADDRESS: Final = 1024
_MAX_NAME: Final = 1024
_COMPOSE_ID: Final = r"^[A-Za-z0-9_-]{1,64}$"
_UPLOAD_TYPE: Final = "application/octet-stream"
_MAX_CONTENT_ID: Final = 250


def _mailboxes(mailboxes: Iterable[Mailbox]) -> list[MailAddress]:
    return [MailAddress(name=mailbox.name or "", address=mailbox.address) for mailbox in mailboxes]


class PrefillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: BatchRef
    mode: Literal["reply", "reply_all", "forward", "unsubscribe"]


class KeptPart(MessagePart):
    ref: str


class ShownPart(MessagePart):
    """An inline part: what the HTML shows as ``cid:<content_id>``."""

    content_id: str

    @classmethod
    def of_inline(cls, inline: InlinePart) -> ShownPart:
        return cls(content_id=inline.content_id, **MessagePart.of(inline.info).model_dump())


class KeptShownPart(ShownPart):
    ref: str


def _shown(parts: Iterable[InlinePart]) -> list[ShownPart]:
    return [ShownPart.of_inline(part) for part in parts]


class PrefillAnswer(BaseModel):
    account: int
    from_: str = Field(serialization_alias="from")
    to: list[MailAddress]
    cc: list[MailAddress]
    bcc: list[MailAddress]
    subject: str
    text: str
    html: str | None
    answers: str | None
    forwards: str | None
    attachments: list[KeptPart]
    inline: list[KeptShownPart]

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
            html=started.html,
            answers=started.answers,
            forwards=started.forwards,
            attachments=[
                KeptPart(ref=started.source, **MessagePart.of(info).model_dump())
                for info in started.attachments
            ],
            inline=[
                KeptShownPart(ref=started.source, **shown.model_dump())
                for shown in _shown(started.inline)
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
    inline: bool = False
    """The part is one the HTML shows as ``cid:``: the previous version's, or a forward's."""


ContentId = Annotated[str, StringConstraints(min_length=1, max_length=_MAX_CONTENT_ID)]


class DraftRequest(BaseModel):
    """The ``draft`` part of a save; its HTML comes in a part of its own. Addresses, subject, text
    and HTML are held to their caps by the service, which answers ``invalid_argument`` for them,
    not here."""

    model_config = ConfigDict(extra="forbid")

    account: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
    compose_id: Annotated[str, Field(pattern=_COMPOSE_ID)]
    ref: BatchRef | None = None
    answers: BatchRef | None = None
    forwards: BatchRef | None = None
    sender: Annotated[str | None, Field(alias="from", max_length=_MAX_ADDRESS)] = None
    to: list[DraftAddress] = []
    cc: list[DraftAddress] = []
    bcc: list[DraftAddress] = []
    subject: str = ""
    text: str = ""
    content_ids: Annotated[list[ContentId | None], Field(max_length=MAX_UPLOADS)] = []
    """The Content-ID of each of ``files[]`` in order that the HTML shows in place, null for an
    attachment; files past its end are attachments."""
    keep: Annotated[list[KeepRequest], Field(max_length=_MAX_KEPT)] = []

    def fields(self, html: str | None) -> service.DraftFields:
        return service.DraftFields(
            account=self.account,
            compose_id=self.compose_id,
            ref=self.ref,
            answers=self.answers,
            forwards=self.forwards,
            sender=self.sender,
            to=[address.mailbox() for address in self.to],
            cc=[address.mailbox() for address in self.cc],
            bcc=[address.mailbox() for address in self.bcc],
            subject=self.subject,
            text=self.text,
            html=html,
            keep=[(kept.ref, kept.part) for kept in self.keep if not kept.inline],
            keep_inline=[(kept.ref, kept.part) for kept in self.keep if kept.inline],
        )


class SavedDraftAnswer(BaseModel):
    ref: str | None
    parts: list[MessagePart]
    inline: list[ShownPart]
    replaced: bool
    autosave: bool

    @classmethod
    def of(cls, saved: SavedDraft) -> SavedDraftAnswer:
        return cls(
            ref=None if saved.ref is None else format_ref(saved.ref),
            parts=[MessagePart.of(info) for info in saved.parts],
            inline=_shown(saved.inline),
            replaced=saved.replaced in ("expunged", "trashed"),
            autosave=saved.autosave,
        )


class FoundDraftAnswer(BaseModel):
    ref: str
    parts: list[MessagePart]
    inline: list[ShownPart]

    @classmethod
    def of(cls, draft: Draft) -> FoundDraftAnswer:
        return cls(
            ref=format_ref(draft.ref),
            parts=[MessagePart.of(info) for info in draft.attachments],
            inline=_shown(draft.inline),
        )


class DraftSend(BaseModel):
    send_id: str
    state: str
    code: str | None


class DraftAnswer(BaseModel):
    ref: str
    account: int
    compose_id: str | None
    from_: str | None = Field(serialization_alias="from")
    to: list[MailAddress]
    cc: list[MailAddress]
    bcc: list[MailAddress]
    subject: str
    text: str
    html: str | None
    answers: str | None
    forwards: str | None
    lossy: bool
    parts: list[MessagePart]
    inline: list[ShownPart]
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
            html=draft.html,
            answers=None if draft.answers is None else format_ref(draft.answers),
            forwards=None if draft.forwards is None else format_ref(draft.forwards),
            lossy=draft.lossy,
            parts=[MessagePart.of(info) for info in draft.attachments],
            inline=_shown(draft.inline),
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
    if len(value.encode()) > service.DRAFT_FIELD_BYTES:
        raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"])
    try:
        return DraftRequest.model_validate_json(value)
    except ValidationError as error:
        raise RequestValidationError(
            error.errors(include_url=False, include_context=False, include_input=False)
        ) from None


async def _attachment(upload: UploadFile, content_id: str | None) -> OutgoingAttachment:
    return OutgoingAttachment(
        upload.filename or "",
        upload.content_type or _UPLOAD_TYPE,
        await upload.read(),
        content_id,
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
    so that a file is not held twice while it runs. The HTML is a part of its own, raw: in the
    JSON its escapes would let a part's size bind before the HTML cap, which counts its UTF-8."""
    limit = await run_in_threadpool(service.upload_limit, ctx, user.id)
    if _declared_over(request.headers.get("content-length", ""), limit):
        raise _too_large()
    with service.upload_place(ctx, user.id):
        body = Request(request.scope, _at_most(request.receive, limit))
        try:
            async with body.form(
                max_files=MAX_UPLOADS, max_fields=2, max_part_size=MAX_HTML_BYTES
            ) as form:
                draft = _draft_request(form.get("draft"))
                html = form.get("html")
                if not isinstance(html, str | None):
                    raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"])
                files = [item for item in form.getlist("files[]") if isinstance(item, UploadFile)]
                if len(draft.content_ids) > len(files):
                    raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"])
                marks = [*draft.content_ids, *[None] * (len(files) - len(draft.content_ids))]
                uploads = [await _attachment(f, cid) for f, cid in zip(files, marks, strict=True)]
        except HTTPException:  # how Starlette words a malformed body or too many parts
            raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"]) from None
        fields = draft.fields(html)
        saved = await run_in_threadpool(service.save_draft, ctx, user.id, fields, uploads)
    return SavedDraftAnswer.of(saved)


# Ahead of /drafts/{ref}, which would take "find" as a ref.
@mail_router.get("/drafts/find")
def find_draft(
    account: Annotated[int, Query(ge=1, le=MAX_SAFE_INTEGER)],
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
