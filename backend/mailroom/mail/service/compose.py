"""Writing mail: a reply's or a forward's prefill, drafts saved, read, found and deleted, and what
a send shares with them. A draft saved, imported or deleted has its folders synced again into the
mail index (``index_writes.resync``) once the call answered."""

from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, Literal, NamedTuple, cast

from mailroom.audit import WEB, AuditTrail, Origin, kept_nowhere
from mailroom.errors import Refusal, mail_refusal
from mailroom.mail import signatures
from mailroom.mail.index import writes as index_writes
from mailroom.mail.service._shared import (
    _Accounts,
    _accounts,
    _call,
    _find_special_path,
    _message_ref,
    _ref_accounts,
    _Stoppable,
    _usable,
)
from mailroom_core import mail as core
from mailroom_core.mail import MailError
from mailroom_core.render import clean_header

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from mailroom.accounts.service import Via
    from mailroom.context import AppContext
    from mailroom.mail.signatures import Signature
    from mailroom_core.config import AccountConfig
    from mailroom_core.mail import (
        AttachmentSource,
        Draft,
        InlinePart,
        Mailbox,
        MailConnections,
        MessageInput,
        MessageRef,
        OutgoingAttachment,
        SavedDraft,
    )
    from mailroom_core.render import PartInfo
    from mailroom_core.store import SendRecord, SendState

UNDELIVERABLE: Final = (
    "A recipient is not a deliverable address, or the mail server refused the message; "
    "nothing was sent."
)
DRAFT_FIELD_BYTES: Final = 1024 * 1024
"""Of a save's ``draft`` part, its fields as one JSON document but the HTML: the text, the
recipients."""
UPLOAD_OVERHEAD: Final = core.MAX_HTML_BYTES + DRAFT_FIELD_BYTES + 1024 * 1024
"""What a save's request may hold beyond its attachments: its ``html`` part (the HTML cap, in the
same bytes), its ``draft`` part and the multipart framing. With the largest attachments it is the
upload route's body limit (``web/nginx-default.conf``), which the host's proxy must allow too."""
SEND_HOLDS_THE_DRAFT: Final = frozenset({"sending", "unknown", "sent"})
"""Its delivery is going, unresolved or done: a new version could slip past ``delivery_unknown``
with a new key, or make a replayed "sent" name recipients who never got it."""
DRAFT_NOT_EDITABLE: Final = "This draft was sent, or may have been, so it cannot be changed."
DESIGNED_KEPT: Final = "An imported message is kept as it was built: import it again to change it."
IMPORT_OVER_REPLY: Final = (
    "A reply or a forward is not replaced by an imported message: start a new draft to import it."
)
DRAFT_GONE: Final = frozenset({"not_found", "stale_reference"})
"""What reading a draft answers once its ref no longer holds it."""
MAX_QUOTE_CHARS: Final = core.MAX_QUOTED_CHARS
"""Of the part of a message a reply quotes instead of all of it: as much as a whole one's
(``web/src/api/mail.ts`` repeats it)."""
_TYPE_HERE: Final = "<p></p>"
"""Above a prefill's quote: where the user writes; under an agent's HTML, what parts it from the
original (``send._above``)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Prefill:
    """The fields of the draft a reply or a forward starts as; nothing is written yet."""

    account: int
    sender: str
    to: tuple[Mailbox, ...]
    cc: tuple[Mailbox, ...]
    subject: str
    text: str
    """Room to write, the identity's signature (``signatures.text_block``), then the original
    quoted or forwarded."""
    html: str | None
    """The same as HTML, the original's HTML quoted (``core.quoted_original_html``) or forwarded
    (``core.forwarded_original_html``) below an empty paragraph and the signature's block
    (``signatures.html_block``, empty without one); None for an unsubscribe."""
    answers: str | None
    """The message a send is to mark ``\\Answered``: the ref, for a reply."""
    forwards: str | None
    """The message a send is to mark ``$Forwarded``: the ref, for a forward."""
    source: str
    """The ref ``attachments`` are parts of."""
    attachments: tuple[PartInfo, ...]
    """For a forward, the original's attachments, offered for keeping."""
    inline: tuple[InlinePart, ...] = ()
    """For a forward, the original's inline parts ``html`` shows, to keep as inline."""


