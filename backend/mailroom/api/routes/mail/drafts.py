"""Drafts and sends: the prefill of a reply, a forward or an unsubscribe by mail, saving a draft
with its uploads (multipart, bounded before it is read), importing a message built elsewhere or
converting its HTML to text, finding, reading, checking and deleting one, sending it (held for an
Undo, if asked) or scheduling it for later, and reading the send, or the held sends."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Final, Literal

from fastapi import Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)
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
from mailroom.api.token_access import open_to_tokens
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

    from mailroom_core.mail import Draft, Finding, InlinePart, SavedDraft

_MAX_KEPT: Final = 50
_MAX_ADDRESS: Final = 1024
_MAX_NAME: Final = 1024
_COMPOSE_ID: Final = r"^[A-Za-z0-9_-]{1,64}$"
_UPLOAD_TYPE: Final = "application/octet-stream"
_MAX_CONTENT_ID: Final = 250
_PLAIN_TEXT_BODY: Final = MAX_HTML_BYTES + 64 * 1024
"""Of a conversion to plain text: its ``html`` part and the multipart framing."""


def _mailboxes(mailboxes: Iterable[Mailbox]) -> list[MailAddress]:
    return [MailAddress(name=mailbox.name or "", address=mailbox.address) for mailbox in mailboxes]


class PrefillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: BatchRef
    mode: Literal["reply", "reply_all", "forward", "unsubscribe"]
    quote: Annotated[str | None, Field(max_length=service.MAX_QUOTE_CHARS)] = None
    """The text selected in the message, which a reply quotes instead of all of it."""


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


class PlainTextAnswer(BaseModel):
    text: str


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


class ImportRequest(BaseModel):
    """The ``draft`` part of an import. From, recipients and subject left out are the .eml's; the
    HTML or the .eml comes in a part of its own."""

    model_config = ConfigDict(extra="forbid")

    account: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
    compose_id: Annotated[str, Field(pattern=_COMPOSE_ID)]
    ref: BatchRef | None = None
    sender: Annotated[str | None, Field(alias="from", max_length=_MAX_ADDRESS)] = None
    to: list[DraftAddress] | None = None
    cc: list[DraftAddress] | None = None
    bcc: list[DraftAddress] | None = None
    subject: str | None = None
    content_ids: Annotated[list[ContentId | None], Field(max_length=MAX_UPLOADS)] = []
    """As a save's: the Content-ID of each of ``files[]`` its HTML shows in place."""

    def fields(self, *, eml: bytes | None, html: str | None) -> service.ImportFields:
        def mailboxes(addresses: list[DraftAddress] | None) -> list[Mailbox] | None:
            return None if addresses is None else [address.mailbox() for address in addresses]

        return service.ImportFields(
            account=self.account,
            compose_id=self.compose_id,
            ref=self.ref,
            sender=self.sender,
            to=mailboxes(self.to),
            cc=mailboxes(self.cc),
            bcc=mailboxes(self.bcc),
            subject=self.subject,
            eml=eml,
            html=html,
        )


class FindingAnswer(BaseModel):
    code: str
    text: str
    count: int
    items: list[str]

    @classmethod
    def of(cls, finding: Finding) -> FindingAnswer:
        return cls(
            code=finding.code, text=finding.text, count=finding.count, items=list(finding.items)
        )


class FindingsAnswer(BaseModel):
    findings: list[FindingAnswer]


class ImportedDraftAnswer(SavedDraftAnswer):
    findings: list[FindingAnswer]


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
    designed: bool
    """Imported as built: it is sent as stored, and a save is refused."""
    parts: list[MessagePart]
    inline: list[ShownPart]
    send: DraftSend | None
    scheduled: ScheduledAnswer | None = None
    """Its held send still to go (send later, or Undo's), on ``GET /drafts/{ref}``."""

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
            designed=draft.designed,
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
    undoable: bool = False
    """Hold the send for the user's Undo delay (``undo_send_seconds``) first, unless that is 0."""


class TrialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_id: Annotated[str, Field(pattern=_COMPOSE_ID)]


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    send_at: AwareDatetime


class SendAccepted(BaseModel):
    send_id: str


class TrialAccepted(SendAccepted):
    to: str


