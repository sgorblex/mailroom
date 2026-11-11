"""The one gate every send goes through (`src/send/gate.ts`): send mode, recipient checks, the
hourly limit, SMTP, the copy in Sent, and an audit item for the outcome, however it ends.

A host that answers before the message has left takes the phases apart: `admit` under its
request's deadline, then `deliver` and `file_sent` on a worker. Only `admit` makes the `Admitted`
that `deliver` needs, and each is good for one delivery, so a split cannot go round the gate or
send twice. `send_through_gate` runs them in a row.

`send_mode` limits agents: a person's click sends unless the mode is `off`. The hourly limit and
the recipient cap bind both, and `claim_send` writes the attempt down before SMTP.

Nothing after the message was handed over can make it a failure: filing it, removing its draft
and marking what it answers end in `filed = False` or a note. `deliver` writes the send's row, and
what followed goes in once, when it is all over: by `send_through_gate`, or by the host. A step a
host's deadline left running behind it then has nothing to overwrite.
"""

import logging
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, NoReturn, SupportsIndex

from mailroom_core.mail.audit import AuditItem, audit_result, outgoing_item
from mailroom_core.mail.compose import (
    DRAFT_HEADER,
    Composed,
    Envelope,
    MessageInput,
    bare_cr_in_headers,
    compose,
    crlf_lines,
    field_count,
    new_message_id,
    without_headers,
)
from mailroom_core.mail.drafts import (
    DRAFT_FLAGS,
    MAX_DRAFT_BYTES,
    append_message,
    draft_source,
    read_draft,
    try_remove_draft,
)
from mailroom_core.mail.errors import MailError, mail_error
from mailroom_core.mail.identity import from_identity
from mailroom_core.mail.outgoing import attachment_cap, check_sendable
from mailroom_core.mail.recipients import check_recipient_count, deliverable_address, matches_rule
from mailroom_core.mail.reply import mark_answered
from mailroom_core.render import format_date
from mailroom_core.send.smtp import Transfer, watched
from mailroom_core.store import SEND_CEILING, SendLimitReached, SendsBusy

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mailroom_core.config import AccountConfig, Limits
    from mailroom_core.mail.audit import Authoriser
    from mailroom_core.mail.drafts import Draft, Note
    from mailroom_core.mail.imap import MailConnections
    from mailroom_core.mail.recipients import Mailbox
    from mailroom_core.mail.ref import MessageRef
    from mailroom_core.send.smtp import Sender
    from mailroom_core.store import FinishedSendState, Json, SendLog, SendRecord

_log = logging.getLogger("mailroom_core.send")

_HOUR: Final = timedelta(hours=1)
_SENT_FLAGS: Final = ("\\Seen",)
_SEAL: Final = object()

type SendNoteCode = Literal["draft_not_removed", "draft_kept", "original_not_marked"]


@dataclass(frozen=True, slots=True)
class SendNote:
    """Something the sender should know about what followed a delivery: `text` in English, and
    `code`, which stays the same, for a host that words it itself."""

    code: SendNoteCode
    text: str


DRAFT_NOT_REMOVED: Final = SendNote(
    "draft_not_removed", "The draft could not be removed from Drafts."
)
DRAFT_KEPT: Final = SendNote("draft_kept", "The draft was kept as the only copy of the message.")
ORIGINAL_NOT_MARKED: Final = SendNote(
    "original_not_marked", "The original could not be marked as answered."
)


@dataclass(frozen=True, slots=True, kw_only=True)
class GateDeps:
    """What the gate works with: mail connections, the SMTP sender, the send log and the limits."""

    connections: MailConnections
    smtp: Sender
    send_log: SendLog
    limits: Limits
    time_zone: str = "UTC"


@dataclass(frozen=True, slots=True)
class Prepared:
    """A message already built, sent as it is: a stored draft."""

    composed: Composed
    attachments: int