class KeptPart(NamedTuple):
    """A part of an existing message to attach."""

    ref: str
    part: str
    name: str | None = None
    """The name to attach it under, an agent's, instead of its own."""
    content_id: str | None = None
    """On an import, what makes it an inline part of the HTML."""


@dataclass(frozen=True, slots=True, kw_only=True)
class DraftFields:
    """A draft as the user wrote it. Its addresses are checked here; the caps on the text, the HTML
    and the attachments too (``core.check_fields``, ``core.check_uploads``)."""

    account: int
    compose_id: str
    ref: str | None = None
    answers: str | None = None
    forwards: str | None = None
    sender: str | None = None
    to: Sequence[Mailbox] = ()
    cc: Sequence[Mailbox] = ()
    bcc: Sequence[Mailbox] = ()
    subject: str = ""
    text: str = ""
    html: str | None = None
    """The body as HTML, sanitised when composed; the text is then derived from it and ``text``
    is not used."""
    keep: Sequence[KeptPart] = ()
    """The existing parts it keeps, its previous version's included."""
    keep_inline: Sequence[KeptPart] = ()
    """The existing inline parts ``html`` shows: its previous version's, or the
    forwarded message's."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SendView:
    """A send as a client follows it; ``docs/requirements/mail-client.md``, *Send state machine*."""

    send_id: str
    state: SendState
    code: str | None = None
    filed: bool | None = None
    rejected: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    note_codes: tuple[str, ...] = ()
    """``gate.SendNoteCode``s of ``notes``, for a client that words them itself; ``filed`` says
    the same as ``NOT_FILED``, which has none."""
    settled: bool = False
    """The send has all its notes: filing, \\Answered and the draft's removal are done, or failed.
    ``sent`` comes earlier, the moment the server took the message."""


@dataclass(frozen=True, slots=True)
class DraftView:
    draft: Draft
    send: SendView | None
    """The send of this draft, so that reopening one shows how it went."""


@contextmanager
def _as_refusals() -> Iterator[None]:
    try:
        yield
    except MailError as error:
        raise mail_refusal(error.code, error.message) from None


def _writable(account: AccountConfig) -> None:
    """``send_disabled`` on an account in send mode ``off``: no drafts or sends."""
    with _as_refusals():
        core.check_sendable(account)


def _deliverable(mailboxes: Iterable[Mailbox]) -> tuple[Mailbox, ...]:
    """``mailboxes`` in the form every hop agrees on; mailroom-core words a bad one
    ``invalid_argument``, and the REST contract ``recipient_refused``, with a fixed text."""
    try:
        return tuple(replace(m, address=core.deliverable_address(m.address)) for m in mailboxes)
    except MailError:
        raise mail_refusal("recipient_refused", UNDELIVERABLE) from None


def _can_be_sent_to(mailbox: Mailbox) -> bool:
    try:
        core.deliverable_address(mailbox.address)
    except MailError:
        return False
    return True


def _stoppable_call[T](
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    event: str,
    via: Via,
    account_id: int,
    work: Callable[[MailConnections], T],
    *,
    on: Sequence[int] | None = None,
) -> T:
    """``_call``, writing nothing further once the deadline has passed (``_Stoppable``)."""
    stop = threading.Event()
    return _call(
        ctx,
        user_id,
        accounts,
        event,
        via,
        account_id,
        lambda connections: work(cast("MailConnections", _Stoppable(connections, stop))),
        on=on,
        on_deadline=stop.set,
    )


def prefill(
    ctx: AppContext,
    user_id: int,
    ref: str,
    mode: Literal["reply", "reply_all", "forward", "unsubscribe"],
    *,
    quote: str | None = None,
    signature: bool = True,
    via: Via = "web",
) -> Prefill:
    """What answering, forwarding or unsubscribing by mail from the message ``ref`` names starts
    from; writes nothing. A recipient no send could reach is left out: it would refuse every save
    of the draft. A reply with a ``quote`` (the text the user selected in the message, at most
    ``MAX_QUOTE_CHARS``) quotes only that, as text in both forms; a forward or an unsubscribe
    ignores it. A reply or a forward carries the signature of the identity it goes from, unless
    ``signature`` is false; an unsubscribe never does."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    account = accounts.config(message.account)
    _writable(account)
    if mode == "unsubscribe":
        return _unsubscribe_prefill(ctx, user_id, accounts, ref, account, via)
    original = _call(
        ctx,
        user_id,
        accounts,
        "mail_prefilled",
        via,
        message.account,
        lambda connections: core.original_message(connections, message, with_html=True),
    )
    zone = ctx.time_zone(user_id)
    with _as_refusals():
        sender = core.from_identity(account, None, original.addressed).address
    signed = _signature(ctx, user_id, account.id, sender) if signature else None
    text_top = "\n\n" if signed is None else f"\n\n{signatures.text_block(signed)}\n\n"
    html_top = f"{_TYPE_HERE}{signatures.html_block(signed)}" if signature else _TYPE_HERE
    with _as_refusals():
        if mode == "forward":
            return Prefill(
                account=account.id,
                sender=sender,
                to=(),
                cc=(),
                subject=_subject_cut(core.forward_subject(original.subject)),
                text=f"{text_top}{core.forwarded_original(original, zone)}",
                html=f"{html_top}{core.forwarded_original_html(original, zone)}",
                answers=None,
                forwards=ref,
                source=ref,
                attachments=original.attachments,
                inline=original.inline,
            )
        answered = core.reply_recipients(original, account, reply_all=mode == "reply_all")
        selected = _quoted_part(quote)
        if selected is not None:
            original = replace(original, text=selected, html=None)
        return Prefill(
            account=account.id,
            sender=sender,
            to=tuple(filter(_can_be_sent_to, answered.to)),
            cc=tuple(filter(_can_be_sent_to, answered.cc)),
            subject=_subject_cut(core.reply_subject(original.subject)),
            text=f"{text_top}{core.quoted_original(original, zone)}",
            html=f"{html_top}{core.quoted_original_html(original, zone)}",
            answers=ref,
            forwards=None,
            source=ref,
            attachments=(),
        )


