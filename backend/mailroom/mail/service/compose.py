"""Writing mail: a reply's or a forward's prefill, drafts saved, read, found and deleted, and what
a send shares with them."""

from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, Literal, cast

from mailroom.audit import WEB, AuditTrail, Origin, kept_nowhere
from mailroom.errors import Refusal, mail_refusal
from mailroom.mail.service._shared import (
    _Accounts,
    _accounts,
    _call,
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
    from mailroom_core.config import AccountConfig
    from mailroom_core.mail import (
        AttachmentSource,
        Draft,
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
UPLOAD_OVERHEAD: Final = 2 * 1024 * 1024
"""What a save's request may hold beyond its attachments: the text, the recipients and the
multipart framing."""
TIME_ZONE: Final = "UTC"
"""Of the dates quoted in a reply or a forward: the user has no time zone yet."""
SEND_HOLDS_THE_DRAFT: Final = frozenset({"sending", "unknown", "sent"})
"""Its delivery is going, unresolved or done: a new version could slip past ``delivery_unknown``
with a new key, or make a replayed "sent" name recipients who never got it."""
DRAFT_NOT_EDITABLE: Final = "This draft was sent, or may have been, so it cannot be changed."
DRAFT_GONE: Final = frozenset({"not_found", "stale_reference"})
"""What reading a draft answers once its ref no longer holds it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Prefill:
    """The fields of the draft a reply or a forward starts as; nothing is written yet."""

    account: str
    sender: str
    to: tuple[Mailbox, ...]
    cc: tuple[Mailbox, ...]
    subject: str
    text: str
    answers: str | None
    """The message a send is to mark ``\\Answered``: the ref, for a reply."""
    source: str
    """The ref ``attachments`` are parts of."""
    attachments: tuple[PartInfo, ...]
    """For a forward, the original's attachments, offered for keeping."""


@dataclass(frozen=True, slots=True, kw_only=True)
class DraftFields:
    """A draft as the user wrote it. Its addresses are checked here; the caps on the text and on
    the attachments too (``core.check_fields``, ``core.check_uploads``)."""

    account: str
    compose_id: str
    ref: str | None = None
    answers: str | None = None
    sender: str | None = None
    to: Sequence[Mailbox] = ()
    cc: Sequence[Mailbox] = ()
    bcc: Sequence[Mailbox] = ()
    subject: str = ""
    text: str = ""
    keep: Sequence[tuple[str, str]] = ()
    """``(ref, part)`` of the existing parts it keeps, its previous version's included."""


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
    account_id: str,
    work: Callable[[MailConnections], T],
    *,
    on: Sequence[str] | None = None,
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
    mode: Literal["reply", "reply_all", "forward"],
    *,
    via: Via = "web",
) -> Prefill:
    """What answering or forwarding the message ``ref`` names starts from; writes nothing. A
    recipient no send could reach is left out: it would refuse every save of the draft."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    account = accounts.config(message.account)
    _writable(account)
    original = _call(
        ctx,
        user_id,
        accounts,
        "mail_prefilled",
        via,
        message.account,
        lambda connections: core.original_message(connections, message),
    )
    with _as_refusals():
        if mode == "forward":
            return Prefill(
                account=account.id,
                sender=core.from_identity(account).address,
                to=(),
                cc=(),
                subject=_subject_cut(core.forward_subject(original.subject)),
                text=f"\n\n{core.forwarded_original(original, TIME_ZONE)}",
                answers=None,
                source=ref,
                attachments=original.attachments,
            )
        answered = core.reply_recipients(original, account, reply_all=mode == "reply_all")
        return Prefill(
            account=account.id,
            sender=core.from_identity(account, None, original.addressed).address,
            to=tuple(filter(_can_be_sent_to, answered.to)),
            cc=tuple(filter(_can_be_sent_to, answered.cc)),
            subject=_subject_cut(core.reply_subject(original.subject)),
            text=f"\n\n{core.quoted_original(original, TIME_ZONE)}",
            answers=ref,
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
) -> Draft | None:
    """The version a save replaces. Its ``compose_id`` is the send key, so a new one would forget
    how the draft's send went and deliver it again. A draft written elsewhere has none: the
    client gives it one, which ``_not_held`` allows only while no send stands under its ref. That
    send is why such a draft is normally gone, so a ref that holds nothing is asked about it too."""
    if replaces is None:
        return None
    try:
        previous = core.read_draft(connections, replaces)
    except MailError as error:
        if error.code in DRAFT_GONE:
            _not_held(ctx, user_id, replaces)
        raise
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
    named = [fields.ref, fields.answers, *(ref for ref, _part in fields.keep)]
    used = _ref_accounts(ctx, user_id, accounts, [ref for ref in named if ref is not None])
    account = accounts.config(fields.account)
    limits = accounts.write_limits
    _writable(account)
    with _as_refusals():
        core.check_fields(
            to=fields.to, cc=fields.cc, bcc=fields.bcc, subject=fields.subject, text=fields.text
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
        attachments=tuple(uploads),
        answers=fields.answers,
    )
    replaces = None if fields.ref is None else core.parse_ref(fields.ref)
    for named_ref in (fields.ref, fields.answers):
        if named_ref is not None and core.parse_ref(named_ref).account != account.id:
            elsewhere = "That message belongs to another account."
            raise mail_refusal("invalid_argument", elsewhere)
    keep = _attachment_sources(fields.keep)
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

    return _stoppable_call(
        ctx,
        user_id,
        accounts,
        "mail_draft_saved",
        origin.via,
        account.id,
        saved,
        on=sorted({account.id, *used}),
    )


def _ref_key(ref: MessageRef) -> str:
    return f"ref-{hashlib.sha256(core.format_ref(ref).encode()).hexdigest()}"


def _send_key(draft: Draft) -> str:
    """What guards a draft's send against a second delivery: its account and ``compose_id``, since
    a key is claimed per user and a copy between accounts keeps the id. A draft written elsewhere
    has none, and is one message, so its ref says which send it is."""
    if draft.compose_id is not None:
        return f"{draft.ref.account}/{draft.compose_id}"
    return _ref_key(draft.ref)


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
    ctx: AppContext, user_id: int, account_id: str, compose_id: str, *, via: Via = "web"
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
    any other."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    _writable(accounts.config(message.account))
    removed = AuditTrail(ctx.session_maker, user_id, origin, "draft_remove")
    return _call(
        ctx,
        user_id,
        accounts,
        "mail_draft_deleted",
        origin.via,
        message.account,
        lambda connections: core.remove_draft(connections, message, removed.note),
    )


def _attachment_sources(refs: Iterable[tuple[str, str]]) -> list[AttachmentSource]:
    return [core.AttachmentSource(core.parse_ref(ref), part) for ref, part in refs]