class ScheduledAnswer(BaseModel):
    scheduled_id: str
    account: int
    ref: str
    subject: str
    recipients: list[str]
    send_at: datetime
    remaining_ms: int
    """How long until ``send_at`` as the server answers, by its clock, not the client's."""
    state: str
    code: str | None
    by: Literal["user", "agent"]
    """Who scheduled it: the user, or an agent."""
    app: str | None
    """An agent's connected app (its name, or its client id when it gave none) or API token."""

    @classmethod
    def of(cls, view: service.ScheduledView) -> ScheduledAnswer:
        remaining = view.send_at - datetime.now(UTC)
        return cls(
            scheduled_id=view.scheduled_id,
            account=view.account,
            ref=view.ref,
            subject=view.subject,
            recipients=list(view.recipients),
            send_at=view.send_at,
            remaining_ms=max(0, round(remaining.total_seconds() * 1000)),
            state=view.state,
            code=view.code,
            by=view.by,
            app=view.app,
        )


class ScheduledList(BaseModel):
    scheduled: list[ScheduledAnswer]


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


def _draft_request[T: BaseModel](value: object, model: type[T]) -> T:
    if not isinstance(value, str):
        raise RequestValidationError(
            [{"type": "missing", "loc": ("body", "draft"), "msg": "Field required"}]
        )
    if len(value.encode()) > service.DRAFT_FIELD_BYTES:
        raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"])
    try:
        return model.model_validate_json(value)
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
@open_to_tokens("mail_draft")
def prefill_draft(request: PrefillRequest, ctx: Context, user: CurrentUser) -> PrefillAnswer:
    prefilled = service.prefill(
        ctx, user.id, request.ref, request.mode, quote=request.quote, via=ctx.origin.via
    )
    return PrefillAnswer.of(prefilled)


async def _plain_text(request: Request, ctx: Context, user_id: int) -> PlainTextAnswer:
    """The text the ``html`` part, alone in the body, would be sent with; saves nothing."""
    limit = _PLAIN_TEXT_BODY
    if _declared_over(request.headers.get("content-length", ""), limit):
        raise _too_large()
    with service.upload_place(ctx, user_id):
        body = Request(request.scope, _at_most(request.receive, limit))
        try:
            async with body.form(max_files=0, max_fields=1, max_part_size=MAX_HTML_BYTES) as form:
                html = form.get("html")
        except HTTPException:
            raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"]) from None
        if not isinstance(html, str):
            raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"])
        return PlainTextAnswer(text=await run_in_threadpool(service.plain_text, html))


async def _import(
    request: Request, ctx: Context, user_id: int, mode: Literal["eml", "html", "kept"]
) -> ImportedDraftAnswer:
    """A message built elsewhere saved as a draft as it is: the ``eml`` file, the ``html`` part
    with its ``files[]``, or (``kept``) the draft ``ref`` imported before, under the ``draft``
    part's fields; bounded as a save is."""
    limit = await run_in_threadpool(service.upload_limit, ctx, user_id)
    if _declared_over(request.headers.get("content-length", ""), limit):
        raise _too_large()
    with service.upload_place(ctx, user_id):
        body = Request(request.scope, _at_most(request.receive, limit))
        try:
            async with body.form(
                max_files={"html": MAX_UPLOADS, "eml": 1, "kept": 0}[mode],
                max_fields=2 if mode == "html" else 1,
                max_part_size=MAX_HTML_BYTES,
            ) as form:
                draft = _draft_request(form.get("draft"), ImportRequest)
                eml: bytes | None = None
                html: str | None = None
                uploads: list[OutgoingAttachment] = []
                if mode == "kept":
                    if draft.ref is None or draft.content_ids:
                        raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"])
                elif mode == "eml":
                    file = form.get("eml")
                    if not isinstance(file, UploadFile) or draft.content_ids:
                        raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"])
                    eml = await file.read()
                else:
                    given = form.get("html")
                    files = [i for i in form.getlist("files[]") if isinstance(i, UploadFile)]
                    if not isinstance(given, str) or len(draft.content_ids) > len(files):
                        raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"])
                    html = given
                    marks = [*draft.content_ids, *[None] * (len(files) - len(draft.content_ids))]
                    uploads = [await _attachment(f, c) for f, c in zip(files, marks, strict=True)]
        except HTTPException:  # how Starlette words a malformed body or too many parts
            raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"]) from None
        fields = draft.fields(eml=eml, html=html)
        imported = await run_in_threadpool(
            service.import_draft, ctx, user_id, fields, uploads, origin=ctx.origin
        )
    saved = SavedDraftAnswer.of(imported.saved)
    return ImportedDraftAnswer(
        **saved.model_dump(), findings=[FindingAnswer.of(f) for f in imported.findings]
    )