def _signature(ctx: AppContext, user_id: int, account_id: int, address: str) -> Signature | None:
    with ctx.session_maker() as db:
        return signatures.account_signatures(db, user_id, account_id).get(address.lower())


def identity_signature(
    ctx: AppContext, user_id: int, account_id: int, sender: str | None
) -> Signature | None:
    """The signature of the identity a message from the user's account goes from: ``sender``,
    else the default; None without one, or for a ``sender`` that is no identity, which the
    message's save or send then refuses."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)
    try:
        address = core.from_identity(accounts.config(account_id), sender).address
    except MailError:
        return None
    return _signature(ctx, user_id, account_id, address)


def _quoted_part(quote: str | None) -> str | None:
    if quote is None:
        return None
    text = quote.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return text if text.strip() else None


def plain_text(html: str) -> str:
    """The text a draft's HTML body would be sent with, for a switch to plain text:
    ``invalid_argument`` when that is longer than a plain-text body may be, so the composer stays
    in rich text rather than holding text no save would take."""
    with _as_refusals():
        core.check_fields(html=html)
    text = core.html_text(html)
    with _as_refusals():
        core.check_fields(text=text)
    return text


NO_UNSUBSCRIBE_ADDRESS: Final = "This message offers no address to unsubscribe by mail."


def _unsubscribe_prefill(
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    ref: str,
    account: AccountConfig,
    via: Via,
) -> Prefill:
    """A draft to the message's List-Unsubscribe ``mailto:``: its one address, subject and body,
    nothing else it asks for (cc, bcc, other fields), from the identity the message arrived at.
    The user sends it, through the send gate like any other."""
    view = _call(
        ctx,
        user_id,
        accounts,
        "mail_prefilled",
        via,
        account.id,
        lambda connections: core.read_message(connections, ref, body_chars=1, account=account),
    )
    info = view.headers.mailing_list
    link = None if info is None else info.unsubscribe_mailto
    fields = None if link is None else core.mailto_fields(link)
    if fields is None or not _can_be_sent_to(to := core.Mailbox(fields.to)):
        raise Refusal("invalid", "no_unsubscribe_address", NO_UNSUBSCRIBE_ADDRESS)
    with _as_refusals():
        sender = core.from_identity(account, view.sent_to)
    return Prefill(
        account=account.id,
        sender=sender.address,
        to=(to,),
        cc=(),
        subject=_subject_cut(fields.subject or "unsubscribe"),
        text=fields.body,
        html=None,
        answers=None,
        forwards=None,
        source=ref,
        attachments=(),
    )


def _subject_cut(subject: str) -> str:
    """Within the cap a save holds it to: the prefix may have taken it over."""
    return clean_header(subject, core.MAX_SUBJECT_CHARS)


def _replaced(
    ctx: AppContext,
    user_id: int,
    connections: MailConnections,
    replaces: MessageRef | None,
    compose_id: str,
    *,
    importing: bool = False,
) -> Draft | None:
    """The version a save replaces. Its ``compose_id`` is the send key, so a new one would forget
    how the draft's send went and deliver it again. A draft written elsewhere has none: the
    client gives it one, which ``_not_held`` allows only while no send stands under its ref. That
    send is why such a draft is normally gone, so a ref that holds nothing is asked about it too.
    One imported as built is replaced only by ``importing`` another: no save composes it again."""
    if replaces is None:
        return None
    try:
        previous = core.read_draft(connections, replaces)
    except MailError as error:
        if error.code in DRAFT_GONE:
            _not_held(ctx, user_id, replaces)
        raise
    if previous.designed and not importing:
        raise MailError("invalid_argument", DESIGNED_KEPT)
    if previous.compose_id is None:
        _not_held(ctx, user_id, replaces)
    elif previous.compose_id != compose_id:
        raise MailError("invalid_argument", "A draft keeps its compose_id across versions.")
    return previous


def _holding_send(ctx: AppContext, user_id: int, ref: MessageRef) -> SendRecord | None:
    """The send that holds the draft written elsewhere at ``ref``, there or not by now."""
    send = ctx.store(user_id).send_state_by_key(_ref_key(ref))
    return send if send is not None and send.state in SEND_HOLDS_THE_DRAFT else None


def _not_held(ctx: AppContext, user_id: int, ref: MessageRef) -> None:
    """Refuses to replace a draft written elsewhere whose send holds it: the key of such a draft
    is its ref, so the next version would stand free of that send. Moving the state to the new key
    instead could strand it if the APPEND then failed. An own draft's key survives a save. The
    refusal says how the send stands, which the client could no longer read from a draft that
    is gone."""
    send = _holding_send(ctx, user_id, ref)
    if send is not None:
        held = {"send_id": str(send.id), "state": send.state, "code": None}
        raise Refusal("conflict", "draft_not_editable", DRAFT_NOT_EDITABLE, extra={"send": held})


def _threaded(
    connections: MailConnections, message: MessageInput, previous: Draft | None
) -> MessageInput:
    """``message`` in its thread, which the client does not know: from the version it replaces,
    which also keeps the thread of a draft written elsewhere, else from the message it answers.
    One that is gone by now cannot be answered, but the draft is still saved."""
    if previous is not None:
        same = message.answers is None or (
            previous.answers is not None and core.format_ref(previous.answers) == message.answers
        )
        if same and (previous.in_reply_to is not None or previous.references):
            return replace(
                message, in_reply_to=previous.in_reply_to, references=previous.references
            )
    if message.answers is None:
        return message
    try:
        original = core.original_message(connections, core.parse_ref(message.answers))
    except MailError as error:
        if error.code in ("not_found", "stale_reference", "mailbox_missing"):
            return replace(message, answers=None)
        raise
    thread = core.threading(original)
    return replace(message, in_reply_to=thread.in_reply_to, references=thread.references)


def upload_limit(ctx: AppContext, user_id: int) -> int:
    """The largest save request the user can have a reason to make, in bytes."""
    return core.attachment_cap(_accounts(ctx, user_id).write_limits) + UPLOAD_OVERHEAD


@contextmanager
def upload_place(ctx: AppContext, user_id: int) -> Iterator[None]:
    """The user's place for a save's body while it is held, or ``busy`` at once."""
    with _as_refusals(), ctx.mail_pool.upload(user_id):
        yield


def save_draft(
    ctx: AppContext,
    user_id: int,
    fields: DraftFields,
    uploads: Sequence[OutgoingAttachment] = (),
    *,
    origin: Origin = WEB,
) -> SavedDraft:
    """Saves the draft, as a first version or as the next version of ``fields.ref``:
    mailroom-core's ``save_draft``, so a failure leaves the last good version where it was.
    Every ref is the user's or the whole save is ``not_found``, before anything is written. An
    agent's save is recorded in the audit trail, the web's are not: they are the user typing."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, fields.account)
    kept = (*fields.keep, *fields.keep_inline)
    named = [fields.ref, fields.answers, fields.forwards, *(part.ref for part in kept)]
    used = _ref_accounts(ctx, user_id, accounts, [ref for ref in named if ref is not None])
    account = accounts.config(fields.account)
    limits = accounts.write_limits
    _writable(account)
    with _as_refusals():
        core.check_fields(
            to=fields.to,
            cc=fields.cc,
            bcc=fields.bcc,
            subject=fields.subject,
            text=fields.text,
            html=fields.html or "",
        )
    to, cc, bcc = _deliverable(fields.to), _deliverable(fields.cc), _deliverable(fields.bcc)
    with _as_refusals():
        if to or cc or bcc:
            core.check_recipient_count((*to, *cc, *bcc), limits.max_recipients)
        sender = core.from_identity(account, fields.sender)
    message = core.MessageInput(
        sender=sender,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=fields.subject,
        text=fields.text,
        html=fields.html,
        attachments=tuple(uploads),
        answers=fields.answers,
        forwards=fields.forwards,
    )
    replaces = None if fields.ref is None else core.parse_ref(fields.ref)
    if fields.answers is not None and fields.forwards is not None:
        raise mail_refusal("invalid_argument", "A draft answers or forwards a message, not both.")
    for named_ref in (fields.ref, fields.answers, fields.forwards):
        if named_ref is not None and core.parse_ref(named_ref).account != account.id:
            elsewhere = "That message belongs to another account."
            raise mail_refusal("invalid_argument", elsewhere)
    keep = _attachment_sources(fields.keep) + _attachment_sources(fields.keep_inline, inline=True)
    note = (
        kept_nowhere
        if origin.via == "web"
        else AuditTrail(ctx.session_maker, user_id, origin, "draft_save").note
    )

    def saved(connections: MailConnections) -> SavedDraft:
        previous = _replaced(ctx, user_id, connections, replaces, fields.compose_id)
        return core.save_draft(
            connections,
            account,
            _threaded(connections, message, previous),
            cap=core.attachment_cap(limits),
            note=note,
            compose_id=fields.compose_id,
            replaces=replaces,
            keep=keep,
        )

    done = _stoppable_call(
        ctx,
        user_id,
        accounts,
        "mail_draft_saved",
        origin.via,
        account.id,
        saved,
        on=sorted({account.id, *used}),
    )
    _resync_drafts(ctx, user_id, account.id, done.folder, replaces)
    return done


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportFields:
    """A message built elsewhere, to save as a draft as it is (``core.import_draft``): an .eml, an
    HTML file with its files, or neither, for the draft ``ref`` imported earlier, kept as built
    under new fields. From, recipients and subject left None are the .eml's (From never: the
    default identity); ``keep`` names parts of existing messages, a Content-ID making
    one an inline part of the HTML."""

    account: int
    compose_id: str
    ref: str | None = None
    sender: str | None = None
    to: Sequence[Mailbox] | None = None
    cc: Sequence[Mailbox] | None = None
    bcc: Sequence[Mailbox] | None = None
    subject: str | None = None
    eml: bytes | None = None
    html: str | None = None
    keep: Sequence[KeptPart] = ()