class Admitted:
    """Proof that a message passed `admit`, and all `deliver` needs. Only `admit` can make one,
    and it is good for one delivery. `send` is the attempt `claim_send` wrote down."""

    __slots__ = ("_lock", "_spent", "account", "composed", "item", "send")

    def __init__(
        self,
        seal: object,
        account: AccountConfig,
        composed: Composed,
        send: SendRecord,
        item: AuditItem,
    ) -> None:
        """Make an `Admitted`; raises `TypeError` unless `seal` is the one only `admit` holds."""
        if seal is not _SEAL:
            raise TypeError("only admit() makes an Admitted")
        self.account: Final = account
        self.composed: Final = composed
        self.send: Final = send
        self.item: Final = item
        self._lock: Final = threading.Lock()
        self._spent = False

    def spend(self) -> None:
        """Use up the one delivery, raising `ValueError` if it was already used."""
        with self._lock:
            if self._spent:
                raise ValueError("this Admitted has been delivered already")
            self._spent = True

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("an Admitted cannot be copied: it is good for one delivery")


@dataclass(frozen=True, slots=True)
class Drafted:
    """The account's send mode kept it as a draft: nothing left the account."""

    ref: MessageRef | None
    """None if it could not be read back; it is in Drafts all the same."""


@dataclass(frozen=True, slots=True)
class Earlier:
    """A retry of a key that must not be delivered again: `send.state` is `sent` (its `result`
    says how that went) or `sending` (the same id is still at it)."""

    send: SendRecord


@dataclass(frozen=True, slots=True)
class SentMessage:
    """What `deliver` returns: the server took it."""

    admitted: Admitted
    rejected: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class GateResult:
    """How a send ended for the host: sent, still sending, or kept as a draft."""

    outcome: Literal["sent", "sending", "drafted"]
    """`sending`: an earlier attempt with this key is still running."""
    ref: MessageRef | None = None
    """The draft's, when drafted."""
    send_id: int | None = None
    filed: bool | None = None
    """For a send: whether the copy reached Sent; None while that is not known."""
    rejected: tuple[str, ...] = ()
    """For a send: recipients the server refused while taking the others."""


def sends_now(account: AccountConfig, message: MessageInput, by: Authoriser) -> bool:
    """Whether the send mode lets this go out now. For a user anything but `off`. For an agent
    `direct`, and `approval` when every recipient is on its auto_approve list (until the outbox,
    the rest are drafted); `drafts` and `off` never."""
    if account.send_mode == "off":
        return False
    if by == "user" or account.send_mode == "direct":
        return True
    if account.send_mode != "approval":
        return False
    return all(matches_rule(m.address, account.auto_approve) for m in _recipients_of(message))


def _recipients_of(message: MessageInput) -> tuple[Mailbox, ...]:
    return (*message.to, *message.cc, *message.bcc)


def _checked(mailboxes: Iterable[Mailbox]) -> tuple[Mailbox, ...]:
    return tuple(replace(m, address=deliverable_address(m.address)) for m in mailboxes)


def _kept_as_draft(account: AccountConfig) -> MailError:
    # A prepared message is a draft already: drafting it again would only make a copy.
    return MailError(
        "invalid_argument",
        "Not every recipient is auto-approved for this account, so it would only be saved as a "
        "draft again. The draft stays."
        if account.send_mode == "approval"
        else f"{account.id}'s send mode is {account.send_mode}: drafts are not sent from here. "
        "The draft stays.",
    )


def _limit_reached(deps: GateDeps, error: SendLimitReached) -> MailError:
    oldest = error.recent.oldest or datetime.now(UTC)
    return MailError(
        "rate_limited",
        f"The limit is {deps.limits.send_per_hour} per hour; the next send is possible at "
        f"{format_date(oldest + _HOUR, deps.time_zone)}.",
    )