@mail_router.post("/drafts", status_code=201)
@open_to_tokens("mail_draft")
async def save_draft(
    request: Request,
    ctx: Context,
    user: CurrentUser,
    response: Response,
    convert: Annotated[Literal["text"] | None, Query()] = None,
    import_: Annotated[Literal["eml", "html", "kept"] | None, Query(alias="import")] = None,
) -> SavedDraftAnswer | ImportedDraftAnswer | PlainTextAnswer:
    """Multipart, parsed here and not by a signature: FastAPI would read the whole body before
    the signed-in user is known, and a declared size is refused before any of it is read. So is
    a save past the user's few at a time, which the mail gates could only refuse once it was
    read. Files past the memory threshold spool to disk until the form closes, before the save,
    so that a file is not held twice while it runs. The HTML is a part of its own, raw: in the
    JSON its escapes would let a part's size bind before the HTML cap, which counts its UTF-8.

    ``?convert=text`` saves nothing: 200 with the text the ``html`` part would be sent with, for
    the composer's switch to plain text. It is this route, not one of its own, because the host's
    proxy already lets a body this size through to it and to no other.

    ``?import=eml`` or ``?import=html`` saves a message built elsewhere as it is (an ``eml`` file,
    or the ``html`` part and its ``files[]``), answering the checks of it too: the same route for
    the same reason. ``?import=kept`` saves the imported draft ``ref`` again as it was built,
    under the ``draft`` part's From, recipients and subject: how one is addressed."""
    if convert is not None:
        response.status_code = 200
        return await _plain_text(request, ctx, user.id)
    if import_ is not None:
        return await _import(request, ctx, user.id, import_)
    limit = await run_in_threadpool(service.upload_limit, ctx, user.id)
    if _declared_over(request.headers.get("content-length", ""), limit):
        raise _too_large()
    with service.upload_place(ctx, user.id):
        body = Request(request.scope, _at_most(request.receive, limit))
        try:
            async with body.form(
                max_files=MAX_UPLOADS, max_fields=2, max_part_size=MAX_HTML_BYTES
            ) as form:
                draft = _draft_request(form.get("draft"), DraftRequest)
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
        saved = await run_in_threadpool(
            service.save_draft, ctx, user.id, fields, uploads, origin=ctx.origin
        )
    return SavedDraftAnswer.of(saved)


# Ahead of /drafts/{ref}, which would take "find" as a ref.
@mail_router.get("/drafts/find")
@open_to_tokens("mail_draft")
def find_draft(
    account: Annotated[int, Query(ge=1, le=MAX_SAFE_INTEGER)],
    compose_id: Annotated[str, Query(pattern=_COMPOSE_ID)],
    ctx: Context,
    user: CurrentUser,
) -> FoundDraftAnswer:
    return FoundDraftAnswer.of(
        service.find_draft(ctx, user.id, account, compose_id, via=ctx.origin.via)
    )


@mail_router.get("/drafts/{ref}")
@open_to_tokens("mail_draft")
def read_draft(ref: Ref, ctx: Context, user: CurrentUser) -> DraftAnswer:
    answer = DraftAnswer.of(service.read_draft(ctx, user.id, ref, via=ctx.origin.via))
    held = service.scheduled_for(ctx, user.id, answer.ref)
    if held is not None:
        answer.scheduled = ScheduledAnswer.of(held)
    return answer


@mail_router.get("/drafts/{ref}/findings")
@open_to_tokens("mail_draft")
def check_draft(ref: Ref, ctx: Context, user: CurrentUser) -> FindingsAnswer:
    """The checks before sending a draft, from it as stored: an imported one's import included."""
    findings = service.draft_findings(ctx, user.id, ref, via=ctx.origin.via)
    return FindingsAnswer(findings=[FindingAnswer.of(finding) for finding in findings])


@mail_router.delete("/drafts/{ref}")
@open_to_tokens("mail_draft")
def delete_draft(ref: Ref, ctx: Context, user: CurrentUser) -> DraftRemoved:
    return DraftRemoved(removed=service.delete_draft(ctx, user.id, ref, origin=ctx.origin))


