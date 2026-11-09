"""Sending mail: ``message_send``, ``message_reply``, ``message_forward`` and ``draft_send``, over
``mailroom.mail.service`` and the same send gate as the web client, with the agent as the one who
authorised it. The account's send mode limits what an agent may do (``off`` refuses, ``drafts``
saves a draft, ``approval`` sends only if every recipient is auto-approved and otherwise saves the
whole message as a draft, ``direct`` sends), the hourly limit and the recipient cap bind every send,
and the answer says plainly which happened and to whom.

**After admission (30 s) the tool waits up to ``SEND_WAIT`` seconds for the delivery.** The delivery
runs past the request, so the tool follows its row until it is settled; ``draft_send`` first reads
the draft, under a 30 s deadline of its own. A send still going then, one whose outcome cannot be
known or one that timed out on its way in answers "Not confirmed; do not resend": the message may
have been delivered, a direct send has a fresh Message-ID each time, and an own draft's key is gone
with the draft, so nothing here could recognise a repeat. A sent message's answer says it must not
be sent again.

Every address a result names is inside the untrusted-content envelope, the recipients the server
refused too; the instructions around them are ours.
"""

from __future__ import annotations

import logging
import time
from functools import partial
from typing import TYPE_CHECKING, Any, Final, cast

import anyio
from pydantic import Field, StrictBool

from mailroom.errors import Refusal, mail_refusal
from mailroom.mail import service
from mailroom.mcp.tool import Arguments, Hints, Tool, text_result
from mailroom.mcp.tools.mail_read import (
    RefText,
    named_account,
)
from mailroom.mcp.tools.outgoing import (
    SENDER,
    AccountId,
    Addresses,
    Attachments,
    Sender,
    Subject,
    Text,
    keep_of,
    recipients,
    who,
)
from mailroom_core.mail import MESSAGES, format_ref
from mailroom_core.render import untrusted

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp import types

    from mailroom.mail.service import Dispatched, SendView
    from mailroom.mcp.tool import ToolCall
    from mailroom_core.mail import MailErrorCode

logger = logging.getLogger(__name__)

SEND: Final = Hints(destructive=True, open_world=True)
"""Mail that has left cannot be taken back."""
SEND_WAIT: Final = 45.0
"""How long a send tool follows its delivery, in seconds."""
_POLL: Final = (0.05, 1.0)
"""The first wait between looks at a send, and the longest."""

_DO_NOT_RESEND: Final = "Do not send it again."
_NOTHING_SENT: Final = "nothing was sent"
_SENT_EARLIER: Final = "This draft was already sent earlier; nothing went out now."
UNCONFIRMED: Final = (
    "Not confirmed; do not resend. The message may or may not have been delivered, so sending it "
    "again could deliver it twice. Look for it in the account's Sent folder, or tell the user."
)


class MessageSendArguments(Arguments):
    account_id: AccountId | None = None
    subject: Subject
    sender: Sender | None = SENDER
    to: Addresses | None = None
    cc: Addresses | None = None
    bcc: Addresses | None = None
    text: Text
    attachments: Attachments | None = None


class MessageReplyArguments(Arguments):
    ref: RefText = Field(description="Ref of the message to answer.")
    text: Text
    reply_all: StrictBool = False
    sender: Sender | None = SENDER
    cc: Addresses | None = Field(
        default=None, description="Further Cc addresses, added to the reply's own."
    )
    bcc: Addresses | None = None
    attachments: Attachments | None = None
    quote: StrictBool = Field(default=True, description="Quote the original under the text.")


class MessageForwardArguments(Arguments):
    ref: RefText = Field(description="Ref of the message to forward.")
    to: Addresses
    cc: Addresses | None = None
    bcc: Addresses | None = None
    text: Text | None = Field(default=None, description="A note above the forwarded message.")
    sender: Sender | None = SENDER
    include_attachments: StrictBool = True


class DraftSendArguments(Arguments):
    ref: RefText = Field(description="Ref of the draft.")


