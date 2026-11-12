"""An account's folders: create, rename and delete, over ``mailroom.mail.service`` (never over
mailroom-core directly), as the REST folder routes are (``docs/requirements/mail-client.md``,
*Folder management*). ``folder_delete`` is the one MCP tool whose own call deletes mail for good
(a filter's ``delete`` action does too, once its rule set runs): only with the message count the
user confirmed, refused when it changed.

The answers are JSON like ``folders_list``'s, the paths as the server names them. A refusal's text
is the service's: mailroom-core's fixed messages, and ``folder_in_use`` naming the user's own rule
sets; none carries the server's words.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Final

from pydantic import Field, StrictInt

from mailroom.errors import Refusal
from mailroom.mail import service
from mailroom.mcp.tool import Arguments, Hints, JsonObject, Tool
from mailroom.mcp.tools.accounts import AccountId  # noqa: TC001 -- Pydantic reads it at runtime
from mailroom.mcp.tools.mail_read import FolderPath

if TYPE_CHECKING:
    from collections.abc import Callable

    from mailroom.mcp.tool import ToolCall

FOLDER_WRITE: Final = Hints(open_world=True)
"""A change to the mail server that loses no mail (and can be undone with another)."""
FOLDER_DELETE: Final = Hints(destructive=True, open_world=True)
"""Deletes the folder's mail for good."""

FolderName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=service.MAX_FOLDER_NAME,
        description="The folder's own name: one level, without the delimiter.",
    ),
]
Folder = Annotated[
    FolderPath, Field(description="The folder's path, as folders_list gives it in `path`.")
]
Parent = Annotated[
    FolderPath | None,
    Field(
        description=(
            "The parent folder's path, as folders_list gives it in `path`; null for the top level."
        )
    ),
]


class FolderCreateArguments(Arguments):
    account_id: AccountId
    parent: Parent
    name: FolderName


class FolderRenameArguments(Arguments):
    account_id: AccountId
    folder: Folder
    parent: FolderPath | None = Field(
        description=(
            "Where the folder goes: a path as folders_list gives it, its current parent to keep "
            "its place, or null for the top level."
        )
    )
    name: FolderName


class FolderDeleteArguments(Arguments):
    account_id: AccountId
    folder: Folder
    messages: Annotated[StrictInt, Field(ge=0, le=service.MAX_MESSAGE_COUNT)] = Field(
        description=(
            "The folder's message count the user confirmed: its `total` from "
            "folders_list(counts=true)."
        )
    )


RULE_SETS_NOT_UPDATED: Final = (
    "The folder was renamed, but its rule sets could not be updated: they still name the old "
    "path. Tell the user, who can point them at the new one."
)


async def _folder_create(args: FolderCreateArguments, call: ToolCall) -> JsonObject:
    change = await call.thread(
        lambda: service.create_folder(
            call.ctx, call.user_id, args.account_id, args.parent, args.name, origin=call.origin
        )
    )
    return {"account_id": args.account_id, "path": change.path}


async def _folder_rename(args: FolderRenameArguments, call: ToolCall) -> JsonObject:
    renamed = await call.thread(
        lambda: service.rename_folder(
            call.ctx,
            call.user_id,
            args.account_id,
            args.folder,
            args.parent,
            args.name,
            origin=call.origin,
        )
    )
    change = renamed.change
    answer: JsonObject = {
        "account_id": args.account_id,
        "folder": change.path,
        "path": change.destination or change.path,
        "rule_sets": list(renamed.rule_sets),
        "filters": list(renamed.filters),
        "rule_sets_updated": renamed.rule_sets_updated,
    }
    if not renamed.rule_sets_updated:
        answer["note"] = RULE_SETS_NOT_UPDATED
    return answer


def _worded_for_an_agent(refusal: Refusal) -> Refusal:
    """``refusal``, with what the agent does next where the service's text leaves it out."""
    if refusal.code == "folder_changed":
        count = refusal.extra.get("messages")
        message = (
            f"{refusal.message} Tell the user, and call again with messages={count} only once "
            "they confirm deleting that many."
        )
    elif refusal.code == "folder_in_use":
        listed = refusal.extra.get("rule_sets")
        ids = ", ".join(str(ref["id"]) for ref in listed) if isinstance(listed, list) else ""
        message = f"{refusal.message} Rule set ids: {ids}." if ids else refusal.message
    else:
        return refusal
    return Refusal(refusal.kind, refusal.code, message, extra=refusal.extra)


async def _refusals_worded[T](call: ToolCall, work: Callable[[], T]) -> T:
    try:
        return await call.thread(work)
    except Refusal as refusal:
        raise _worded_for_an_agent(refusal) from None


async def _folder_delete(args: FolderDeleteArguments, call: ToolCall) -> JsonObject:
    change = await _refusals_worded(
        call,
        lambda: service.delete_folder(
            call.ctx,
            call.user_id,
            args.account_id,
            args.folder,
            messages=args.messages,
            origin=call.origin,
        ),
    )
    return {"account_id": args.account_id, "folder": change.path, "messages": args.messages}


MAIL_FOLDERS_TOOLS: Final[tuple[Tool[Any], ...]] = (
    Tool(
        name="folder_create",
        title="Create a folder",
        description=(
            "Create a folder, subscribed: `name` under `parent` (a path as folders_list gives "
            "it, or null for the top level). Refused when a folder with that path exists. The "
            "answer gives the new folder's path."
        ),
        group="mail_folders",
        arguments=FolderCreateArguments,
        hints=FOLDER_WRITE,
        run=_folder_create,
    ),
    Tool(
        name="folder_rename",
        title="Rename or move a folder",
        description=(
            "Rename a folder, or move it under another parent, with its subfolders and their "
            "mail; nothing is deleted. Not INBOX, a folder with a role (sent, drafts, trash, "
            "junk, archive…) or one holding such a folder. Every rule set and filter action "
            "(move_to, trash) that names the folder or one under it is pointed at the new path, "
            "enabled rule sets included, since they keep acting on the same folder: the answer "
            "lists their ids (`rule_sets`, `filters`), and `rule_sets_updated` is false when "
            "they could not be updated (the folder is renamed all the same). Refs to messages "
            "in the folder stop working."
        ),
        group="mail_folders",
        arguments=FolderRenameArguments,
        hints=FOLDER_WRITE,
        run=_folder_rename,
    ),
    Tool(
        name="folder_delete",
        title="Delete a folder and its mail",
        description=(
            "Delete a folder and every message in it for good: not to Trash, and nothing can "
            "recover them. Call it only when the user explicitly asks to delete this folder, "
            "and pass `messages`: the `total` folders_list(counts=true) gave for it, which the "
            "user confirmed. Refused, deleting nothing, when the count changed (the refusal "
            "gives the new one: confirm it with the user, then call again), when the folder has "
            "subfolders, is INBOX or has a role, or while a rule set or filter names it or a "
            "folder under it (the refusal names them). On Gmail a folder is a label: its mail "
            "stays in All Mail."
        ),
        group="mail_folders",
        arguments=FolderDeleteArguments,
        hints=FOLDER_DELETE,
        run=_folder_delete,
    ),
)
