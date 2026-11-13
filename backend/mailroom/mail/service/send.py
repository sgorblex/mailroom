"""Sends: a draft's, admitted in the request and delivered on a ``SendRunner`` thread, and an
agent's direct send, reply and forward (``Dispatched``).

A send has two halves. ``send_draft`` admits it under the pool's deadline, in the request; delivery
runs on a ``SendRunner`` thread, past the end of the request, and writes its outcome to the send's
row. From the moment the server has taken the message nothing can make that a failure: filing it,
removing its draft and marking what it answered are later calls, each of which at worst sets
``filed`` to false or adds a note. A draft that did not reach Sent stays where it is: it is the only
copy the account has.

An agent's direct send (``send_message``, ``send_reply``, ``send_forward``) goes the same way, by
``agent``, without a key: its Message-ID is fresh, so a repeat is a second message. Where the send
mode only drafts it, the gate saves a draft and the answer says so (``Dispatched``).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Final, cast

from mailroom.accounts.store import AccountNotFound, account_password, load_accounts
from mailroom.audit import WEB, AuditTrail, Origin
from mailroom.errors import Refusal, mail_refusal
from mailroom.mail.service._shared import (
    _Accounts,
    _accounts,
    _call,
    _message_ref,
    _ref_accounts,
    _run,
    _Stoppable,
    _usable,
    unknown_account,
    unknown_message,
)
from mailroom.mail.service.compose import (
    DRAFT_GONE,
    TIME_ZONE,
    SendView,
    _as_refusals,
    _attachment_sources,
    _deliverable,
    _holding_send,
    _send_key,
    _send_view,
    _subject_cut,
    _writable,
)
from mailroom_core import mail as core
from mailroom_core import send as gate
from mailroom_core.mail import MESSAGES, MailError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mailroom.context import AppContext
    from mailroom.mail.sends import SendRunner
    from mailroom_core.config import AccountConfig
    from mailroom_core.mail import (
        Authoriser,
        Draft,
        Mailbox,
        MailConnections,
        MessageInput,
        MessageRef,
    )
    from mailroom_core.send import Transfer
    from mailroom_core.store import Json, SendState

logger = logging.getLogger(__name__)

NOT_FILED: Final = "Sent, but not filed in Sent."
_SEND_ID: Final = re.compile(r"[0-9]{1,18}")
"""What fits the column: anything else names no send."""


@dataclass(frozen=True, slots=True)
class _Outgoing:
    """What a send takes to ``gate.admit``, and what is left once the server has it."""

    message: MessageInput
    prepared: gate.Prepared | None
    key: str | None
    after: Callable[[MailConnections, bool], tuple[gate.SendNote, ...]]
    """Marking what it answered, removing its draft, given whether it was filed: the notes of
    what failed."""
    unsettled: Callable[[bool], tuple[gate.SendNote, ...]]
    """The notes for when ``after`` did not run at all, given the same."""


def _sending(ctx: AppContext, user_id: int, origin: Origin) -> AuditTrail:
    """A send's audit trail: admission, delivery, filing and settling are all ``send``."""
    return AuditTrail(ctx.session_maker, user_id, origin, "send")


def _runner(ctx: AppContext) -> SendRunner:
    if ctx.send_runner is None:
        msg = "The mail layer is off: nothing that sends mail is served."
        raise RuntimeError(msg)
    return ctx.send_runner


def _smtp(ctx: AppContext, user_id: int) -> gate.SmtpSender:
    store = ctx.store(user_id)
    subkey = ctx.subkey

    def account(account_id: str) -> AccountConfig | None:
        try:
            return load_accounts(store).account(account_id)
        except AccountNotFound:
            return None

    return gate.SmtpSender(
        account,
        lambda account_id: account_password(store, subkey, account_id),
        ssl_context=ctx.ssl_context,
    )


