"""Organizing mail: move, trash, flag and snooze, over ``mailroom.mail.service`` (never over
mailroom-core directly). The behaviour and the text are mailroom-mcp's ``move``, ``trash`` and
``flag`` (mailroom-core's ``organize_text``): one line per folder the refs came from. These tools
never delete mail permanently, and none expunges anything it did not move. ``messages_snooze``
moves one message to its account's Snoozed folder until a time, when the worker brings it back.

A call is a tool error when every folder failed (a lone folder's error message, or the text
naming each folder), and an answer naming each folder's outcome otherwise. Either carries folder
names the mail server chose, so it sits inside the untrusted-content envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Final

from mcp import types
from pydantic import AwareDatetime, Field, StrictBool

from mailroom.errors import mail_refusal
from mailroom.mail import service
from mailroom.mcp.tool import Arguments, Hints, Tool, content_result, text_result
from mailroom.mcp.tools.mail_read import FolderPath, RefText
from mailroom_core.mail import organize_text
from mailroom_core.render import format_date, untrusted

if TYPE_CHECKING:
    from collections.abc import Callable

    from mailroom.mcp.tool import ToolCall
    from mailroom_core.mail import OrganizeResult

ORGANIZE: Final = Hints(open_world=True)
"""A change to the mail server that mail can be moved back from."""
ORGANIZE_IDEMPOTENT: Final = Hints(idempotent=True, open_world=True)
"""Marking the same state twice changes nothing."""
TRASH: Final = Hints(destructive=True, open_world=True)
"""Moves to Trash, which the server may empty on its own schedule (Gmail after 30 days)."""

Refs = Annotated[
    list[RefText],
    Field(
        min_length=1,
        max_length=service.MAX_REFS,
        # Neutral: mail_organize can be served without the tools that give refs, and the server
        # instructions name those when they are served.
        description="Message refs (up to 50, any folders).",
    ),
]


SOURCE_FOLDER: Final = (
    "The folder the refs were listed from (INBOX or a label's path), on Gmail: a ref of All "
    "Mail is then acted on there, so it leaves that folder only and keeps its other labels. "
    "Without it, a Gmail move out of All Mail only adds the destination's label. Ignored for "
    "other accounts."
)


class MessagesMoveArguments(Arguments):
    refs: Refs
    folder: FolderPath = Field(
        description="Destination folder path, as folders_list gives it in `path`."
    )
    source_folder: FolderPath | None = Field(default=None, description=SOURCE_FOLDER)


class MessagesTrashArguments(Arguments):
    refs: Refs
    source_folder: FolderPath | None = Field(default=None, description=SOURCE_FOLDER)


class MessagesFlagArguments(Arguments):
    refs: Refs
    seen: StrictBool | None = Field(default=None, description="true: mark read; false: unread.")
    flagged: StrictBool | None = Field(
        default=None, description="true: flag (star); false: unflag."
    )
    handled: StrictBool | None = Field(
        default=None,
        description=(
            "true: mark Handled (dealt with: the filters may sort it); false: clear it. "
            "accounts_list says what each account keeps it as (handled): where that is star, it "
            "is the same mark as flagged."
        ),
    )


class MessagesSnoozeArguments(Arguments):
    ref: RefText = Field(description="The message's ref.")
    until: AwareDatetime = Field(
        strict=False,  # JSON carries it as text
        description=(
            "When it comes back: an ISO 8601 time with its UTC offset, in the future and at "
            "most a year ahead."
        ),
    )
    source_folder: FolderPath | None = Field(default=None, description=SOURCE_FOLDER)


def organize_answer(result: OrganizeResult, *, copies_answer: bool = False) -> types.CallToolResult:
    """The text of ``result``, or the refusal when no folder succeeded. ``copies_answer``: a
    failed folder that made copies (``new_refs``) is answered too, its messages being in both
    places."""
    if result.succeeded or (copies_answer and any(group.new_refs for group in result.groups)):
        return content_result(types.TextContent(type="text", text=untrusted(organize_text(result))))
    [first, *_] = result.groups
    error = first.error
    assert error is not None  # no folder succeeded, so each has its error
    if len(result.groups) == 1:
        raise mail_refusal(error.code, untrusted(error.message))
    raise mail_refusal(error.code, untrusted(organize_text(result)))


async def _organize(call: ToolCall, work: Callable[[], OrganizeResult]) -> types.CallToolResult:
    return organize_answer(await call.thread(work))


async def _messages_move(args: MessagesMoveArguments, call: ToolCall) -> types.CallToolResult:
    return await _organize(
        call,
        lambda: service.move(
            call.ctx,
            call.user_id,
            args.refs,
            args.folder,
            source_folder=args.source_folder,
            origin=call.origin,
        ),
    )


async def _messages_trash(args: MessagesTrashArguments, call: ToolCall) -> types.CallToolResult:
    return await _organize(
        call,
        lambda: service.trash(
            call.ctx,
            call.user_id,
            args.refs,
            source_folder=args.source_folder,
            origin=call.origin,
        ),
    )


async def _messages_flag(args: MessagesFlagArguments, call: ToolCall) -> types.CallToolResult:
    return await _organize(
        call,
        lambda: service.flag(
            call.ctx,
            call.user_id,
            args.refs,
            seen=args.seen,
            flagged=args.flagged,
            handled=args.handled,
            origin=call.origin,
        ),
    )


async def _messages_snooze(args: MessagesSnoozeArguments, call: ToolCall) -> types.CallToolResult:
    snoozed, zone = await call.thread(
        lambda: (
            service.snooze(
                call.ctx,
                call.user_id,
                args.ref,
                args.until,
                source_folder=args.source_folder,
                origin=call.origin,
            ),
            call.ctx.time_zone(call.user_id),
        )
    )
    return text_result(
        untrusted(
            f"Snoozed until {format_date(snoozed.until, zone)} ({zone}): moved to the account's "
            f"Snoozed folder, it comes back to {snoozed.folder} marked unread then. Its ref no "
            "longer works; the user can bring it back sooner from Mailroom's Snoozed list."
        )
    )


MAIL_ORGANIZE_TOOLS: Final[tuple[Tool[Any], ...]] = (
    Tool(
        name="messages_move",
        title="Move messages",
        description=(
            "Move messages to another existing folder (as folders_list gives its `path`); not "
            "into Drafts or Sent. Old refs stop working; the result gives new ones. Moving into "
            "Junk also teaches the account's spam filter that the messages are spam, and moving "
            "out of Junk (except to Trash) that they are not."
        ),
        group="mail_organize",
        arguments=MessagesMoveArguments,
        hints=ORGANIZE,
        run=_messages_move,
    ),
    Tool(
        name="messages_trash",
        title="Move messages to Trash",
        description=(
            "Move messages to the account's Trash folder; they can be moved back with "
            "messages_move. It never deletes permanently (the server may still empty "
            "Trash on its own schedule, as Gmail does after 30 days)."
        ),
        group="mail_organize",
        arguments=MessagesTrashArguments,
        hints=TRASH,
        run=_messages_trash,
    ),
    Tool(
        name="messages_flag",
        title="Mark messages",
        description=(
            "Mark messages read or unread, flag or unflag them, and mark them Handled or clear "
            "it. Refs stay valid."
        ),
        group="mail_organize",
        arguments=MessagesFlagArguments,
        hints=ORGANIZE_IDEMPOTENT,
        run=_messages_flag,
    ),
    Tool(
        name="messages_snooze",
        title="Snooze a message",
        description=(
            "Snooze a message until a time: it moves to the account's Snoozed folder (made "
            "when missing; a label on Gmail) and comes back to where it was, marked unread, "
            "then. Not from Drafts, Sent, Junk, Trash or Gmail's All Mail."
        ),
        group="mail_organize",
        arguments=MessagesSnoozeArguments,
        hints=ORGANIZE,
        run=_messages_snooze,
    ),
)