def import_draft(
    ctx: AppContext,
    user_id: int,
    fields: ImportFields,
    uploads: Sequence[OutgoingAttachment] = (),
    *,
    origin: Origin = WEB,
) -> core.ImportedDraft:
    """Saves an imported message in Drafts as it was built, as a first version or as the next
    version of ``fields.ref`` (itself imported or not, but no reply or forward: the marker of a
    designed draft names no original), keeping its thread, and answers with the checks of what
    was stored. The refs are checked as ``save_draft``'s are, and the save is audited the same
    way."""
    as_stored = fields.eml is None and fields.html is None
    if (
        (fields.eml is not None and fields.html is not None)
        or (as_stored and fields.ref is None)
        or ((fields.eml is not None or as_stored) and (uploads or fields.keep))
    ):
        raise mail_refusal("invalid_argument", "Import an .eml, or an HTML file with its files.")
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, fields.account)
    named = [fields.ref, *(part.ref for part in fields.keep)]
    used = _ref_accounts(ctx, user_id, accounts, [ref for ref in named if ref is not None])
    account = accounts.config(fields.account)
    limits = accounts.write_limits
    cap = core.attachment_cap(limits)
    _writable(account)
    with _as_refusals():
        core.check_fields(
            to=fields.to or (),
            cc=fields.cc or (),
            bcc=fields.bcc or (),
            subject=fields.subject or "",
        )
        core.check_uploads([*(len(f.content) for f in uploads), *(0 for _ in fields.keep)], cap)
    replaces = None if fields.ref is None else core.parse_ref(fields.ref)
    if replaces is not None and replaces.account != account.id:
        raise mail_refusal("invalid_argument", "That message belongs to another account.")
    sources = _attachment_sources(fields.keep)
    note = (
        kept_nowhere
        if origin.via == "web"
        else AuditTrail(ctx.session_maker, user_id, origin, "draft_save").note
    )

    def imported(connections: MailConnections) -> core.ImportedDraft:
        previous = _replaced(ctx, user_id, connections, replaces, fields.compose_id, importing=True)
        if previous is not None and (previous.answers is not None or previous.forwards is not None):
            raise MailError("invalid_argument", IMPORT_OVER_REPLY)
        used_bytes = sum(len(upload.content) for upload in uploads)
        kept = core.attachments_from(connections, sources, cap, used=used_bytes)
        files = (
            *uploads,
            *(replace(a, content_id=k.content_id) for a, k in zip(kept, fields.keep, strict=True)),
        )
        if replaces is not None and as_stored:
            built = core.stored_design(connections, replaces, limits)
        elif fields.eml is not None:
            built = core.import_eml(fields.eml, cap)
        else:
            built = core.import_html(fields.html or "", files, cap)
        to, cc, bcc = (
            _deliverable(built.to if fields.to is None else fields.to),
            _deliverable(built.cc if fields.cc is None else fields.cc),
            _deliverable(built.bcc if fields.bcc is None else fields.bcc),
        )
        core.check_fields(to=to, cc=cc, bcc=bcc)
        if to or cc or bcc:
            core.check_recipient_count((*to, *cc, *bcc), limits.max_recipients)
        message = core.MessageInput(
            sender=core.from_identity(account, fields.sender),
            to=to,
            cc=cc,
            bcc=bcc,
            subject=built.subject if fields.subject is None else fields.subject,
            text="",
            in_reply_to=None if previous is None else previous.in_reply_to,
            references=() if previous is None else previous.references,
        )
        return core.import_draft(
            connections,
            account,
            message,
            built,
            cap=cap,
            note=note,
            compose_id=fields.compose_id,
            replaces=replaces,
        )

    done = _stoppable_call(
        ctx,
        user_id,
        accounts,
        "mail_draft_imported",
        origin.via,
        account.id,
        imported,
        on=sorted({account.id, *used}),
    )
    _resync_drafts(ctx, user_id, account.id, done.saved.folder, replaces)
    return done


