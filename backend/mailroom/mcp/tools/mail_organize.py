"""Organizing mail: move, trash and flag, over ``mailroom.mail.service`` (never over
mailroom-core directly). The behaviour and the text are mailroom-mcp's ``move``, ``trash`` and
``flag`` (mailroom-core's ``organize_text``): one line per folder the refs came from. These tools
never delete mail permanently, and none expunges anything it did not move.

A call is a tool error when every folder failed (a lone folder's error message, or the text
naming each folder), and an answer naming each folder's outcome otherwise. Either carries folder
names the mail server chose, so it sits inside the untrusted-content envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Final

from mcp import types
from pydantic import Field, StrictBool

from mailroom.errors import mail_refusal
from mailroom.mail import service
from mailroom.mcp.tool import Arguments, Hints, Tool, content_result
from mailroom.mcp.tools.mail_read import FolderPath, RefText
from mailroom_core.mail import organize_text
from mailroom_core.render import untrusted

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


class MessagesMoveArguments(Arguments):
    refs: Refs
    folder: FolderPath = Field(
        description="Destination folder path, as folders_list gives it in `path`."
    )


class MessagesTrashArguments(Arguments):
    refs: Refs


class MessagesFlagArguments(Arguments):
    refs: Refs
    seen: StrictBool | None = Field(default=None, description="true: mark read; false: unread.")
    flagged: StrictBool | None = Field(
        default=None, description="true: flag (star); false: unflag."
    )


def _answer(result: OrganizeResult) -> types.CallToolResult:
    """The text of ``result``, or the refusal when no folder succeeded."""
    if result.succeeded:
        return content_result(types.TextContent(type="text", text=untrusted(organize_text(result))))
    [first, *_] = result.groups
    error = first.error
    assert error is not None  # no folder succeeded, so each has its error
    if len(result.groups) == 1:
        raise mail_refusal(error.code, untrusted(error.message))
    raise mail_refusal(error.code, untrusted(organize_text(result)))


async def _organize(call: ToolCall, work: Callable[[], OrganizeResult]) -> types.CallToolResult:
    return _answer(await call.thread(work))


async def _messages_move(args: MessagesMoveArguments, call: ToolCall) -> types.CallToolResult:
    return await _organize(
        call,
        lambda: service.move(call.ctx, call.user_id, args.refs, args.folder, origin=call.origin),
    )


async def _messages_trash(args: MessagesTrashArguments, call: ToolCall) -> types.CallToolResult:
    return await _organize(
        call, lambda: service.trash(call.ctx, call.user_id, args.refs, origin=call.origin)
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
            origin=call.origin,
        ),
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
        description="Mark messages read or unread, and flag or unflag them. Refs stay valid.",
        group="mail_organize",
        arguments=MessagesFlagArguments,
        hints=ORGANIZE_IDEMPOTENT,
        run=_messages_flag,
    ),
)