@mail_router.post("/drafts/{ref}/send", status_code=202)
@open_to_tokens("mail_send")
def send_draft(
    ref: Ref,
    ctx: Context,
    user: CurrentUser,
    response: Response,
    request: SendRequest | None = None,
) -> SendAccepted | SendAnswer | ScheduledAnswer:
    """202 with the send to follow, unless this draft's key has been delivered already: then 200
    with what came of it, and nothing goes out. ``undoable``, while the user's Undo delay is not
    0, 202 with the held send instead (``GET``/``DELETE /scheduled/{id}``), unless a send of the
    draft began already. From an agent the send mode decides, and ``confirm_resend`` is not its to
    say: a send not confirmed stays refused."""
    asked_again = request is not None and request.confirm_resend
    confirm_resend = asked_again and not ctx.origin.agent
    undoable = request is not None and request.undoable and not confirm_resend
    if undoable and user.undo_send_seconds > 0:
        delay = timedelta(seconds=user.undo_send_seconds)
        held = service.hold_send(ctx, user.id, ref, delay, origin=ctx.origin)
        if held is not None:
            return ScheduledAnswer.of(held)
    send = service.send_draft(
        ctx, user.id, ref, by=ctx.origin.actor, confirm_resend=confirm_resend, origin=ctx.origin
    )
    if send.state == "sending":
        return SendAccepted(send_id=send.send_id)
    response.status_code = 200
    return SendAnswer.of(send)


@mail_router.post("/drafts/{ref}/test", status_code=202)
def send_test(ref: Ref, request: TrialRequest, ctx: Context, user: CurrentUser) -> TrialAccepted:
    """The draft sent as a test to the account's default identity (``to``), subject prefixed
    ``[Test]``, the draft left as it is; the send is followed as any other. ``test_id`` is the
    click's: the same one again answers the send it made."""
    trial = service.send_test(ctx, user.id, ref, request.test_id)
    return TrialAccepted(send_id=trial.send.send_id, to=trial.to)


@mail_router.post("/drafts/{ref}/schedule", status_code=201)
@open_to_tokens("mail_send")
def schedule_draft(
    ref: Ref, request: ScheduleRequest, ctx: Context, user: CurrentUser
) -> ScheduledAnswer:
    """Send later: the draft held for the worker to send at ``send_at``, still to come."""
    return ScheduledAnswer.of(
        service.schedule_later(ctx, user.id, ref, request.send_at, origin=ctx.origin)
    )


@mail_router.get("/sends")
@open_to_tokens("mail_send")
def read_compose_send(
    account: Annotated[int, Query(ge=1, le=MAX_SAFE_INTEGER)],
    compose_id: Annotated[str, Query(pattern=_COMPOSE_ID)],
    ctx: Context,
    user: CurrentUser,
) -> SendAnswer:
    """The send of the draft saved under ``compose_id``, there or not by now."""
    return SendAnswer.of(service.read_compose_send(ctx, user.id, account, compose_id))


@mail_router.get("/sends/{send_id}")
@open_to_tokens("mail_send")
def read_send(
    send_id: Annotated[str, Path(max_length=64)], ctx: Context, user: CurrentUser
) -> SendAnswer:
    return SendAnswer.of(service.read_send(ctx, user.id, send_id))


@mail_router.get("/scheduled")
@open_to_tokens("mail_send")
def list_scheduled(ctx: Context, user: CurrentUser) -> ScheduledList:
    """The user's held sends, soonest first, failed ones included until cancelled."""
    return ScheduledList(
        scheduled=[ScheduledAnswer.of(view) for view in service.list_scheduled(ctx, user.id)]
    )


@mail_router.get("/scheduled/{scheduled_id}")
@open_to_tokens("mail_send")
def read_scheduled(
    scheduled_id: Annotated[str, Path(max_length=64)], ctx: Context, user: CurrentUser
) -> ScheduledAnswer:
    """A held send; ``not_found`` once it went (``GET /sends`` has its send) or was cancelled."""
    return ScheduledAnswer.of(service.read_scheduled(ctx, user.id, scheduled_id))


@mail_router.delete("/scheduled/{scheduled_id}")
@open_to_tokens("mail_send")
def cancel_scheduled(
    scheduled_id: Annotated[str, Path(max_length=64)], ctx: Context, user: CurrentUser
) -> ScheduledAnswer:
    """Cancel a held send, the draft left in Drafts; ``send_started`` once the worker took it.
    A token cancels only what it scheduled itself, ``not_found`` otherwise."""
    return ScheduledAnswer.of(
        service.cancel_scheduled(ctx, user.id, scheduled_id, origin=ctx.origin)
    )
