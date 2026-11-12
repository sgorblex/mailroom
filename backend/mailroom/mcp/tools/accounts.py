"""The user's mail accounts as the filter and mail tools need them: which there are, their
folders, and the Run filters switch.

``folders_list`` lists through ``mailroom.mail.service`` (mailroom-core's LIST, the server's
paths and delimiter, ``segments`` split on it), as the REST folder picker of a rule set does
(``mailroom.filters.service.account_folders``): both name a folder by the same segments."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Final

from pydantic import Field, StrictBool

from mailroom.accounts import service
from mailroom.filters import service as filters_service
from mailroom.mail import service as mail_service
from mailroom.mcp.tool import MAILBOX_READ, READ, Arguments, Hints, JsonObject, Tool

if TYPE_CHECKING:
    from mailroom.mcp.tool import ToolCall

AccountId = Annotated[
    str, Field(min_length=1, max_length=64, description="An account id, from accounts_list.")
]

ACCOUNT_FIELDS: Final = {"id", "login", "has_password", "run_filters", "send_mode", "identities"}


class NoArguments(Arguments):
    pass


class AccountArguments(Arguments):
    account_id: AccountId


class FoldersArguments(AccountArguments):
    counts: StrictBool = Field(
        default=False,
        description="Add each folder's unread and total counts (one more command per folder).",
    )


class RunFiltersArguments(Arguments):
    account_id: AccountId
    on: StrictBool = Field(description="True to turn Run filters on, false to turn it off.")


async def _accounts_list(_args: NoArguments, call: ToolCall) -> JsonObject:
    listed = await call.db(lambda db: service.listed_accounts(call.ctx, db, call.user_id))
    return {
        "accounts": [account.model_dump(mode="json", include=ACCOUNT_FIELDS) for account in listed]
    }


async def _folders_list(args: FoldersArguments, call: ToolCall) -> JsonObject:
    listed = await call.thread(
        lambda: mail_service.folders(
            call.ctx, call.user_id, args.account_id, counts=args.counts, via="mcp"
        )
    )
    folders = []
    for folder in listed:
        entry: JsonObject = {
            "path": folder.path,
            "segments": mail_service.folder_segments(folder),
            "delimiter": folder.delimiter,
            "role": folder.role,
        }
        if args.counts:
            # None when the server would not say (STATUS failed): not an empty folder.
            entry["unread"] = folder.unseen
            entry["total"] = folder.messages
        folders.append(entry)
    return {"account_id": args.account_id, "folders": folders}


async def _account_run_filters(args: RunFiltersArguments, call: ToolCall) -> JsonObject:
    on = await call.db(
        lambda db: filters_service.set_run_filters(
            call.ctx, db, call.user_id, args.account_id, on=args.on, via="mcp"
        )
    )
    return {"account_id": args.account_id, "run_filters": on}


ACCOUNT_TOOLS: Final[tuple[Tool[Any], ...]] = (
    Tool(
        name="accounts_list",
        title="List accounts",
        description=(
            "The user's mail accounts: id, login, whether a password is stored, the send mode "
            "(what an agent may send: off, drafts, approval or direct) and the identities it "
            "can send as (`address`, `name`, and which is the `default`); where the instance "
            "runs filters, also whether Run filters is on (`run_filters`: rule sets run only on "
            "an account where it is). Call this first."
        ),
        group="filters_read",
        also_in=("mail_read", "mail_organize", "mail_folders", "mail_draft", "mail_send"),
        arguments=NoArguments,
        hints=READ,
        run=_accounts_list,
    ),
    Tool(
        name="folders_list",
        title="List folders",
        description=(
            "An account's folders, each with its path as the server names it ('Archive/2026': "
            "what messages_list and messages_search take as folder), its delimiter, its path "
            "as segments (['Archive', '2026']: what a rule set's folder and a move_to action "
            "take) and its role (inbox, sent, drafts, trash, junk, archive…, or null). "
            "counts=true adds unread and total (null when the server would not say). Asks the "
            "mail server."
        ),
        group="filters_read",
        also_in=("mail_read", "mail_organize", "mail_folders", "mail_draft", "mail_send"),
        arguments=FoldersArguments,
        hints=MAILBOX_READ,
        run=_folders_list,
    ),
    Tool(
        name="account_run_filters",
        title="Turn Run filters on or off",
        description=(
            "Turn an account's Run filters switch on or off. While it is on, the account's "
            "enabled rule sets act on its mail (move, flag, delete…) on their triggers; while "
            "off, none runs. Confirm with the user first."
        ),
        group="filters_run",
        arguments=RunFiltersArguments,
        hints=Hints(destructive=True, idempotent=True),
        run=_account_run_filters,
    ),
)