def _after_delivery[T](
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    event: str,
    account_id: str,
    work: Callable[[MailConnections], T],
    fallback: T,
) -> T:
    """One step after the server took a message, as its own call: cut at the deadline, refused
    by the gates, or broken, it comes back ``fallback``, which only ever words what did not
    happen. The message has gone whatever happens here."""
    stop = threading.Event()
    try:
        return _run(
            ctx,
            user_id,
            accounts,
            event,
            [account_id],
            lambda connections: work(cast("MailConnections", _Stoppable(connections, stop))),
            on_deadline=stop.set,
        )
    except Refusal as refusal:
        logger.warning(event, extra={"event": event, "user_id": user_id, "code": refusal.code})
        return fallback


def _run_send(
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    origin: Origin,
    deps: gate.GateDeps,
    admitted: gate.Admitted,
    outgoing: _Outgoing,
    transfer: Transfer,
) -> None:
    """A send's thread: delivers, and only then files and settles. ``gate.deliver`` writes down
    how the delivery went, so there is nothing to add when it fails."""
    started = time.monotonic()
    account_id = admitted.account.id
    trail = _sending(ctx, user_id, origin)

    def finished(
        state: SendState, outcome: str, *, filed: bool | None = None, rejected: int = 0
    ) -> None:
        logger.info(
            "mail_send_finished",
            extra={
                "event": "mail_send_finished",
                "user_id": user_id,
                "via": origin.via,
                "account_id": account_id,
                "send_id": admitted.send.id,
                "state": state,
                "outcome": outcome,
                "filed": filed,
                "recipient_count": len(admitted.composed.envelope.recipients),
                "rejected_count": rejected,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )

    try:
        sent = gate.deliver(deps, admitted, trail.note, transfer)
    except MailError as error:
        delivered: SendState = "unknown" if error.code == "delivery_unknown" else "failed"
        finished(delivered, error.code)
        return
    except Exception as error:
        logger.warning(
            "mail_send_broke",
            extra={"event": "mail_send_broke", "error_type": type(error).__name__},
        )
        finished("unknown", "delivery_unknown")
        return
    filed = _after_delivery(
        ctx,
        user_id,
        accounts,
        "mail_send_not_filed",
        account_id,
        lambda connections: gate.file_sent(
            replace(deps, connections=connections), sent, trail.note
        ),
        False,
    )
    notes = _after_delivery(
        ctx,
        user_id,
        accounts,
        "mail_send_not_settled",
        account_id,
        lambda connections: outgoing.after(connections, filed),
        outgoing.unsettled(filed),
    )
    texts = tuple(note.text for note in notes)
    result: Json = {
        "rejected": [*sent.rejected],
        "filed": filed,
        "notes": [*texts] if filed else [NOT_FILED, *texts],
        "note_codes": [note.code for note in notes],
    }
    try:
        deps.send_log.finish_send(admitted.send, "sent", result)
    except Exception as error:
        logger.warning(
            "mail_send_result_lost",
            extra={"event": "mail_send_result_lost", "error_type": type(error).__name__},
        )
    finished("sent", "ok", filed=filed, rejected=len(sent.rejected))


def _admit(
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    account: AccountConfig,
    origin: Origin,
    outgoing: Callable[[MailConnections], _Outgoing],
    *,
    by: Authoriser,
    confirm_resend: bool,
    on: Sequence[str] | None = None,
) -> SendView | gate.Drafted:
    """Takes a send to the gate under the pool's deadline, and on to the runner: a place first,
    so that ``busy`` comes before anything is claimed. The running send is started inside the
    pool's call, so an admission that outlives its caller's deadline cannot leave a claimed send
    with nobody to run it. That admission is not stopped either: past the deadline it can still
    claim and start the send, so a ``timeout`` refusal does not mean nothing went out. An own
    draft's key guards a retry only while the draft exists, and the send removes the draft, so a
    caller must treat that refusal as not confirmed."""
    with _as_refusals():
        slot = _runner(ctx).reserve(user_id)
    stop = threading.Event()
    limits = accounts.write_limits

    def admission(connections: MailConnections) -> SendView | gate.Drafted:
        guarded = cast("MailConnections", _Stoppable(connections, stop))
        request = outgoing(guarded)
        if stop.is_set():
            raise MailError("timeout")
        deps = gate.GateDeps(
            connections=guarded,
            smtp=_smtp(ctx, user_id),
            send_log=ctx.store(user_id),
            limits=limits,
            time_zone=TIME_ZONE,
        )
        admitted = gate.admit(
            deps,
            account,
            request.message,
            _sending(ctx, user_id, origin).note,
            by=by,
            prepared=request.prepared,
            key=request.key,
            confirm_resend=confirm_resend,
        )
        if isinstance(admitted, gate.Admitted):
            slot.start(partial(_run_send, ctx, user_id, accounts, origin, deps, admitted, request))
            return _send_view(admitted.send)
        if isinstance(admitted, gate.Earlier):
            return _send_view(admitted.send)
        return admitted

    try:
        return _call(
            ctx,
            user_id,
            accounts,
            "mail_send_requested",
            origin.via,
            account.id,
            admission,
            on=on,
            on_deadline=stop.set,
        )
    finally:
        slot.release()


def _draft_unsettled(draft: Draft, filed: bool) -> tuple[gate.SendNote, ...]:
    """What ``gate.settle_draft`` would have reported had none of it worked."""
    unmarked = () if draft.answers is None else (gate.ORIGINAL_NOT_MARKED,)
    return (*unmarked, gate.DRAFT_NOT_REMOVED if filed else gate.DRAFT_KEPT)


def send_holds(ctx: AppContext, user_id: int, ref: str) -> bool:
    """Whether a send holds the draft written elsewhere at ``ref``, there or not by now."""
    return _holding_send(ctx, user_id, core.parse_ref(ref)) is not None


def _send_of_gone_draft(
    ctx: AppContext, user_id: int, ref: MessageRef, *, confirm_resend: bool
) -> SendView | None:
    """What a retry for a draft that is gone reaches: the send of one written elsewhere, whose key
    needs only its ref. None when that leaves nothing to answer, ``confirm_resend`` included: the
    user has said to send again, and there is no draft to send."""
    send = _holding_send(ctx, user_id, ref)
    if send is None or (send.state == "unknown" and confirm_resend):
        return None
    if send.state == "unknown":
        raise mail_refusal("delivery_unknown", MESSAGES["delivery_unknown"])
    return _send_view(send)


def send_draft(
    ctx: AppContext,
    user_id: int,
    ref: str,
    *,
    by: Authoriser,
    confirm_resend: bool = False,
    origin: Origin = WEB,
) -> SendView:
    """Sends the draft ``ref`` as it is stored, and answers once it is admitted: ``sending``
    (follow it with ``read_send``), or ``sent`` when this draft was delivered before. Its key is
    claimed once, so asking twice delivers once. ``delivery_unknown`` when an earlier attempt
    may have gone out, unless ``confirm_resend`` says the user knows; ``by`` an agent, a send
    mode that would only draft it again is refused (``gate.admit``)."""
    accounts = _accounts(ctx, user_id)
    message = _message_ref(ctx, user_id, accounts, ref)
    account = accounts.config(message.account)
    _writable(account)

    def stored(connections: MailConnections) -> _Outgoing:
        ready = gate.prepare_draft(connections, account, message, accounts.write_limits)
        _deliverable((*ready.message.to, *ready.message.cc, *ready.message.bcc))
        return _Outgoing(
            ready.message,
            ready.prepared,
            _send_key(ready.draft),
            lambda settling, filed: gate.settle_draft(
                settling, ready.draft, _sending(ctx, user_id, origin).note, filed=filed
            ),
            partial(_draft_unsettled, ready.draft),
        )

    try:
        started = _admit(
            ctx, user_id, accounts, account, origin, stored, by=by, confirm_resend=confirm_resend
        )
    except Refusal as refusal:
        if refusal.code not in DRAFT_GONE:
            raise
        earlier = _send_of_gone_draft(ctx, user_id, message, confirm_resend=confirm_resend)
        if earlier is None:
            raise
        return earlier
    if isinstance(started, gate.Drafted):  # a stored draft is refused, not drafted again
        raise mail_refusal("invalid_argument", MESSAGES["invalid_argument"])
    return started


def read_send(ctx: AppContext, user_id: int, send_id: str) -> SendView:
    """The user's send ``send_id``. Another user's reads as one that does not exist."""
    send = None
    if _SEND_ID.fullmatch(send_id):
        send = ctx.store(user_id).send_state(int(send_id))
    if send is None:
        raise unknown_message()
    return _send_view(send)


@dataclass(frozen=True, slots=True, kw_only=True)
class Dispatched:
    """What an agent's direct send came to: ``send`` (follow it with ``read_send``) when the gate
    let it go, else it was saved as a draft (``draft``, None when it could not be read back)."""

    account: str
    send_mode: str
    message: MessageInput
    """As it goes out or was drafted, from and every recipient included."""
    send: SendView | None = None
    draft: MessageRef | None = None


def _dispatch(
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    account: AccountConfig,
    origin: Origin,
    build: Callable[[MailConnections], _Outgoing],
    *,
    on: Sequence[str],
) -> Dispatched:
    """An agent's message to the gate. It has no key and a fresh Message-ID, so a repeat is a
    second message: a retry is the caller's to avoid. Where the send mode only drafts it, the
    gate saves it in Drafts."""
    built: list[MessageInput] = []

    def outgoing(connections: MailConnections) -> _Outgoing:
        request = build(connections)
        built.append(request.message)
        return request

    started = _admit(
        ctx, user_id, accounts, account, origin, outgoing, by="agent", confirm_resend=False, on=on
    )
    [message] = built
    if isinstance(started, gate.Drafted):
        return Dispatched(
            account=account.id, send_mode=account.send_mode, message=message, draft=started.ref
        )
    return Dispatched(
        account=account.id, send_mode=account.send_mode, message=message, send=started
    )


def _written(
    ctx: AppContext,
    user_id: int,
    accounts: _Accounts,
    account_id: str,
    refs: Sequence[str],
) -> tuple[AccountConfig, list[str]]:
    """The account that writes, once it may (not ``off``) and every ref is the user's."""
    if not accounts.has(account_id):
        raise unknown_account()
    _usable(ctx, user_id, accounts, account_id)
    used = _ref_accounts(ctx, user_id, accounts, refs)
    account = accounts.config(account_id)
    _writable(account)
    return account, sorted({account_id, *used})


def _plain(
    request: Callable[[MailConnections], MessageInput],
) -> Callable[[MailConnections], _Outgoing]:
    """``request`` as a message with nothing to do once it is delivered."""

    def build(connections: MailConnections) -> _Outgoing:
        return _Outgoing(
            request(connections), None, None, lambda _connections, _filed: (), lambda _filed: ()
        )

    return build


def send_message(
    ctx: AppContext,
    user_id: int,
    account_id: str,
    *,
    sender: str | None = None,
    to: Sequence[Mailbox] = (),
    cc: Sequence[Mailbox] = (),
    bcc: Sequence[Mailbox] = (),
    subject: str,
    text: str,
    attachments: Sequence[tuple[str, str]] = (),
    origin: Origin,
) -> Dispatched:
    """A new message from an agent, as far as the account's send mode lets it. ``attachments``
    are ``(ref, part)`` of existing messages."""
    accounts = _accounts(ctx, user_id)
    account, used = _written(ctx, user_id, accounts, account_id, [r for r, _ in attachments])
    with _as_refusals():
        core.check_fields(to=to, cc=cc, bcc=bcc, subject=subject, text=text)
    recipients = _deliverable(to), _deliverable(cc), _deliverable(bcc)
    limits = accounts.write_limits
    sources = _attachment_sources(attachments)
    with _as_refusals():
        from_ = core.from_identity(account, sender)

    def message(connections: MailConnections) -> MessageInput:
        files = core.attachments_from(connections, sources, core.attachment_cap(limits))
        return core.MessageInput(
            sender=from_,
            to=recipients[0],
            cc=recipients[1],
            bcc=recipients[2],
            subject=subject,
            text=text,
            attachments=files,
        )

    return _dispatch(ctx, user_id, accounts, account, origin, _plain(message), on=used)


def send_reply(
    ctx: AppContext,
    user_id: int,
    ref: str,
    *,
    text: str,
    reply_all: bool = False,
    sender: str | None = None,
    cc: Sequence[Mailbox] = (),
    bcc: Sequence[Mailbox] = (),
    attachments: Sequence[tuple[str, str]] = (),
    quote: bool = True,
    origin: Origin,
) -> Dispatched:
    """A reply from an agent: threaded, to the original's Reply-To or sender (everyone with
    ``reply_all``) and ``cc``, from the identity it was sent to, quoting it. It marks the
    original ``\\Answered`` once delivered."""
    accounts = _accounts(ctx, user_id)
    original_ref = _message_ref(ctx, user_id, accounts, ref)
    account, used = _written(
        ctx, user_id, accounts, original_ref.account, [ref, *(r for r, _ in attachments)]
    )
    with _as_refusals():
        core.check_fields(cc=cc, bcc=bcc, text=text)
    extra_cc, extra_bcc = _deliverable(cc), _deliverable(bcc)
    limits = accounts.write_limits
    sources = _attachment_sources(attachments)

    def build(connections: MailConnections) -> _Outgoing:
        original = core.original_message(connections, original_ref)
        answered = core.reply_recipients(original, account, reply_all=reply_all)
        thread = core.threading(original)
        files = core.attachments_from(connections, sources, core.attachment_cap(limits))
        message = core.MessageInput(
            sender=core.from_identity(account, sender, original.addressed),
            to=_deliverable(answered.to),
            cc=_deliverable((*answered.cc, *extra_cc)),
            bcc=extra_bcc,
            subject=_subject_cut(core.reply_subject(original.subject)),
            text=f"{text}\n\n{core.quoted_original(original, TIME_ZONE)}" if quote else text,
            answers=ref,
            in_reply_to=thread.in_reply_to,
            references=thread.references,
            attachments=files,
        )

        def mark(settling: MailConnections, _filed: bool) -> tuple[gate.SendNote, ...]:
            note = gate.mark_original(settling, original_ref, _sending(ctx, user_id, origin).note)
            return () if note is None else (note,)

        return _Outgoing(message, None, None, mark, lambda _filed: (gate.ORIGINAL_NOT_MARKED,))

    return _dispatch(ctx, user_id, accounts, account, origin, build, on=used)


def send_forward(
    ctx: AppContext,
    user_id: int,
    ref: str,
    *,
    to: Sequence[Mailbox],
    cc: Sequence[Mailbox] = (),
    bcc: Sequence[Mailbox] = (),
    text: str | None = None,
    sender: str | None = None,
    include_attachments: bool = True,
    origin: Origin,
) -> Dispatched:
    """A forward from an agent: the original inline, its attachments too by default, an optional
    note above it."""
    accounts = _accounts(ctx, user_id)
    original_ref = _message_ref(ctx, user_id, accounts, ref)
    account, used = _written(ctx, user_id, accounts, original_ref.account, [ref])
    with _as_refusals():
        core.check_fields(to=to, cc=cc, bcc=bcc, text=text or "")
    recipients = _deliverable(to), _deliverable(cc), _deliverable(bcc)
    limits = accounts.write_limits

    def message(connections: MailConnections) -> MessageInput:
        original = core.original_message(connections, original_ref)
        forwarded = core.forwarded_original(original, TIME_ZONE)
        sources = (
            [core.AttachmentSource(original_ref, part.part) for part in original.attachments]
            if include_attachments
            else []
        )
        return core.MessageInput(
            sender=core.from_identity(account, sender, original.addressed),
            to=recipients[0],
            cc=recipients[1],
            bcc=recipients[2],
            subject=_subject_cut(core.forward_subject(original.subject)),
            text=forwarded if text is None else f"{text}\n\n{forwarded}",
            attachments=core.attachments_from(connections, sources, core.attachment_cap(limits)),
        )

    return _dispatch(ctx, user_id, accounts, account, origin, _plain(message), on=used)