async def _settled(call: ToolCall, view: SendView) -> SendView:
    """``view`` once its send is over (sent and settled, failed or unknown), or as it is when
    ``SEND_WAIT`` has passed or the send cannot be followed any further: a send on its way must
    not read as a failure, so the last view stands."""
    deadline = time.monotonic() + SEND_WAIT
    delay, longest = _POLL
    while (view.state == "sending" or (view.state == "sent" and not view.settled)) and (
        time.monotonic() < deadline
    ):
        await anyio.sleep(delay)
        delay = min(delay * 2, longest)
        try:
            view = await call.thread(
                partial(service.read_send, call.ctx, call.user_id, view.send_id)
            )
        except Exception as error:
            logger.warning(
                "mail_send_not_followed",
                extra={"event": "mail_send_not_followed", "error_type": type(error).__name__},
            )
            break
    return view


def _outcome(view: SendView, sent: str) -> types.CallToolResult:
    """What a send came to, as the model reads it; ``sent`` is the sentence for one that went."""
    if view.state == "failed":
        code = view.code or "server_error"
        known = MESSAGES.get(cast("MailErrorCode", code), MESSAGES["server_error"])
        raise mail_refusal(code, known if _NOTHING_SENT in known else f"{known} Nothing was sent.")
    if view.state != "sent":  # still sending, or unknown: it may have gone
        return text_result(UNCONFIRMED)
    refused = (
        f" The server refused {untrusted(', '.join(view.rejected))}: they did not get it."
        if view.rejected
        else ""
    )
    notes = "".join(f" {note}" for note in view.notes)
    return text_result(f"{sent} {_DO_NOT_RESEND}{refused}{notes}")


async def _answer(call: ToolCall, dispatched: Dispatched) -> types.CallToolResult:
    message = dispatched.message
    who_text = who([message.sender], message.to, message.cc, message.bcc)
    if dispatched.send is None:
        approval = dispatched.send_mode == "approval"
        why = " and not every recipient is auto-approved" if approval else ""
        where = "in Drafts" if dispatched.draft is None else format_ref(dispatched.draft)
        return text_result(
            f"Saved as a draft, NOT sent ({dispatched.account}'s send mode is "
            f"{dispatched.send_mode}{why}): {who_text}. Draft: {where}."
        )
    return _outcome(await _settled(call, dispatched.send), f"Sent {who_text}.")


async def _admitted[T](call: ToolCall, admit: Callable[[], T]) -> T | None:
    """``admit`` in a thread, None when it timed out, which reads as not confirmed: it is no proof
    nothing left, since an admission outlives its deadline and may still claim and deliver. A
    direct send has no key to stop a retry, and once an own draft is sent its key has no draft to
    reach it, so the model sees a timeout and then a missing draft, and writes the message again."""
    try:
        return await call.thread(admit)
    except Refusal as refusal:
        if refusal.code != "timeout":
            raise
        return None


async def _send(call: ToolCall, dispatch: Callable[[], Dispatched]) -> types.CallToolResult:
    """``dispatch``, and what it came to."""
    dispatched = await _admitted(call, dispatch)
    if dispatched is None:
        return text_result(UNCONFIRMED)
    return await _answer(call, dispatched)


async def _message_send(args: MessageSendArguments, call: ToolCall) -> types.CallToolResult:
    account = await named_account(args.account_id, call)
    to, cc, bcc = recipients(args.to), recipients(args.cc), recipients(args.bcc)
    keep = keep_of(args.attachments)
    return await _send(
        call,
        lambda: service.send_message(
            call.ctx,
            call.user_id,
            account,
            sender=args.sender,
            to=to or (),
            cc=cc or (),
            bcc=bcc or (),
            subject=args.subject,
            text=args.text,
            attachments=keep,
            via="mcp",
        ),
    )


