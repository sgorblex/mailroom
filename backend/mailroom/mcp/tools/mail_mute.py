"""Muted threads: muting the conversation a message belongs to, listing and unmuting them, over
``mailroom.filters.mute`` as the web's routes (``/mail/messages/{ref}/mute``, ``/mail/muted``).
Part of ``mail_organize``: a muted thread's new messages arrive marked read, nothing is moved."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Final

from pydantic import Field

from mailroom.filters import mute
from mailroom.mcp.tool import READ, Arguments, Hints, JsonObject, Tool
from mailroom.mcp.tools.accounts import AccountId  # noqa: TC001 -- Pydantic reads it at runtime
from mailroom.mcp.tools.mail_read import RefText

if TYPE_CHECKING:
    from mailroom.mcp.tool import ToolCall

# Neutral: mail_organize can be served without the tools that give refs (as its Refs).
Ref = Annotated[RefText, Field(description="A message ref.")]
MutedThreadId = Annotated[
    int, Field(ge=1, le=2**63 - 1, description="A muted thread's id, from muted_threads_list.")
]

_RUN_FILTERS = (
    " Muting acts only while the account's Run filters is on: run_filters in the answer says "
    "whether it is."
)


class ThreadMuteArguments(Arguments):
    ref: Ref


class MutedThreadsListArguments(Arguments):
    account_id: AccountId | None = Field(
        default=None, description="Only this account's muted threads; every account's if left out."
    )


class ThreadUnmuteArguments(Arguments):
    id: MutedThreadId


async def _thread_mute(args: ThreadMuteArguments, call: ToolCall) -> JsonObject:
    muted = await call.db(
        lambda db: mute.mute(call.ctx, db, call.user_id, args.ref, origin=call.origin)
    )
    return muted.model_dump(mode="json")


async def _muted_threads_list(args: MutedThreadsListArguments, call: ToolCall) -> JsonObject:
    listed = await call.db(lambda db: mute.muted_threads(db, call.user_id, args.account_id))
    return {"threads": [thread.model_dump(mode="json") for thread in listed]}


async def _thread_unmute(args: ThreadUnmuteArguments, call: ToolCall) -> JsonObject:
    await call.db(lambda db: mute.unmute(call.ctx, db, call.user_id, args.id, origin=call.origin))
    return {"unmuted": True}


MAIL_MUTE_TOOLS: Final[tuple[Tool[Any], ...]] = (
    Tool(
        name="thread_mute",
        title="Mute a conversation",
        description=(
            "Mute the conversation the message belongs to, on its account: its new messages in "
            "INBOX arrive marked read; nothing is moved or deleted. Found by the Message-IDs "
            "its messages cite (and Gmail's thread), never by subject. Muting a conversation "
            "already muted keeps one muted thread." + _RUN_FILTERS
        ),
        group="mail_organize",
        arguments=ThreadMuteArguments,
        hints=Hints(idempotent=True, open_world=True),
        run=_thread_mute,
    ),
    Tool(
        name="muted_threads_list",
        title="List muted conversations",
        description=(
            "The user's muted conversations, oldest first: id, account_id, how many Message-IDs "
            "each is known by, whether by its Gmail thread too, and run_filters."
        ),
        group="mail_organize",
        arguments=MutedThreadsListArguments,
        hints=READ,
        run=_muted_threads_list,
    ),
    Tool(
        name="thread_unmute",
        title="Unmute a conversation",
        description="Unmute a muted conversation (its id from muted_threads_list).",
        group="mail_organize",
        arguments=ThreadUnmuteArguments,
        hints=Hints(),
        run=_thread_unmute,
    ),
)