def admit(
    deps: GateDeps,
    account: AccountConfig,
    message: MessageInput,
    note: Note,
    *,
    by: Authoriser,
    prepared: Prepared | None = None,
    key: str | None = None,
    confirm_resend: bool = False,
) -> Admitted | Drafted | Earlier:
    """Decides what becomes of `message`, and audits it unless it comes back `Admitted`, when
    `deliver` and `file_sent` do.

    `by` says who authorised it. `prepared` is the message as stored, for a draft (`prepare_draft`),
    which an agent is refused rather than drafted again; its envelope is rebuilt here from
    `message`, the one that was checked. `key` guards a retry (`claim_send`). `confirm_resend`
    goes ahead after `delivery_unknown`, on a person's word.

    Raises a `MailError`: `send_disabled`, `invalid_argument`, `rate_limited`, `busy`, or
    `delivery_unknown` for a key whose earlier attempt may have gone out.
    """
    item = replace(outgoing_item(account.id, message, "ok"), mode=account.send_mode, by=by)
    if prepared is not None:
        item = replace(item, attachments=prepared.attachments)

    def refuse(error: MailError) -> MailError:
        note(replace(item, delivery="refused", result=error.code))
        return error

    composed: Composed | None = None
    try:
        check_sendable(account)
        # Addresses from an original or a draft come from IMAP, not from recipient_list: all are
        # checked here, so auto_approve never matches something like `x@evil.org@team.example`.
        checked = replace(
            message, to=_checked(message.to), cc=_checked(message.cc), bcc=_checked(message.bcc)
        )
        recipients = _recipients_of(checked)
        check_recipient_count(recipients, deps.limits.max_recipients)
        goes_out = sends_now(account, checked, by)
        if prepared is not None and not goes_out:
            raise _kept_as_draft(account)
        if goes_out:
            composed = prepared.composed if prepared else compose(replace(checked, draft=False))
    except MailError as error:
        raise refuse(error) from None
    if composed is None:
        return _draft(deps, account, checked, item, note)
    envelope = Envelope(message.sender.address, tuple(m.address for m in recipients))
    composed = replace(composed, envelope=envelope)
    item = replace(item, message_id=composed.message_id)

    try:
        claim = deps.send_log.claim_send(
            key, account.id, deps.limits.send_per_hour, confirm_resend=confirm_resend
        )
    except SendLimitReached as error:
        raise refuse(_limit_reached(deps, error)) from None
    except SendsBusy:
        raise refuse(MailError("busy")) from None
    if claim.fresh:
        return Admitted(_SEAL, account, composed, claim.send, item)
    if claim.send.state == "unknown":
        raise refuse(MailError("delivery_unknown"))
    note(replace(item, delivery="repeated"))
    return Earlier(claim.send)


def _draft(
    deps: GateDeps, account: AccountConfig, message: MessageInput, item: AuditItem, note: Note
) -> Drafted:
    item = replace(item, delivery="drafted")
    try:
        composed = compose(replace(message, draft=True))
        item = replace(item, message_id=composed.message_id)
        folder = deps.connections.special_folder(account.id, "drafts").path
        ref = append_message(
            deps.connections, account.id, folder, composed.stored, DRAFT_FLAGS, composed.message_id
        )
    except MailError as error:
        note(replace(item, result=error.code))
        raise
    note(replace(item, folder=folder))
    return Drafted(ref)


def _finish(deps: GateDeps, send: SendRecord, state: FinishedSendState, result: Json) -> None:
    """Records an outcome, whatever becomes of that: a row left `sending` reads as `unknown` past
    the ceiling, which is the safe way for it to be wrong, and the send's own outcome stands."""
    try:
        deps.send_log.finish_send(send, state, result)
    except Exception as error:
        _log.warning(
            "send_not_recorded",
            extra={
                "event": "send_not_recorded",
                "send_id": send.id,
                "state": state,
                "error_type": type(error).__name__,
            },
        )


