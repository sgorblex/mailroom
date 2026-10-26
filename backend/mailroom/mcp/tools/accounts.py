"""The user's mail accounts as the filter tools need them: which there are, their folders, and
the Run filters switch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Final

from pydantic import Field, StrictBool

from mailroom.accounts import service
from mailroom.mcp.tool import READ, Arguments, Hints, JsonObject, Tool

if TYPE_CHECKING:
    from mailroom.mcp.tool import ToolCall

FOLDERS_LIST_SECONDS: Final = 30.0
"""``folders_list`` still waiting on the mail server after this long is stopped (as a dry run
is), so a slow server cannot hold a worker thread and the user's call slot."""

AccountId = Annotated[
    str, Field(min_length=1, max_length=64, description="An account id, from accounts_list.")
]


class NoArguments(Arguments):
    pass


class AccountArguments(Arguments):
    account_id: AccountId


class RunFiltersArguments(Arguments):
    account_id: AccountId
    on: StrictBool = Field(description="True to turn Run filters on, false to turn it off.")


async def _accounts_list(_args: NoArguments, call: ToolCall) -> JsonObject:
    listed = await call.db(lambda db: service.listed_accounts(call.ctx, db, call.user_id))
    return {
        "accounts": [
            account.model_dump(mode="json", include={"id", "login", "has_password", "run_filters"})
            for account in listed
        ]
    }


async def _folders_list(args: AccountArguments, call: ToolCall) -> JsonObject:
    folders = await call.thread(
        lambda: service.account_folders(
            call.ctx, call.user_id, args.account_id, seconds=FOLDERS_LIST_SECONDS
        )
    )
    return {"account_id": args.account_id, "folders": folders}


async def _account_run_filters(args: RunFiltersArguments, call: ToolCall) -> JsonObject:
    on = await call.db(
        lambda db: service.set_run_filters(
            call.ctx, db, call.user_id, args.account_id, on=args.on, via="mcp"
        )
    )
    return {"account_id": args.account_id, "run_filters": on}


ACCOUNT_TOOLS: Final[tuple[Tool[Any], ...]] = (
    Tool(
        name="accounts_list",
        title="List accounts",
        description=(
            "The user's mail accounts: id, login, whether a password is stored, and whether "
            "Run filters is on (rule sets run only on an account where it is). Call this first."
        ),
        group="filters_read",
        arguments=NoArguments,
        hints=READ,
        run=_accounts_list,
    ),
    Tool(
        name="folders_list",
        title="List folders",
        description=(
            "An account's folders, each as its path segments (['Archive', '2026']): what a rule "
            "set's folder and a move_to action take. Asks the mail server."
        ),
        group="filters_read",
        arguments=AccountArguments,
        hints=READ,
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