def _resync_drafts(
    ctx: AppContext, user_id: int, account_id: int, folder: str, replaced: MessageRef | None
) -> None:
    """The mail index brought in step with a draft saved in ``folder`` over ``replaced``."""
    folders = {folder} if replaced is None else {folder, replaced.folder}
    index_writes.resync(ctx.session_maker, user_id, account_id, sorted(folders))


def draft_findings(
    ctx: AppContext, user_id: int, ref: str, *, via: Via = "web"
) -> tuple[core.Finding, ...]:
    """The checks of a draft before it is sent (``core.check_message``), from it as stored."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    limits = accounts.write_limits
    return _call(
        ctx,
        user_id,
        accounts,
        "mail_draft_checked",
        via,
        message.account,
        lambda connections: core.draft_findings(connections, message, limits),
    )


def _ref_key(ref: MessageRef) -> str:
    return f"ref-{hashlib.sha256(core.format_ref(ref).encode()).hexdigest()}"


def _send_key(draft: Draft) -> str:
    """What guards a draft's send against a second delivery: its account and ``compose_id``, since
    a key is claimed per user and a copy between accounts keeps the id. A draft written elsewhere
    has none, and is one message, so its ref says which send it is."""
    if draft.compose_id is not None:
        return _compose_key(draft.ref.account, draft.compose_id)
    return _ref_key(draft.ref)


def _compose_key(account_id: int, compose_id: str) -> str:
    """The send key of an own draft (``_send_key``)."""
    return f"{account_id}/{compose_id}"


def _texts(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _send_view(send: SendRecord) -> SendView:
    result = send.result if isinstance(send.result, dict) else {}
    code = result.get("code")
    filed = result.get("filed")
    return SendView(
        send_id=str(send.id),
        state=send.state,
        code=str(code) if send.state == "failed" and code is not None else None,
        filed=filed if isinstance(filed, bool) else None,
        rejected=_texts(result.get("rejected")),
        notes=_texts(result.get("notes")),
        note_codes=_texts(result.get("note_codes")),
        settled=isinstance(result.get("notes"), list),
    )


def read_draft(ctx: AppContext, user_id: int, ref: str, *, via: Via = "web") -> DraftView:
    """A draft for the composer: ``ref`` must be in the account's Drafts."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    draft = _call(
        ctx,
        user_id,
        accounts,
        "mail_draft_read",
        via,
        message.account,
        lambda connections: core.read_draft(connections, message),
    )
    send = ctx.store(user_id).send_state_by_key(_send_key(draft))
    return DraftView(draft, None if send is None else _send_view(send))


