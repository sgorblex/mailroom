"""Moving mail to another of the user's accounts, over ``mailroom.mail.service``: each message is
copied to the target folder, confirmed there, then its original is moved to its account's Trash
(``messages_move_to_account``) or, only when the user explicitly asks, deleted for good
(``messages_move_to_account_for_good``, a tool of its own so that no other call deletes as a
side effect). The text is mailroom-core's ``organize_text``, one line per source folder.

A call is a tool error when no folder succeeded and nothing was copied; when copies were made
but their originals stayed, the answer says so, since the messages are then in both accounts.
Either carries folder names the mail servers chose, so it sits inside the untrusted-content
envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from pydantic import Field

from mailroom.mail import service
from mailroom.mcp.tool import Arguments, Hints, Tool
from mailroom.mcp.tools.accounts import AccountId  # noqa: TC001 -- Pydantic reads it at runtime
from mailroom.mcp.tools.mail_organize import (
    Refs,
    organize_answer,
)
from mailroom.mcp.tools.mail_read import FolderPath  # noqa: TC001 -- Pydantic reads it at runtime

if TYPE_CHECKING:
    from mcp import types

    from mailroom.mcp.tool import ToolCall
    from mailroom_core.mail import TransferRemoval

MOVE_TO_ACCOUNT: Final = Hints(destructive=True, open_world=True)
"""The originals go to Trash, which the server may empty on its own schedule, or for good."""


class MessagesMoveToAccountArguments(Arguments):
    refs: Refs
    account_id: AccountId = Field(
        description="The account to move them to, as accounts_list gives its `id`; not theirs."
    )
    folder: FolderPath = Field(
        description="A folder of that account, as folders_list gives its `path`."
    )


async def _move(
    args: MessagesMoveToAccountArguments, call: ToolCall, removal: TransferRemoval
) -> types.CallToolResult:
    result = await call.thread(
        lambda: service.move_to_account(
            call.ctx,
            call.user_id,
            args.refs,
            args.account_id,
            args.folder,
            removal,
            origin=call.origin,
        )
    )
    return organize_answer(result, copies_answer=True)


async def _messages_move_to_account(
    args: MessagesMoveToAccountArguments, call: ToolCall
) -> types.CallToolResult:
    return await _move(args, call, "trash")


async def _messages_move_to_account_for_good(
    args: MessagesMoveToAccountArguments, call: ToolCall
) -> types.CallToolResult:
    return await _move(args, call, "delete")


MAIL_TRANSFER_TOOLS: Final[tuple[Tool[Any], ...]] = (
    Tool(
        name="messages_move_to_account",
        title="Move messages to another account",
        description=(
            "Move messages to a folder (not Drafts or Sent) of another of the user's accounts: "
            "each is copied there with its flags and date, then its original goes to its own "
            "account's Trash. Old refs stop working; the result gives the new ones. A message "
            "with no Message-ID, or over the user's move size limit, is refused. If the "
            "answer says some were copied but not removed, they are in both accounts: do not "
            "move them again. Repeating a move that was cut short copies nothing twice."
        ),
        group="mail_transfer",
        arguments=MessagesMoveToAccountArguments,
        hints=MOVE_TO_ACCOUNT,
        run=_messages_move_to_account,
    ),
    Tool(
        name="messages_move_to_account_for_good",
        title="Move messages to another account, deleting the originals",
        description=(
            "As messages_move_to_account, but each original is deleted for good once its copy "
            "is confirmed, not moved to Trash: this cannot be undone. Call it only when the user "
            "explicitly asks for the originals to be deleted permanently."
        ),
        group="mail_transfer",
        arguments=MessagesMoveToAccountArguments,
        hints=MOVE_TO_ACCOUNT,
        run=_messages_move_to_account_for_good,
    ),
)