async def _message_reply(args: MessageReplyArguments, call: ToolCall) -> types.CallToolResult:
    cc, bcc = recipients(args.cc), recipients(args.bcc)
    keep = keep_of(args.attachments)
    return await _send(
        call,
        lambda: service.send_reply(
            call.ctx,
            call.user_id,
            args.ref,
            text=args.text,
            reply_all=args.reply_all,
            sender=args.sender,
            cc=cc or (),
            bcc=bcc or (),
            attachments=keep,
            quote=args.quote,
            via="mcp",
        ),
    )


async def _message_forward(args: MessageForwardArguments, call: ToolCall) -> types.CallToolResult:
    to, cc, bcc = recipients(args.to), recipients(args.cc), recipients(args.bcc)
    return await _send(
        call,
        lambda: service.send_forward(
            call.ctx,
            call.user_id,
            args.ref,
            to=to or (),
            cc=cc or (),
            bcc=bcc or (),
            text=args.text,
            sender=args.sender,
            include_attachments=args.include_attachments,
            via="mcp",
        ),
    )


async def _draft_send(args: DraftSendArguments, call: ToolCall) -> types.CallToolResult:
    # Read first, for the sentence naming whom it goes to: it must say what is stored.
    try:
        stored = await call.thread(
            lambda: service.read_draft(call.ctx, call.user_id, args.ref, via="mcp")
        )
    except Refusal as refusal:
        if refusal.code not in service.DRAFT_GONE:
            raise
        # A send of it can answer without it; with no such send, the send repeats this refusal.
        stored = None
    # A replay delivered what the draft held then, which need not be what it holds now.
    already = stored is not None and stored.send is not None and stored.send.state == "sent"
    send = await _admitted(
        call, lambda: service.send_draft(call.ctx, call.user_id, args.ref, by="agent", via="mcp")
    )
    if send is None:
        return text_result(UNCONFIRMED)
    settled = await _settled(call, send)
    if stored is None or already:
        return _outcome(settled, _SENT_EARLIER)
    draft = stored.draft
    return _outcome(settled, f"Sent {who(draft.sender, draft.to, draft.cc, draft.bcc)}.")


MAIL_SEND_TOOLS: Final[tuple[Tool[Any], ...]] = (
    Tool(
        name="message_send",
        title="Send a message",
        description=(
            "Send a new message. Depending on the account's send mode (accounts_list gives it) "
            "it is sent or only saved as a draft; the result says which, and to whom. Limited "
            "per hour and in recipients. Never call it again for the same message: a result that "
            "is not confirmed means it may have been delivered."
        ),
        group="mail_send",
        arguments=MessageSendArguments,
        hints=SEND,
        run=_message_send,
    ),
    Tool(
        name="message_reply",
        title="Reply to a message",
        description=(
            "Reply to a message: threaded, to its Reply-To address if it has one, else its sender "
            "(everyone with reply_all), from the identity it was sent to, quoting it. Sent or "
            "only saved as a draft depending on the send mode; the result says which, and lists "
            "every recipient. Never call it again for the same reply: a result that is not "
            "confirmed means it may have been delivered."
        ),
        group="mail_send",
        arguments=MessageReplyArguments,
        hints=SEND,
        run=_message_reply,
    ),
    Tool(
        name="message_forward",
        title="Forward a message",
        description=(
            "Forward a message, its attachments included by default, with an optional note "
            "above it. Sent or only saved as a draft depending on the send mode; the result says "
            "which, and to whom. Never call it again for the same forward: a result that is not "
            "confirmed means it may have been delivered."
        ),
        group="mail_send",
        arguments=MessageForwardArguments,
        hints=SEND,
        run=_message_forward,
    ),
    Tool(
        name="draft_send",
        title="Send a draft",
        description=(
            "Send a draft from Drafts exactly as it is stored (HTML, attachments and Message-ID "
            "included), then remove it from Drafts. Refused when the account's send mode would "
            "only save it as a draft again. Asking twice delivers once; a result that is not "
            "confirmed means it may have been delivered, so do not resend."
        ),
        group="mail_send",
        arguments=DraftSendArguments,
        hints=SEND,
        run=_draft_send,
    ),
)
