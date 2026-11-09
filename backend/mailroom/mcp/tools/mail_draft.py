"""Writing drafts: create, update and delete, over ``mailroom.mail.service`` (never over
mailroom-core directly). Behaviour and inputs are mailroom-mcp's ``draft_create``, ``draft_update``
and ``draft_delete``, with this server's account naming and the app's refs. Nothing here sends.

A draft is saved in the account's Drafts folder as a new message: an update saves the next version
and removes the old one (``docs/requirements/mail-client.md``, *The own-draft exception*), so the
draft gets a new ref. The account's send mode ``off`` refuses all three.

The result gives the draft's ref, from which identity and to whom, read back from the stored draft
(every address inside the untrusted-content envelope: a reply takes them from an email).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Final

from pydantic import Field, StrictBool

from mailroom.errors import Refusal
from mailroom.mail import service
from mailroom.mail.service import DraftFields
from mailroom.mcp.tool import Arguments, Hints, Tool, text_result
from mailroom.mcp.tools.mail_read import RefText, named_account
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
from mailroom_core.mail import format_ref

if TYPE_CHECKING:
    from mcp import types

    from mailroom.mcp.tool import ToolCall
    from mailroom_core.mail import Draft, SavedDraft

DRAFT: Final = Hints(open_world=True)
"""Adds a message to Drafts and nothing else."""
DRAFT_REPLACING: Final = Hints(destructive=True, open_world=True)
"""An edited draft is a new message and the old version is removed (or moved to Trash)."""
_NOT_EDITABLE: Final = (
    f"{service.DRAFT_NOT_EDITABLE} Look for it in the account's Sent folder, or tell the user."
)


class DraftCreateArguments(Arguments):
    account_id: AccountId | None = None
    subject: Subject | None = Field(
        default=None, description='Subject; for a reply, "Re: " plus the original\'s by default.'
    )
    sender: Sender | None = SENDER
    to: Addresses | None = None
    cc: Addresses | None = None
    bcc: Addresses | None = None
    text: Text
    attachments: Attachments | None = None
    reply_to: RefText | None = Field(
        default=None, description="Ref of the message this draft answers."
    )
    reply_all: StrictBool = Field(
        default=False, description="With reply_to: also address its other recipients."
    )
    quote: StrictBool = Field(
        default=True, description="With reply_to: quote the original under the text."
    )


class DraftUpdateArguments(Arguments):
    ref: RefText = Field(description="Ref of the draft, from draft_create or a listing of Drafts.")
    subject: Subject | None = None
    sender: Sender | None = SENDER
    to: Addresses | None = None
    cc: Addresses | None = None
    bcc: Addresses | None = None
    text: Text | None = None
    attachments: Attachments | None = None


class DraftDeleteArguments(Arguments):
    ref: RefText = Field(description="Ref of the draft.")


def _compose_id() -> str:
    """What every version of a draft builds its Message-ID on."""
    return uuid.uuid4().hex


def _answered_ref(draft: Draft) -> str | None:
    return None if draft.answers is None else format_ref(draft.answers)


async def _saved(call: ToolCall, saved: SavedDraft) -> str:
    """The sentence for a save, from the draft as it is stored: its identity and recipients are
    read back, since a reply takes them from an email."""
    if saved.ref is None:
        return (
            "Draft saved in Drafts (its ref could not be read back; list Drafts to find it). "
            "Not sent."
        )
    ref = format_ref(saved.ref)
    attachments = f"{len(saved.parts)} attachment(s). Not sent."
    try:
        stored = await call.thread(
            lambda: service.read_draft(call.ctx, call.user_id, ref, via="mcp")
        )
    except Refusal:  # it is saved: an error here would invite a second copy
        return (
            f"Draft saved as {ref}: {attachments} "
            "Its recipients could not be read back; read it before sending."
        )
    draft = stored.draft
    recipients_text = who(draft.sender, draft.to, draft.cc, draft.bcc)
    return f"Draft saved as {ref}: {recipients_text}, {attachments}"


async def _draft_create(args: DraftCreateArguments, call: ToolCall) -> types.CallToolResult:
    to, cc, bcc = recipients(args.to), recipients(args.cc), recipients(args.bcc)
    keep = keep_of(args.attachments)
    if args.reply_to is None:
        account = await named_account(args.account_id, call)
        fields = DraftFields(
            account=account,
            compose_id=_compose_id(),
            sender=args.sender,
            to=to or (),
            cc=cc or (),
            bcc=bcc or (),
            subject=args.subject or "",
            text=args.text,
            keep=keep,
        )
    else:
        answered = args.reply_to
        prefilled = await call.thread(
            lambda: service.prefill(
                call.ctx,
                call.user_id,
                answered,
                "reply_all" if args.reply_all else "reply",
                via="mcp",
            )
        )
        if args.account_id is not None and args.account_id != prefilled.account:
            raise Refusal("invalid", "invalid_argument", "reply_to belongs to another account.")
        fields = DraftFields(
            account=prefilled.account,
            compose_id=_compose_id(),
            answers=answered,
            sender=args.sender or prefilled.sender,
            to=prefilled.to if to is None else to,
            cc=prefilled.cc if cc is None else cc,
            bcc=bcc or (),
            subject=prefilled.subject if args.subject is None else args.subject,
            text=f"{args.text}{prefilled.text}" if args.quote else args.text,
            keep=keep,
        )
    saved = await call.thread(lambda: service.save_draft(call.ctx, call.user_id, fields, via="mcp"))
    return text_result(await _saved(call, saved))


async def _draft_update(args: DraftUpdateArguments, call: ToolCall) -> types.CallToolResult:
    to, cc, bcc = recipients(args.to), recipients(args.cc), recipients(args.bcc)
    try:
        current = await call.thread(
            lambda: service.read_draft(call.ctx, call.user_id, args.ref, via="mcp")
        )
    except Refusal as refusal:
        # Its delivery is what normally takes a draft written elsewhere away.
        if refusal.code in service.DRAFT_GONE and await call.thread(
            lambda: service.send_holds(call.ctx, call.user_id, args.ref)
        ):
            raise Refusal("conflict", "draft_not_editable", _NOT_EDITABLE) from None
        raise
    if current.send is not None and current.send.state in service.SEND_HOLDS_THE_DRAFT:
        raise Refusal("conflict", "draft_not_editable", _NOT_EDITABLE)
    draft = current.draft
    if args.attachments is None:
        ref = format_ref(draft.ref)
        keep = [(ref, part.part) for part in draft.attachments]
    else:
        keep = keep_of(args.attachments)
    fields = DraftFields(
        account=draft.ref.account,
        compose_id=draft.compose_id or _compose_id(),
        ref=args.ref,
        answers=_answered_ref(draft),
        sender=args.sender or (draft.sender[0].address if draft.sender else None),
        to=draft.to if to is None else to,
        cc=draft.cc if cc is None else cc,
        bcc=draft.bcc if bcc is None else bcc,
        subject=draft.subject if args.subject is None else args.subject,
        text=draft.text if args.text is None else args.text,
        keep=keep,
    )
    saved = await call.thread(lambda: service.save_draft(call.ctx, call.user_id, fields, via="mcp"))
    gone = {"expunged": "removed", "trashed": "moved to Trash"}.get(saved.replaced or "")
    if saved.ref is None:
        previous = " The previous version was left in place."
    elif gone is None:
        previous = " The previous version could not be removed from Drafts."
    else:
        previous = f" The previous version was {gone}."
    lossy = (
        " The previous version had HTML or more text than is read; this one keeps its plain "
        "text only."
        if draft.lossy
        else ""
    )
    return text_result(f"{await _saved(call, saved)}{previous}{lossy}")


async def _draft_delete(args: DraftDeleteArguments, call: ToolCall) -> types.CallToolResult:
    removed = await call.thread(
        lambda: service.delete_draft(call.ctx, call.user_id, args.ref, via="mcp")
    )
    return text_result("Draft deleted." if removed == "expunged" else "Draft moved to Trash.")


MAIL_DRAFT_TOOLS: Final[tuple[Tool[Any], ...]] = (
    Tool(
        name="draft_create",
        title="Create a draft",
        description=(
            "Save a new message in Drafts without sending it. With `reply_to`, it answers that "
            "message: threaded, addressed to its sender (all its recipients with reply_all), "
            "from the identity it was sent to, and quoting it. The result gives the draft's ref."
        ),
        group="mail_draft",
        arguments=DraftCreateArguments,
        hints=DRAFT,
        run=_draft_create,
    ),
    Tool(
        name="draft_update",
        title="Change a draft",
        description=(
            "Change a draft in Drafts: any field given replaces the old one, the rest is kept "
            "(attachments too, unless `attachments` is given). The draft gets a new ref; the old "
            "one stops working. A draft written elsewhere keeps only its plain text. Refused "
            "once the draft was sent or may have been."
        ),
        group="mail_draft",
        arguments=DraftUpdateArguments,
        hints=DRAFT_REPLACING,
        run=_draft_update,
    ),
    Tool(
        name="draft_delete",
        title="Delete a draft",
        description=(
            "Delete a draft from Drafts. A draft Mailroom wrote is removed; one written "
            "elsewhere is moved to Trash."
        ),
        group="mail_draft",
        arguments=DraftDeleteArguments,
        hints=DRAFT_REPLACING,
        run=_draft_delete,
    ),
)