def find_draft(
    ctx: AppContext, user_id: int, account_id: int, compose_id: str, *, via: Via = "web"
) -> Draft:
    """The newest version saved under ``compose_id``, ``not_found`` without one: what recovers
    a save whose answer was lost."""
    accounts = _accounts(ctx, user_id)
    _usable(ctx, user_id, accounts, account_id)

    def newest(connections: MailConnections) -> Draft:
        versions = core.find_drafts(connections, account_id, compose_id)
        if not versions:
            raise MailError("not_found")
        return core.read_draft(connections, versions[0])

    return _call(ctx, user_id, accounts, "mail_draft_found", via, account_id, newest)


def delete_draft(ctx: AppContext, user_id: int, ref: str, *, origin: Origin = WEB) -> str:
    """Removes a draft: ``expunged`` for one mailroom wrote, by its UID alone; ``trashed`` for
    any other. Its folder, and Trash for one trashed, are synced again into the mail index."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    _writable(accounts.config(message.account))
    removed = AuditTrail(ctx.session_maker, user_id, origin, "draft_remove")
    done = _call(
        ctx,
        user_id,
        accounts,
        "mail_draft_deleted",
        origin.via,
        message.account,
        lambda connections: core.remove_draft(connections, message, removed.note),
    )
    folders = {message.folder}
    if done != "expunged":
        trash = _find_special_path(ctx, user_id, accounts, message.account, "trash")
        folders |= {trash} if trash is not None else set()
    index_writes.resync(ctx.session_maker, user_id, message.account, sorted(folders))
    return done


def _attachment_sources(
    kept: Iterable[KeptPart], *, inline: bool = False
) -> list[AttachmentSource]:
    return [core.AttachmentSource(core.parse_ref(k.ref), k.part, inline, k.name) for k in kept]