def deliver(
    deps: GateDeps, admitted: Admitted, note: Note, transfer: Transfer | None = None
) -> SentMessage:
    """Hands an admitted message to SMTP and records how that went. On a `MailError` the attempt
    ends `unknown` when it is `delivery_unknown`, else `failed`, and is audited; any other
    exception counts as `unknown`, since nothing says the message did not leave. After a return,
    `file_sent` audits it."""
    admitted.spend()
    send = admitted.send
    composed = admitted.composed
    remaining = (send.at + SEND_CEILING - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        # Queued so long that the attempt reads as `unknown`, and could be confirmed and resent.
        error = MailError("timeout")
        _finish(deps, send, "failed", {"code": error.code})
        note(replace(admitted.item, delivery="attempted", result=error.code))
        raise error
    running = Transfer() if transfer is None else transfer
    try:
        # The same goes for an attempt still running then, so its time starts at the claim: the
        # sender's own watchdog only starts here, however long the attempt was queued.
        with watched(running, remaining):
            delivery = deps.smtp.send(
                admitted.account.id, composed.envelope, composed.wire, running
            )
    except MailError as error:
        state: FinishedSendState = "unknown" if error.code == "delivery_unknown" else "failed"
        _finish(deps, send, state, {"code": error.code})
        note(replace(admitted.item, delivery="attempted", result=error.code))
        raise
    except Exception:
        _finish(deps, send, "unknown", {"code": "delivery_unknown"})
        note(replace(admitted.item, delivery="attempted", result="internal"))
        raise
    _finish(deps, send, "sent", {"rejected": list(delivery.rejected)})
    return SentMessage(admitted, delivery.rejected)


def file_sent(deps: GateDeps, sent: SentMessage, note: Note) -> bool:
    """Files the delivered message in Sent, except where the server does that itself (Gmail, by
    capability), and audits the send. Returns whether the copy is there: failing here, at any
    step, never undoes the delivery. It leaves the send's row alone: a host that stopped waiting
    for it may have written the final result by now."""
    admitted = sent.admitted
    account = admitted.account.id
    composed = admitted.composed
    filed = True
    try:
        if not deps.connections.capabilities(account).gmail:
            folder = deps.connections.special_folder(account, "sent").path
            append_message(
                deps.connections,
                account,
                folder,
                composed.stored,
                _SENT_FLAGS,
                composed.message_id,
            )
    except Exception as error:
        mail_error(error, account)  # logged
        filed = False
    note(
        replace(
            admitted.item,
            delivery="sent",
            filed=filed,
            rejected=sent.rejected or None,
            result="ok",
        )
    )
    return filed


def _replayed(send: SendRecord) -> GateResult:
    if send.state == "sending":
        return GateResult(outcome="sending", send_id=send.id)
    result = send.result if isinstance(send.result, dict) else {}
    filed = result.get("filed")
    rejected = result.get("rejected")
    return GateResult(
        outcome="sent",
        send_id=send.id,
        filed=filed if isinstance(filed, bool) else None,
        rejected=tuple(str(r) for r in rejected) if isinstance(rejected, list) else (),
    )


def send_through_gate(
    deps: GateDeps,
    account: AccountConfig,
    message: MessageInput,
    note: Note,
    *,
    by: Authoriser,
    prepared: Prepared | None = None,
    key: str | None = None,
    confirm_resend: bool = False,
) -> GateResult:
    """`admit`, `deliver` and `file_sent` in a row, for a host that waits for all of it."""
    admission = admit(
        deps,
        account,
        message,
        note,
        by=by,
        prepared=prepared,
        key=key,
        confirm_resend=confirm_resend,
    )
    if isinstance(admission, Drafted):
        return GateResult(outcome="drafted", ref=admission.ref)
    if isinstance(admission, Earlier):
        return _replayed(admission.send)
    sent = deliver(deps, admission, note)
    filed = file_sent(deps, sent, note)
    _finish(deps, admission.send, "sent", {"rejected": list(sent.rejected), "filed": filed})
    return GateResult(
        outcome="sent", send_id=admission.send.id, filed=filed, rejected=sent.rejected
    )


@dataclass(frozen=True, slots=True)
class DraftToSend:
    """A stored draft as `admit` takes it."""

    draft: Draft
    message: MessageInput
    """Who it goes to, for the gate's checks; its text is not sent, `prepared` is."""
    prepared: Prepared


_UNSENDABLE_HEADERS: Final = (
    "This draft's headers can't be sent as they are; saving it again fixes them."
)


def _source_cap(limits: Limits) -> int:
    """How big a stored draft may be: its attachments up to their cap, which base64 and its line
    breaks grow by 37%, and its text."""
    return attachment_cap(limits) * 3 // 2 + MAX_DRAFT_BYTES


def prepare_draft(
    connections: MailConnections, account: AccountConfig, ref: MessageRef, limits: Limits
) -> DraftToSend:
    """Reads a draft to send it as it is stored (HTML, attachments and Message-ID included): the
    wire copy loses its Bcc, and both lose mailroom's marker. Its From must still be one of the
    account's identities. A draft with no Message-ID, not written here, gets one, so what is filed
    can be found and audited. `too_large` rather than a cut message.

    A bare CR in its headers is refused before anything is read out of them: ENVELOPE reads one as
    data and the wire as a line break, so a From, Message-ID or Cc behind it would be checked in one
    reading and go out in the other. Only another client's draft can have one. So is anything but
    one address in one From field, because only that address is checked and the field goes out as
    stored. Line endings are fixed before anything is stripped, so that the strip and the wire agree
    on what a line is."""
    if ref.account != account.id:
        raise MailError("invalid_argument", "That draft belongs to another account.")
    source = draft_source(connections, ref, _source_cap(limits))
    if bare_cr_in_headers(source):
        raise MailError("invalid_argument", _UNSENDABLE_HEADERS)
    draft = read_draft(connections, ref)
    from_fields = field_count(source, "From")
    if from_fields > 1 or len(draft.sender) != from_fields:
        raise MailError("invalid_argument", _UNSENDABLE_HEADERS)
    sender = from_identity(account, draft.sender[0].address if draft.sender else None)
    stored = without_headers(crlf_lines(source), [DRAFT_HEADER])
    message_id = draft.message_id
    if message_id is None:
        message_id = new_message_id(sender.address)
        stored = f"Message-ID: {message_id}\r\n".encode("ascii") + stored
    recipients = (*draft.to, *draft.cc, *draft.bcc)
    return DraftToSend(
        draft=draft,
        message=MessageInput(
            sender=sender,
            to=draft.to,
            cc=draft.cc,
            bcc=draft.bcc,
            subject=draft.subject,
            text="",
        ),
        prepared=Prepared(
            Composed(
                stored=stored,
                wire=without_headers(stored, ["Bcc"]),
                message_id=message_id,
                envelope=Envelope(sender.address, tuple(m.address for m in recipients)),
            ),
            len(draft.attachments),
        ),
    )


def mark_original(connections: MailConnections, ref: MessageRef, note: Note) -> SendNote | None:
    """Sets \\Answered on what a sent reply answers. A failure is audited and comes back as a
    note for the sender: the reply has gone."""
    try:
        mark_answered(connections, ref)
    except Exception as error:
        note(
            AuditItem(
                account=ref.account,
                folder=ref.folder,
                uid=ref.uid,
                flag="\\Answered",
                result=audit_result(error),
            )
        )
        return ORIGINAL_NOT_MARKED
    return None


def settle_draft(
    connections: MailConnections, draft: Draft, note: Note, *, filed: bool
) -> tuple[SendNote, ...]:
    """Once a draft has been delivered: marks what it answers, and removes it if `filed`, what
    `file_sent` answered. Otherwise it stays: it is the only copy in the account, and the send's
    key keeps a retry from delivering it again. What the sender should know comes back as notes,
    never as an error."""
    notes: list[SendNote] = []
    if draft.answers is not None:
        marked = mark_original(connections, draft.answers, note)
        if marked is not None:
            notes.append(marked)
    if not filed:
        notes.append(DRAFT_KEPT)
    elif try_remove_draft(connections, draft.ref, note) == "failed":
        notes.append(DRAFT_NOT_REMOVED)
    return tuple(notes)
